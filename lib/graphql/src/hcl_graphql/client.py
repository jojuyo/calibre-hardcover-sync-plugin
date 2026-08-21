from typing import Optional
from urllib import request, error
import json
import logging
import os
import threading
import time

try:
    import fcntl
except ImportError:
    fcntl = None

logger = logging.getLogger(__name__)


class GraphQLError(RuntimeError):
    """A GraphQL response reported an error (including auth/token failures).

    Hardcover returns a top-level ``errors`` array for query/mutation problems
    and a top-level ``error`` string (e.g. "Unable to verify token") when the
    API token cannot be authenticated. Both used to be silently discarded,
    surfacing as empty results with no explanation.
    """


def _extract_error_message(payload: dict) -> Optional[str]:
    """Return a human-readable error from a GraphQL response, if any."""
    errors = payload.get("errors")
    if errors:
        messages = []
        for err in errors:
            if isinstance(err, dict):
                message = err.get("message")
                if message:
                    messages.append(str(message))
            elif err:
                messages.append(str(err))
        return "; ".join(messages) if messages else "GraphQL request failed"
    # Hardcover uses a top-level "error" string for token/auth failures.
    top_error = payload.get("error")
    if top_error and "data" not in payload:
        return str(top_error)
    return None

# Hardcover documents ~60 req/min. Requests are serialized across Calibre
# processes via a shared lock file under the Calibre config directory, so the
# rate limit applies to this plugin and any other Hardcover plugin using the
# same client (for example the official Hardcover metadata plugin).
DEFAULT_REQUESTS_PER_MINUTE = 30
MAX_429_RETRIES = 5
DEFAULT_429_WAIT_SECONDS = 60

_module_lock = threading.Lock()


def _rate_state_path() -> str:
    base = os.environ.get("CALIBRE_CONFIG_DIRECTORY")
    if not base:
        for candidate in (
            os.path.join(os.path.expanduser("~"), "Library", "Preferences", "calibre"),
            os.path.join(os.path.expanduser("~"), ".config", "calibre"),
        ):
            if os.path.isdir(candidate):
                base = candidate
                break
        else:
            base = os.path.join(
                os.path.expanduser("~"), "Library", "Preferences", "calibre"
            )
    return os.path.join(base, "hardcover_api_rate.ts")


def _rate_config_path() -> str:
    return os.path.join(os.path.dirname(_rate_state_path()), "hardcover_api.json")


def get_configured_requests_per_minute(
    default: int = DEFAULT_REQUESTS_PER_MINUTE,
) -> int:
    try:
        with open(_rate_config_path(), encoding="utf-8") as handle:
            data = json.load(handle)
        return max(1, int(data.get("requests_per_minute", default)))
    except (OSError, ValueError, TypeError):
        return default


def _min_interval_from_rpm(requests_per_minute: int | None) -> float:
    rpm = requests_per_minute or get_configured_requests_per_minute()
    return 60.0 / rpm if rpm else 0.0


def _read_last_request_time(state_path: str) -> float:
    try:
        with open(state_path, encoding="utf-8") as handle:
            return float(handle.read().strip() or 0)
    except (OSError, ValueError):
        return 0.0


def _write_last_request_time(state_path: str) -> None:
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    with open(state_path, "w", encoding="utf-8") as handle:
        handle.write(str(time.time()))


def _with_cross_process_lock(lock_path: str):
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    handle = open(lock_path, "a+", encoding="utf-8")

    class _Lock:
        def __enter__(self):
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            return handle

        def __exit__(self, exc_type, exc, tb):
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    return _Lock()


def _wait_for_rate_slot(min_interval: float) -> None:
    if not min_interval:
        return

    state_path = _rate_state_path()
    lock_path = state_path + ".lock"
    with _with_cross_process_lock(lock_path):
        last = _read_last_request_time(state_path)
        wait = min_interval - (time.time() - last)
        if wait > 0:
            time.sleep(wait)


def _record_request_completed() -> None:
    state_path = _rate_state_path()
    lock_path = state_path + ".lock"
    with _with_cross_process_lock(lock_path):
        _write_last_request_time(state_path)


def _retry_after_seconds(http_error: error.HTTPError) -> float:
    retry_after = http_error.headers.get("Retry-After")
    if retry_after:
        try:
            return max(float(retry_after), 1.0)
        except ValueError:
            pass
    return DEFAULT_429_WAIT_SECONDS


class GraphQLClient:
    def __init__(
        self,
        endpoint: str,
        useragent: Optional[str] = None,
        requests_per_minute: int | None = None,
    ):
        self.endpoint = endpoint
        self.token = None
        self.useragent = useragent
        self._requests_per_minute = requests_per_minute

    def set_token(self, token: str):
        self.token = token

    def _min_interval(self) -> float:
        return _min_interval_from_rpm(self._requests_per_minute)

    def execute(self, query: str, variables: Optional[dict] = None, timeout=30):
        with _module_lock:
            for attempt in range(MAX_429_RETRIES + 1):
                _wait_for_rate_slot(self._min_interval())
                try:
                    return self._execute_once(query, variables, timeout)
                except error.HTTPError as exc:
                    if exc.code == 429 and attempt < MAX_429_RETRIES:
                        wait = _retry_after_seconds(exc)
                        logger.warning(
                            "Hardcover API rate limited (429); waiting %.1fs",
                            wait,
                        )
                        time.sleep(wait)
                        continue
                    logger.exception("GraphQL request failed")
                    raise
            raise RuntimeError("Hardcover API rate limit retries exhausted")

    def _execute_once(self, query: str, variables: Optional[dict] = None, timeout=30):
        data = {"query": query, "variables": variables}
        headers = {"Accept": "application/json", "Content-Type": "application/json"}

        if self.token:
            token = self.token
            if " " not in self.token:
                token = f"Bearer {token}"
            headers["Authorization"] = token
        if self.useragent:
            headers["User-Agent"] = self.useragent

        body = json.dumps(data).encode("utf-8")

        if not self.endpoint.startswith(("http:", "https:")):
            raise ValueError("invalid endpoint")

        req = request.Request(self.endpoint, body, headers)  # noqa: S310

        with request.urlopen(req, timeout=timeout) as res:  # noqa: S310
            payload = res.read()
            json_result = json.loads(payload.decode("utf-8"))
        _record_request_completed()
        if isinstance(json_result, dict):
            message = _extract_error_message(json_result)
            if message:
                raise GraphQLError(message)
            if "data" in json_result:
                return json_result.get("data")
        return json_result
