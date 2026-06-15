from dataclasses import dataclass, field
from typing import Optional

from calibre_plugins.hardcover_list.hcl_graphql.client import GraphQLClient

from ._version import __version__
from .config import get_api_key
from .queries import (
    ALL_LIST_BOOKS,
    BOOK_ID_BY_EDITION,
    BOOK_ID_BY_SLUG,
    BOOKS_BY_EDITIONS,
    CURRENT_USER_ID,
    DELETE_LIST_BOOK,
    INSERT_LIST,
    INSERT_LIST_BOOK,
    LIST_BOOK_ENTRIES,
    LIST_BOOK_ENTRY,
    ALL_USER_JOURNALS,
    ALL_USER_RATINGS,
    ALL_USER_REVIEWS,
    ALL_USER_STATUSES,
    ALL_USER_TAGS,
    JOURNAL_ENTRIES_FOR_BOOKS,
    LIST_MEMBERSHIP_BY_ID,
    TAGGINGS_FOR_BOOKS,
    USER_BOOK_IDS,
    USER_BOOK_READS,
    USER_BOOK_REVIEW_STATE,
    USER_LISTS,
)

# Default visibility for pushed journal entries: 1 == "Public" (matches the
# privacy of typical Hardcover journal activity). Change to 3 for "Private".
JOURNAL_PRIVACY_ID = 1

# Page size for streaming all list_books. The whole library typically fits in
# one or two pages, so the entire membership map costs only a few requests.
LIST_BOOKS_PAGE_SIZE = 1000
EDITION_RESOLVE_CHUNK = 500

# Hardcover user_book_statuses: 3 == "Read". A fresh rating insert marks the
# book as read, since rating a book implies it has been read.
READ_STATUS_ID = 3

# Sentinel finished_at meaning "unknown date": Hardcover renders a read with a
# null finished_at as "?", so we clear the auto-created date instead of using a
# placeholder year. Distinct from None, which means "leave today's date".
UNKNOWN_READ_DATE = "unknown"

# The free-form tag category that maps to Calibre's native tags field. Other
# categories (Genre, Mood, Pace, Content Warning, …) are Hardcover-managed and
# are preserved untouched by tag pushes.
TAG_CATEGORY = "Tag"

API_URL = "https://api.hardcover.app/v1/graphql"
NO_API_KEY = "Configure Hardcover API key"
NO_IDENTIFIER = "No Hardcover identifier"
NOT_ON_LISTS = "Not on any lists"
LOADING_TEXT = "Loading..."
SPECIAL_COLUMN_VALUES = frozenset(
    {NOT_ON_LISTS, NO_IDENTIFIER, NO_API_KEY, LOADING_TEXT}
)


@dataclass
class ListMembershipSnapshot:
    by_id: dict[int, set[str]] = field(default_factory=dict)
    by_slug: dict[str, set[str]] = field(default_factory=dict)

    def lists_text(self, book_id: int | None, slug: str | None) -> str:
        names: set[str] = set()
        if book_id is not None:
            names |= self.by_id.get(book_id, set())
        if slug:
            names |= self.by_slug.get(slug, set())
        if not names:
            return NOT_ON_LISTS
        return ", ".join(sorted(names))


@dataclass
class RatingSnapshot:
    by_id: dict[int, float] = field(default_factory=dict)
    by_slug: dict[str, float] = field(default_factory=dict)

    def rating_for(
        self, book_id: int | None, slug: str | None
    ) -> float | None:
        if book_id is not None and book_id in self.by_id:
            return self.by_id[book_id]
        if slug and slug in self.by_slug:
            return self.by_slug[slug]
        return None


@dataclass
class ReviewSnapshot:
    by_id: dict[int, str] = field(default_factory=dict)
    by_slug: dict[str, str] = field(default_factory=dict)

    def review_for(
        self, book_id: int | None, slug: str | None
    ) -> str | None:
        if book_id is not None and book_id in self.by_id:
            return self.by_id[book_id]
        if slug and slug in self.by_slug:
            return self.by_slug[slug]
        return None


@dataclass
class StatusSnapshot:
    by_id: dict[int, int] = field(default_factory=dict)
    by_slug: dict[str, int] = field(default_factory=dict)

    def status_for(
        self, book_id: int | None, slug: str | None
    ) -> int | None:
        if book_id is not None and book_id in self.by_id:
            return self.by_id[book_id]
        if slug and slug in self.by_slug:
            return self.by_slug[slug]
        return None


@dataclass
class TagSnapshot:
    # book_id/slug -> ordered list of free-form ("Tag") tag names
    by_id: dict[int, list[str]] = field(default_factory=dict)
    by_slug: dict[str, list[str]] = field(default_factory=dict)

    def tags_for(self, book_id: int | None, slug: str | None) -> list[str]:
        if book_id is not None and book_id in self.by_id:
            return self.by_id[book_id]
        if slug and slug in self.by_slug:
            return self.by_slug[slug]
        return []


def journal_entry_page(metadata) -> int | None:
    """Extract a page number from a quote's position metadata, if present."""
    if not isinstance(metadata, dict):
        return None
    position = metadata.get("position")
    if not isinstance(position, dict):
        return None
    if position.get("type") != "pages":
        return None
    value = position.get("value")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


@dataclass
class JournalSnapshot:
    # book_id/slug -> {"note": [entry, ...], "quote": [{"entry", "page"}, ...]}
    by_id: dict[int, dict] = field(default_factory=dict)
    by_slug: dict[str, dict] = field(default_factory=dict)

    def entries_for(
        self, book_id: int | None, slug: str | None, event: str
    ) -> list:
        if book_id is not None and book_id in self.by_id:
            return self.by_id[book_id].get(event, [])
        if slug and slug in self.by_slug:
            return self.by_slug[slug].get(event, [])
        return []


def normalize_lists_display(value) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(part) for part in value if part)
    return str(value)


def lists_text_to_field_value(text: str):
    if not text:
        return text
    if text in SPECIAL_COLUMN_VALUES or text.startswith("Hardcover error:"):
        return text
    parts = [part.strip() for part in text.split(",") if part.strip()]
    if not parts:
        return text
    return parts if len(parts) > 1 else parts[0]


def column_values_equal(current, new) -> bool:
    def as_key(val):
        if val is None or val == "":
            return ()
        if isinstance(val, (list, tuple)):
            items = [str(part) for part in val if part]
        else:
            text = str(val)
            if text in SPECIAL_COLUMN_VALUES or text.startswith("Hardcover error:"):
                return (text,)
            items = [part.strip() for part in text.split(",") if part.strip()]
        return tuple(sorted(items))

    return as_key(current) == as_key(new)


def is_stale_lists_column_value(value) -> bool:
    text = normalize_lists_display(value)
    if not text or text == LOADING_TEXT:
        return True
    if text in {NO_IDENTIFIER, NO_API_KEY}:
        return True
    return text.startswith("Hardcover error:")


def _parse_positive_int(value) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def get_hardcover_edition_id(identifiers: dict) -> int | None:
    edition_id = _parse_positive_int(identifiers.get("hardcover-edition"))
    if edition_id is not None:
        return edition_id
    legacy = identifiers.get("hardcover")
    if legacy is not None and str(legacy).isdigit():
        return int(legacy)
    return None


def get_hardcover_lookup(
    identifiers: dict,
) -> tuple[int | None, str | None, int | None]:
    """Return (book_id, slug, edition_id) usable to match a book against lists."""
    book_id = _parse_positive_int(identifiers.get("hardcover-id"))
    slug = None
    for key in ("hardcover-slug", "hardcover"):
        value = identifiers.get(key)
        if value and not str(value).isdigit():
            slug = str(value)
            break
    edition_id = get_hardcover_edition_id(identifiers)
    return book_id, slug, edition_id


def get_hardcover_book_ref(identifiers: dict) -> tuple[str, str | int] | None:
    book_id = _parse_positive_int(identifiers.get("hardcover-id"))
    if book_id is not None:
        return ("id", book_id)

    for key in ("hardcover-slug", "hardcover"):
        value = identifiers.get(key)
        if value and not str(value).isdigit():
            return ("slug", str(value))

    edition_id = get_hardcover_edition_id(identifiers)
    if edition_id is not None:
        return ("edition", edition_id)
    return None


def has_hardcover_link(identifiers: dict) -> bool:
    return get_hardcover_book_ref(identifiers) is not None


def format_list_names(lists: list[dict]) -> str:
    matching = [entry["name"] for entry in lists if entry.get("list_books")]
    if not matching:
        return NOT_ON_LISTS
    return ", ".join(matching)


def _me_from_result(result: dict | None) -> dict | None:
    if not result:
        return None
    me = result.get("me")
    if isinstance(me, list):
        me = me[0] if me else None
    return me


class HardcoverListsClient:
    def __init__(self, api_key: Optional[str] = None):
        useragent = f"hardcover-list-calibre-plugin/{__version__}"
        self.client = GraphQLClient(API_URL, useragent)
        self.client.set_token(api_key or get_api_key())

    def lists_for_book(self, identifiers: dict, timeout=30) -> tuple[str, int | None]:
        if not self.client.token:
            return NO_API_KEY, None

        book_id = self.resolve_book_id(identifiers, timeout)
        if book_id is None:
            return NO_IDENTIFIER, None

        result = self.client.execute(
            LIST_MEMBERSHIP_BY_ID, {"book_id": book_id}, timeout
        )
        me = _me_from_result(result)
        lists = (me or {}).get("lists") or []
        return format_list_names(lists), book_id

    def fetch_user_lists(self, timeout=30) -> list[dict]:
        if not self.client.token:
            return []

        result = self.client.execute(USER_LISTS, {}, timeout)
        me = _me_from_result(result)
        return (me or {}).get("lists") or []

    def current_user_id(self, timeout=30) -> int | None:
        result = self.client.execute(CURRENT_USER_ID, {}, timeout)
        me = _me_from_result(result)
        user_id = (me or {}).get("id")
        return int(user_id) if user_id is not None else None

    def snapshot_list_memberships(self, timeout=30) -> "ListMembershipSnapshot":
        """Fetch every list_books entry for the user in a few paginated requests.

        Returns a snapshot mapping Hardcover book ids and slugs to the set of
        list names that contain them. This replaces per-book membership probes.
        """
        if not self.client.token:
            raise RuntimeError(NO_API_KEY)

        user_id = self.current_user_id(timeout)
        if user_id is None:
            raise RuntimeError("Could not determine Hardcover user id")

        by_id: dict[int, set[str]] = {}
        by_slug: dict[str, set[str]] = {}
        offset = 0
        while True:
            result = self.client.execute(
                ALL_LIST_BOOKS,
                {
                    "user_id": user_id,
                    "limit": LIST_BOOKS_PAGE_SIZE,
                    "offset": offset,
                },
                timeout,
            )
            rows = (result or {}).get("list_books") or []
            for row in rows:
                name = (row.get("list") or {}).get("name")
                if not name:
                    continue
                book_id = row.get("book_id")
                if book_id is not None:
                    by_id.setdefault(int(book_id), set()).add(name)
                slug = (row.get("book") or {}).get("slug")
                if slug:
                    by_slug.setdefault(slug, set()).add(name)
            if len(rows) < LIST_BOOKS_PAGE_SIZE:
                break
            offset += LIST_BOOKS_PAGE_SIZE

        return ListMembershipSnapshot(by_id=by_id, by_slug=by_slug)

    def snapshot_user_ratings(self, timeout=30) -> "RatingSnapshot":
        """Fetch every rated book for the user in a few paginated requests.

        Returns a snapshot mapping Hardcover book ids and slugs to the user's
        rating, so selected books can be matched locally instead of probing or
        resolving each book one at a time.
        """
        if not self.client.token:
            raise RuntimeError(NO_API_KEY)

        user_id = self.current_user_id(timeout)
        if user_id is None:
            raise RuntimeError("Could not determine Hardcover user id")

        by_id: dict[int, float] = {}
        by_slug: dict[str, float] = {}
        offset = 0
        while True:
            result = self.client.execute(
                ALL_USER_RATINGS,
                {
                    "user_id": user_id,
                    "limit": LIST_BOOKS_PAGE_SIZE,
                    "offset": offset,
                },
                timeout,
            )
            rows = (result or {}).get("user_books") or []
            for row in rows:
                rating = row.get("rating")
                if rating is None:
                    continue
                book_id = row.get("book_id")
                if book_id is not None:
                    by_id[int(book_id)] = float(rating)
                slug = (row.get("book") or {}).get("slug")
                if slug:
                    by_slug[slug] = float(rating)
            if len(rows) < LIST_BOOKS_PAGE_SIZE:
                break
            offset += LIST_BOOKS_PAGE_SIZE

        return RatingSnapshot(by_id=by_id, by_slug=by_slug)

    def user_book_ids(self, book_ids, timeout=30) -> dict[int, int]:
        """Map Hardcover book ids to the user's existing user_book entry id."""
        if not self.client.token:
            raise RuntimeError(NO_API_KEY)
        ids = [int(b) for b in book_ids if b is not None]
        if not ids:
            return {}

        user_id = self.current_user_id(timeout)
        if user_id is None:
            raise RuntimeError("Could not determine Hardcover user id")

        mapping: dict[int, int] = {}
        for start in range(0, len(ids), EDITION_RESOLVE_CHUNK):
            chunk = ids[start : start + EDITION_RESOLVE_CHUNK]
            result = self.client.execute(
                USER_BOOK_IDS,
                {"user_id": user_id, "book_ids": chunk},
                timeout,
            )
            for row in (result or {}).get("user_books") or []:
                book_id = row.get("book_id")
                entry_id = row.get("id")
                if book_id is None or entry_id is None:
                    continue
                book_id = int(book_id)
                if book_id not in mapping:
                    mapping[book_id] = int(entry_id)
        return mapping

    def apply_read_dates(
        self, user_book_ids, finished_at: str, timeout=30, chunk_size: int = 40
    ) -> None:
        """Retarget the auto-created "finished" read date for given user_books.

        Marking a book as Read auto-creates a user_book_reads row dated today;
        this rewrites that row's finished_at to ``finished_at`` (e.g. a specific
        date the book was actually read). Passing :data:`UNKNOWN_READ_DATE`
        clears the date entirely, which Hardcover renders as "?".
        """
        ids = [int(i) for i in user_book_ids if i]
        if not ids or not finished_at:
            return

        clear = finished_at == UNKNOWN_READ_DATE

        read_ids: list[int] = []
        for start in range(0, len(ids), EDITION_RESOLVE_CHUNK):
            chunk = ids[start : start + EDITION_RESOLVE_CHUNK]
            result = self.client.execute(USER_BOOK_READS, {"ids": chunk}, timeout)
            for row in (result or {}).get("user_book_reads") or []:
                if row.get("id") is not None:
                    read_ids.append(int(row["id"]))

        for start in range(0, len(read_ids), chunk_size):
            chunk = read_ids[start : start + chunk_size]
            var_defs = [] if clear else ["$d: date"]
            variables: dict = {} if clear else {"d": finished_at}
            value = "null" if clear else "$d"
            fields: list[str] = []
            for offset, rid in enumerate(chunk):
                idx = start + offset
                var_defs.append(f"$id_{idx}: Int!")
                variables[f"id_{idx}"] = rid
                fields.append(
                    f"  u{idx}: update_user_book_read(id: $id_{idx}, "
                    f"object: {{id: $id_{idx}, action: \"finished\", "
                    f"finished_at: {value}}}) {{ id error }}"
                )
            query = (
                "mutation HardcoverBatchSetReadDates("
                + ", ".join(var_defs)
                + ") {\n"
                + "\n".join(fields)
                + "\n}"
            )
            try:
                self.client.execute(query, variables, timeout)
            except Exception:  # noqa: BLE001, S110 - best-effort date adjustment
                pass

    def push_ratings(
        self, items: list[dict], timeout=30, chunk_size: int = 50,
        read_finished_at: str | None = None,
    ) -> list[dict]:
        """Set Hardcover ratings for many books via batched aliased mutations.

        Each item is a dict with ``book_id``, ``rating`` (0-5), an optional
        ``user_book_id`` (update when present, otherwise insert), and a ``_book``
        payload echoed back. Returns a list (same order) of dicts:
        ``{"book": <_book>, "id": int | None, "error": str | None}``.

        ``read_finished_at`` (when set) retargets the read date of any newly
        inserted (Read) entries.
        """
        results: list[dict] = []
        for start in range(0, len(items), chunk_size):
            chunk = items[start : start + chunk_size]
            var_defs: list[str] = []
            fields: list[str] = []
            variables: dict = {}
            for offset, item in enumerate(chunk):
                idx = start + offset
                var_defs.append(f"$rating_{idx}: numeric")
                variables[f"rating_{idx}"] = item["rating"]
                if item.get("user_book_id"):
                    var_defs.append(f"$id_{idx}: Int!")
                    variables[f"id_{idx}"] = item["user_book_id"]
                    fields.append(
                        f"  r{idx}: update_user_book(id: $id_{idx}, "
                        f"object: {{rating: $rating_{idx}}}) {{ id error }}"
                    )
                else:
                    var_defs.append(f"$book_{idx}: Int!")
                    variables[f"book_{idx}"] = item["book_id"]
                    fields.append(
                        f"  r{idx}: insert_user_book("
                        f"object: {{book_id: $book_{idx}, rating: $rating_{idx}, "
                        f"status_id: {READ_STATUS_ID}}}) "
                        f"{{ id error }}"
                    )
            query = (
                "mutation HardcoverBatchPushRatings("
                + ", ".join(var_defs)
                + ") {\n"
                + "\n".join(fields)
                + "\n}"
            )

            try:
                data = self.client.execute(query, variables, timeout) or {}
                request_error = None
            except Exception as exc:  # noqa: BLE001 - reported per book below
                data = {}
                request_error = str(exc)

            for offset, item in enumerate(chunk):
                idx = start + offset
                entry = data.get(f"r{idx}") if request_error is None else None
                entry = entry or {}
                entry_id = entry.get("id")
                error = entry.get("error")
                if request_error is not None:
                    error = request_error
                elif entry_id is None and error is None:
                    error = "Hardcover did not save the rating"
                results.append(
                    {"book": item["_book"], "id": entry_id, "error": error}
                )

        if read_finished_at:
            inserted = [
                result["id"]
                for result, item in zip(results, items)
                if item.get("user_book_id") is None
                and result["id"] is not None
                and result["error"] is None
            ]
            self.apply_read_dates(inserted, read_finished_at, timeout)
        return results

    def snapshot_user_reviews(self, timeout=30) -> "ReviewSnapshot":
        """Fetch every reviewed book for the user in a few paginated requests.

        Returns a snapshot mapping Hardcover book ids and slugs to the user's
        plain-text review, mirroring ``snapshot_user_ratings``.
        """
        if not self.client.token:
            raise RuntimeError(NO_API_KEY)

        user_id = self.current_user_id(timeout)
        if user_id is None:
            raise RuntimeError("Could not determine Hardcover user id")

        by_id: dict[int, str] = {}
        by_slug: dict[str, str] = {}
        offset = 0
        while True:
            result = self.client.execute(
                ALL_USER_REVIEWS,
                {
                    "user_id": user_id,
                    "limit": LIST_BOOKS_PAGE_SIZE,
                    "offset": offset,
                },
                timeout,
            )
            rows = (result or {}).get("user_books") or []
            for row in rows:
                review = row.get("review")
                if not review:
                    continue
                book_id = row.get("book_id")
                if book_id is not None:
                    by_id[int(book_id)] = review
                slug = (row.get("book") or {}).get("slug")
                if slug:
                    by_slug[slug] = review
            if len(rows) < LIST_BOOKS_PAGE_SIZE:
                break
            offset += LIST_BOOKS_PAGE_SIZE

        return ReviewSnapshot(by_id=by_id, by_slug=by_slug)

    def review_states(self, book_ids, timeout=30) -> dict[int, dict]:
        """Map book ids to the user's existing user_book id and reviewed_at."""
        if not self.client.token:
            raise RuntimeError(NO_API_KEY)
        ids = [int(b) for b in book_ids if b is not None]
        if not ids:
            return {}

        user_id = self.current_user_id(timeout)
        if user_id is None:
            raise RuntimeError("Could not determine Hardcover user id")

        states: dict[int, dict] = {}
        for start in range(0, len(ids), EDITION_RESOLVE_CHUNK):
            chunk = ids[start : start + EDITION_RESOLVE_CHUNK]
            result = self.client.execute(
                USER_BOOK_REVIEW_STATE,
                {"user_id": user_id, "book_ids": chunk},
                timeout,
            )
            for row in (result or {}).get("user_books") or []:
                book_id = row.get("book_id")
                entry_id = row.get("id")
                if book_id is None or entry_id is None:
                    continue
                book_id = int(book_id)
                if book_id not in states:
                    states[book_id] = {
                        "id": int(entry_id),
                        "reviewed_at": row.get("reviewed_at"),
                    }
        return states

    def push_reviews(
        self, items: list[dict], timeout=30, chunk_size: int = 50,
        read_finished_at: str | None = None,
    ) -> list[dict]:
        """Set Hardcover reviews for many books via batched aliased mutations.

        Each item is a dict with ``book_id``, ``review_slate`` (a Slate document
        dict), an optional ``user_book_id`` (update when present, otherwise
        insert), ``set_reviewed_at`` (stamp reviewed_at when True), and a
        ``_book`` payload echoed back. Returns a list (same order) of dicts:
        ``{"book": <_book>, "id": int | None, "error": str | None}``.

        ``read_finished_at`` (when set) retargets the read date of any newly
        inserted (Read) entries.
        """
        from datetime import date

        reviewed_at = date.today().isoformat()
        results: list[dict] = []
        for start in range(0, len(items), chunk_size):
            chunk = items[start : start + chunk_size]
            var_defs: list[str] = []
            fields: list[str] = []
            variables: dict = {}
            for offset, item in enumerate(chunk):
                idx = start + offset
                var_defs.append(f"$slate_{idx}: jsonb")
                variables[f"slate_{idx}"] = item["review_slate"]
                stamp = ""
                if item.get("set_reviewed_at"):
                    var_defs.append(f"$revat_{idx}: timestamp")
                    variables[f"revat_{idx}"] = reviewed_at
                    stamp = f", reviewed_at: $revat_{idx}"
                if item.get("user_book_id"):
                    var_defs.append(f"$id_{idx}: Int!")
                    variables[f"id_{idx}"] = item["user_book_id"]
                    fields.append(
                        f"  r{idx}: update_user_book(id: $id_{idx}, "
                        f"object: {{review_slate: $slate_{idx}{stamp}}}) "
                        f"{{ id error }}"
                    )
                else:
                    var_defs.append(f"$book_{idx}: Int!")
                    variables[f"book_{idx}"] = item["book_id"]
                    fields.append(
                        f"  r{idx}: insert_user_book("
                        f"object: {{book_id: $book_{idx}, "
                        f"review_slate: $slate_{idx}, "
                        f"status_id: {READ_STATUS_ID}{stamp}}}) "
                        f"{{ id error }}"
                    )
            query = (
                "mutation HardcoverBatchPushReviews("
                + ", ".join(var_defs)
                + ") {\n"
                + "\n".join(fields)
                + "\n}"
            )

            try:
                data = self.client.execute(query, variables, timeout) or {}
                request_error = None
            except Exception as exc:  # noqa: BLE001 - reported per book below
                data = {}
                request_error = str(exc)

            for offset, item in enumerate(chunk):
                idx = start + offset
                entry = data.get(f"r{idx}") if request_error is None else None
                entry = entry or {}
                entry_id = entry.get("id")
                error = entry.get("error")
                if request_error is not None:
                    error = request_error
                elif entry_id is None and error is None:
                    error = "Hardcover did not save the review"
                results.append(
                    {"book": item["_book"], "id": entry_id, "error": error}
                )

        if read_finished_at:
            inserted = [
                result["id"]
                for result, item in zip(results, items)
                if item.get("user_book_id") is None
                and result["id"] is not None
                and result["error"] is None
            ]
            self.apply_read_dates(inserted, read_finished_at, timeout)
        return results

    def snapshot_user_statuses(self, timeout=30) -> "StatusSnapshot":
        """Fetch every shelved book's status for the user in a few requests.

        Returns a snapshot mapping Hardcover book ids and slugs to the user's
        status_id, mirroring ``snapshot_user_ratings``.
        """
        if not self.client.token:
            raise RuntimeError(NO_API_KEY)

        user_id = self.current_user_id(timeout)
        if user_id is None:
            raise RuntimeError("Could not determine Hardcover user id")

        by_id: dict[int, int] = {}
        by_slug: dict[str, int] = {}
        offset = 0
        while True:
            result = self.client.execute(
                ALL_USER_STATUSES,
                {
                    "user_id": user_id,
                    "limit": LIST_BOOKS_PAGE_SIZE,
                    "offset": offset,
                },
                timeout,
            )
            rows = (result or {}).get("user_books") or []
            for row in rows:
                status_id = row.get("status_id")
                if status_id is None:
                    continue
                book_id = row.get("book_id")
                if book_id is not None:
                    by_id[int(book_id)] = int(status_id)
                slug = (row.get("book") or {}).get("slug")
                if slug:
                    by_slug[slug] = int(status_id)
            if len(rows) < LIST_BOOKS_PAGE_SIZE:
                break
            offset += LIST_BOOKS_PAGE_SIZE

        return StatusSnapshot(by_id=by_id, by_slug=by_slug)

    def push_statuses(
        self, items: list[dict], timeout=30, chunk_size: int = 50,
        read_finished_at: str | None = None,
    ) -> list[dict]:
        """Set Hardcover reading statuses for many books via batched mutations.

        Each item is a dict with ``book_id``, ``status_id``, an optional
        ``user_book_id`` (update when present, otherwise insert), and a ``_book``
        payload echoed back. Returns a list (same order) of dicts:
        ``{"book": <_book>, "id": int | None, "error": str | None}``.

        ``read_finished_at`` (when set) retargets the read date of entries newly
        inserted with the Read status.
        """
        results: list[dict] = []
        for start in range(0, len(items), chunk_size):
            chunk = items[start : start + chunk_size]
            var_defs: list[str] = []
            fields: list[str] = []
            variables: dict = {}
            for offset, item in enumerate(chunk):
                idx = start + offset
                var_defs.append(f"$status_{idx}: Int!")
                variables[f"status_{idx}"] = item["status_id"]
                if item.get("user_book_id"):
                    var_defs.append(f"$id_{idx}: Int!")
                    variables[f"id_{idx}"] = item["user_book_id"]
                    fields.append(
                        f"  r{idx}: update_user_book(id: $id_{idx}, "
                        f"object: {{status_id: $status_{idx}}}) {{ id error }}"
                    )
                else:
                    var_defs.append(f"$book_{idx}: Int!")
                    variables[f"book_{idx}"] = item["book_id"]
                    fields.append(
                        f"  r{idx}: insert_user_book("
                        f"object: {{book_id: $book_{idx}, "
                        f"status_id: $status_{idx}}}) {{ id error }}"
                    )
            query = (
                "mutation HardcoverBatchPushStatuses("
                + ", ".join(var_defs)
                + ") {\n"
                + "\n".join(fields)
                + "\n}"
            )

            try:
                data = self.client.execute(query, variables, timeout) or {}
                request_error = None
            except Exception as exc:  # noqa: BLE001 - reported per book below
                data = {}
                request_error = str(exc)

            for offset, item in enumerate(chunk):
                idx = start + offset
                entry = data.get(f"r{idx}") if request_error is None else None
                entry = entry or {}
                entry_id = entry.get("id")
                error = entry.get("error")
                if request_error is not None:
                    error = request_error
                elif entry_id is None and error is None:
                    error = "Hardcover did not save the status"
                results.append(
                    {"book": item["_book"], "id": entry_id, "error": error}
                )

        if read_finished_at:
            inserted = [
                result["id"]
                for result, item in zip(results, items)
                if item.get("user_book_id") is None
                and item.get("status_id") == READ_STATUS_ID
                and result["id"] is not None
                and result["error"] is None
            ]
            self.apply_read_dates(inserted, read_finished_at, timeout)
        return results

    def snapshot_user_journals(self, timeout=30) -> "JournalSnapshot":
        """Fetch all note/quote journal entries for the user, paginated.

        Returns a snapshot mapping book ids and slugs to ordered note strings
        and quote dicts ({"entry", "page"}).
        """
        if not self.client.token:
            raise RuntimeError(NO_API_KEY)

        user_id = self.current_user_id(timeout)
        if user_id is None:
            raise RuntimeError("Could not determine Hardcover user id")

        by_id: dict[int, dict] = {}
        by_slug: dict[str, dict] = {}
        offset = 0
        while True:
            result = self.client.execute(
                ALL_USER_JOURNALS,
                {
                    "user_id": user_id,
                    "limit": LIST_BOOKS_PAGE_SIZE,
                    "offset": offset,
                },
                timeout,
            )
            rows = (result or {}).get("reading_journals") or []
            for row in rows:
                event = row.get("event")
                entry = row.get("entry")
                if event not in ("note", "quote") or not entry:
                    continue
                if event == "quote":
                    value = {
                        "entry": entry,
                        "page": journal_entry_page(row.get("metadata")),
                    }
                else:
                    value = entry
                book_id = row.get("book_id")
                if book_id is not None:
                    bucket = by_id.setdefault(
                        int(book_id), {"note": [], "quote": []}
                    )
                    bucket[event].append(value)
                slug = (row.get("book") or {}).get("slug")
                if slug:
                    bucket = by_slug.setdefault(slug, {"note": [], "quote": []})
                    bucket[event].append(value)
            if len(rows) < LIST_BOOKS_PAGE_SIZE:
                break
            offset += LIST_BOOKS_PAGE_SIZE

        return JournalSnapshot(by_id=by_id, by_slug=by_slug)

    def _journal_entries_for_books(
        self, book_ids, event, timeout=30
    ) -> dict[int, list[dict]]:
        """Map book id -> existing entries [{id, entry, page}] for one event."""
        ids = [int(b) for b in book_ids if b is not None]
        if not ids:
            return {}
        user_id = self.current_user_id(timeout)
        if user_id is None:
            raise RuntimeError("Could not determine Hardcover user id")

        entries: dict[int, list[dict]] = {}
        for start in range(0, len(ids), EDITION_RESOLVE_CHUNK):
            chunk = ids[start : start + EDITION_RESOLVE_CHUNK]
            result = self.client.execute(
                JOURNAL_ENTRIES_FOR_BOOKS,
                {"user_id": user_id, "book_ids": chunk},
                timeout,
            )
            for row in (result or {}).get("reading_journals") or []:
                if row.get("event") != event:
                    continue
                book_id = row.get("book_id")
                if book_id is None or row.get("id") is None:
                    continue
                entries.setdefault(int(book_id), []).append(
                    {
                        "id": int(row["id"]),
                        "entry": row.get("entry") or "",
                        "page": journal_entry_page(row.get("metadata")),
                    }
                )
        return entries

    @staticmethod
    def _journal_key(event: str, entry: str, page) -> tuple:
        normalized = " ".join((entry or "").split())
        if event == "quote":
            return (page, normalized)
        return (normalized,)

    def _insert_journals(
        self, rows, timeout=30, chunk_size: int = 40
    ) -> list[dict]:
        """Insert many reading_journal entries via batched aliased mutations.

        ``rows`` is a list of dicts with ``book_id``, ``event``, ``entry`` and
        an optional ``page``. Returns a list (same order) of error strings or
        None per row.
        """
        results: list[dict] = []
        for start in range(0, len(rows), chunk_size):
            chunk = rows[start : start + chunk_size]
            var_defs: list[str] = []
            fields: list[str] = []
            variables: dict = {}
            for offset, row in enumerate(chunk):
                idx = start + offset
                var_defs.append(f"$book_{idx}: Int!")
                var_defs.append(f"$event_{idx}: String!")
                var_defs.append(f"$entry_{idx}: String!")
                var_defs.append(f"$meta_{idx}: jsonb")
                var_defs.append(f"$priv_{idx}: Int!")
                variables[f"book_{idx}"] = row["book_id"]
                variables[f"event_{idx}"] = row["event"]
                variables[f"entry_{idx}"] = row["entry"]
                page = row.get("page")
                if page is not None:
                    variables[f"meta_{idx}"] = {
                        "position": {"type": "pages", "value": int(page)}
                    }
                else:
                    variables[f"meta_{idx}"] = {}
                variables[f"priv_{idx}"] = JOURNAL_PRIVACY_ID
                fields.append(
                    f"  e{idx}: insert_reading_journal(object: {{"
                    f"book_id: $book_{idx}, event: $event_{idx}, "
                    f"entry: $entry_{idx}, metadata: $meta_{idx}, "
                    f"privacy_setting_id: $priv_{idx}, tags: []}}) "
                    f"{{ id errors }}"
                )
            query = (
                "mutation HardcoverBatchInsertJournals("
                + ", ".join(var_defs)
                + ") {\n"
                + "\n".join(fields)
                + "\n}"
            )
            try:
                data = self.client.execute(query, variables, timeout) or {}
                request_error = None
            except Exception as exc:  # noqa: BLE001 - reported per row below
                data = {}
                request_error = str(exc)
            for offset, _row in enumerate(chunk):
                idx = start + offset
                entry = data.get(f"e{idx}") if request_error is None else None
                entry = entry or {}
                if request_error is not None:
                    results.append(request_error)
                elif entry.get("errors"):
                    results.append(str(entry["errors"]))
                elif entry.get("id") is None:
                    results.append("Hardcover did not save the entry")
                else:
                    results.append(None)
        return results

    def _delete_journals(
        self, ids, timeout=30, chunk_size: int = 40
    ) -> dict[int, str | None]:
        """Delete many reading_journal entries; returns id -> error (None ok)."""
        results: dict[int, str | None] = {}
        ids = [int(i) for i in ids]
        for start in range(0, len(ids), chunk_size):
            chunk = ids[start : start + chunk_size]
            var_defs: list[str] = []
            fields: list[str] = []
            variables: dict = {}
            for offset, jid in enumerate(chunk):
                idx = start + offset
                var_defs.append(f"$id_{idx}: Int!")
                variables[f"id_{idx}"] = jid
                fields.append(
                    f"  d{idx}: delete_reading_journal(id: $id_{idx}) {{ id }}"
                )
            query = (
                "mutation HardcoverBatchDeleteJournals("
                + ", ".join(var_defs)
                + ") {\n"
                + "\n".join(fields)
                + "\n}"
            )
            try:
                data = self.client.execute(query, variables, timeout) or {}
                request_error = None
            except Exception as exc:  # noqa: BLE001 - reported per entry below
                data = {}
                request_error = str(exc)
            for offset, jid in enumerate(chunk):
                idx = start + offset
                entry = data.get(f"d{idx}") if request_error is None else None
                if entry and entry.get("id") is not None:
                    results[jid] = None
                elif request_error is not None:
                    results[jid] = request_error
                else:
                    results[jid] = "Hardcover did not delete the entry"
        return results

    def push_journals(
        self, event: str, items: list[dict], timeout=30
    ) -> list[dict]:
        """Reconcile each book's Notes/Quotes column with Hardcover.

        ``items`` is a list of dicts with ``book_id``, ``desired`` (a list of
        ``(entry_text, page)`` tuples; page is None for notes) and a ``_book``
        payload. For each book, entries new to Hardcover are inserted and
        entries removed from the column are deleted. Returns a list (same order)
        of dicts: ``{"book", "inserted", "deleted", "error"}``.
        """
        book_ids = {item["book_id"] for item in items}
        existing = self._journal_entries_for_books(book_ids, event, timeout)

        insert_rows: list[dict] = []
        insert_owner: list[int] = []  # index into items for each insert row
        delete_ids: list[int] = []
        delete_owner: dict[int, int] = {}  # journal id -> item index
        per_item = [
            {"book": item["_book"], "inserted": 0, "deleted": 0, "error": None}
            for item in items
        ]

        for index, item in enumerate(items):
            desired = item["desired"]
            book_existing = existing.get(item["book_id"], [])
            existing_keys = {}
            for row in book_existing:
                key = self._journal_key(event, row["entry"], row.get("page"))
                existing_keys.setdefault(key, row["id"])
            desired_keys = set()
            for text, page in desired:
                key = self._journal_key(event, text, page)
                desired_keys.add(key)
                if key not in existing_keys:
                    insert_rows.append(
                        {
                            "book_id": item["book_id"],
                            "event": event,
                            "entry": text,
                            "page": page,
                        }
                    )
                    insert_owner.append(index)
            for key, jid in existing_keys.items():
                if key not in desired_keys:
                    delete_ids.append(jid)
                    delete_owner[jid] = index

        insert_results = self._insert_journals(insert_rows, timeout)
        delete_results = self._delete_journals(delete_ids, timeout)

        for row_index, error in enumerate(insert_results):
            owner = insert_owner[row_index]
            if error is None:
                per_item[owner]["inserted"] += 1
            elif per_item[owner]["error"] is None:
                per_item[owner]["error"] = error

        for jid, error in delete_results.items():
            owner = delete_owner[jid]
            if error is None:
                per_item[owner]["deleted"] += 1
            elif per_item[owner]["error"] is None:
                per_item[owner]["error"] = error

        return per_item

    def snapshot_user_tags(self, timeout=30) -> "TagSnapshot":
        """Fetch every free-form ("Tag") tagging for the user, paginated.

        Returns a snapshot mapping Hardcover book ids and slugs to the ordered
        list of the user's free-form tag names, so selected books can be matched
        locally instead of resolving each book one at a time.
        """
        if not self.client.token:
            raise RuntimeError(NO_API_KEY)

        user_id = self.current_user_id(timeout)
        if user_id is None:
            raise RuntimeError("Could not determine Hardcover user id")

        by_id: dict[int, list[str]] = {}
        by_slug: dict[str, list[str]] = {}
        offset = 0
        while True:
            result = self.client.execute(
                ALL_USER_TAGS,
                {
                    "user_id": user_id,
                    "limit": LIST_BOOKS_PAGE_SIZE,
                    "offset": offset,
                },
                timeout,
            )
            rows = (result or {}).get("taggings") or []
            for row in rows:
                name = (row.get("tag") or {}).get("tag")
                if not name:
                    continue
                book_id = row.get("taggable_id")
                if book_id is not None:
                    by_id.setdefault(int(book_id), []).append(name)
                slug = (row.get("book") or {}).get("slug")
                if slug:
                    by_slug.setdefault(slug, []).append(name)
            if len(rows) < LIST_BOOKS_PAGE_SIZE:
                break
            offset += LIST_BOOKS_PAGE_SIZE

        return TagSnapshot(by_id=by_id, by_slug=by_slug)

    def _taggings_for_books(
        self, book_ids, timeout=30
    ) -> dict[int, list[dict]]:
        """Map book id -> existing taggings [{tag, category, spoiler}] (all cats)."""
        ids = [int(b) for b in book_ids if b is not None]
        if not ids:
            return {}
        user_id = self.current_user_id(timeout)
        if user_id is None:
            raise RuntimeError("Could not determine Hardcover user id")

        out: dict[int, list[dict]] = {}
        for start in range(0, len(ids), EDITION_RESOLVE_CHUNK):
            chunk = ids[start : start + EDITION_RESOLVE_CHUNK]
            result = self.client.execute(
                TAGGINGS_FOR_BOOKS,
                {"user_id": user_id, "book_ids": chunk},
                timeout,
            )
            for row in (result or {}).get("taggings") or []:
                book_id = row.get("taggable_id")
                tag = row.get("tag") or {}
                name = tag.get("tag")
                category = (tag.get("tag_category") or {}).get("category")
                if book_id is None or not name or not category:
                    continue
                out.setdefault(int(book_id), []).append(
                    {
                        "tag": name,
                        "category": category,
                        "spoiler": bool(row.get("spoiler")),
                    }
                )
        return out

    def push_tags(
        self, items: list[dict], timeout=30, chunk_size: int = 25
    ) -> list[dict]:
        """Sync each book's free-form tags to Hardcover via upsert_tags.

        ``items`` is a list of dicts with ``book_id`` (Hardcover id), ``tags``
        (a list of Calibre tag strings) and a ``_book`` payload. Because
        upsert_tags replaces a book's entire tag set, existing structured
        categories (Genre, Mood, …) are fetched and preserved; only the "Tag"
        category is replaced with the supplied Calibre tags. Returns a list
        (same order) of dicts: ``{"book", "error"}``.
        """
        if not self.client.token:
            raise RuntimeError(NO_API_KEY)
        if not items:
            return []

        existing = self._taggings_for_books(
            {item["book_id"] for item in items}, timeout
        )

        # Build the merged tag list (preserved non-"Tag" + new "Tag") per book.
        payloads: list[dict] = []
        for item in items:
            book_id = item["book_id"]
            preserved = [
                {
                    "tag": row["tag"],
                    "category": row["category"],
                    "spoiler": row["spoiler"],
                }
                for row in existing.get(book_id, [])
                if row["category"] != TAG_CATEGORY
            ]
            seen: set[str] = set()
            tag_objs: list[dict] = []
            for name in item.get("tags") or []:
                name = (name or "").strip()
                if not name or name.casefold() in seen:
                    continue
                seen.add(name.casefold())
                tag_objs.append(
                    {"tag": name, "category": TAG_CATEGORY, "spoiler": False}
                )
            payloads.append(
                {
                    "book_id": book_id,
                    "tags": preserved + tag_objs,
                    "_book": item.get("_book", {}),
                }
            )

        results: list[dict] = []
        for start in range(0, len(payloads), chunk_size):
            chunk = payloads[start : start + chunk_size]
            var_defs: list[str] = []
            fields: list[str] = []
            variables: dict = {}
            for offset, row in enumerate(chunk):
                idx = start + offset
                var_defs.append(f"$id_{idx}: bigint!")
                var_defs.append(f"$tags_{idx}: [BasicTag]!")
                variables[f"id_{idx}"] = row["book_id"]
                variables[f"tags_{idx}"] = row["tags"]
                fields.append(
                    f'  u{idx}: upsert_tags(id: $id_{idx}, type: "Book", '
                    f"tags: $tags_{idx}) {{ tags {{ tag }} }}"
                )
            query = (
                "mutation HardcoverBatchUpsertTags("
                + ", ".join(var_defs)
                + ") {\n"
                + "\n".join(fields)
                + "\n}"
            )
            try:
                data = self.client.execute(query, variables, timeout) or {}
                request_error = None
            except Exception as exc:  # noqa: BLE001 - reported per book below
                data = {}
                request_error = str(exc)
            if request_error is None and isinstance(data, dict) and data.get(
                "errors"
            ):
                request_error = "; ".join(
                    e.get("message", "") for e in data["errors"]
                )
            for offset, row in enumerate(chunk):
                idx = start + offset
                entry = data.get(f"u{idx}") if request_error is None else None
                if request_error is not None:
                    results.append({"book": row["_book"], "error": request_error})
                elif entry is None:
                    results.append(
                        {"book": row["_book"], "error": "Hardcover did not save tags"}
                    )
                else:
                    results.append({"book": row["_book"], "error": None})
        return results

    def resolve_editions(
        self, edition_ids, timeout=30
    ) -> dict[int, tuple[int, str | None]]:
        """Resolve edition ids to (book_id, slug) in batched requests."""
        resolved: dict[int, tuple[int, str | None]] = {}
        ids = [int(eid) for eid in edition_ids if eid is not None]
        for start in range(0, len(ids), EDITION_RESOLVE_CHUNK):
            chunk = ids[start : start + EDITION_RESOLVE_CHUNK]
            result = self.client.execute(BOOKS_BY_EDITIONS, {"ids": chunk}, timeout)
            for entry in (result or {}).get("editions") or []:
                book = entry.get("book") or {}
                book_id = book.get("id")
                if entry.get("id") is not None and book_id is not None:
                    resolved[int(entry["id"])] = (int(book_id), book.get("slug"))
        return resolved

    def resolve_book_id(self, identifiers: dict, timeout=30) -> int | None:
        book_id = _parse_positive_int(identifiers.get("hardcover-id"))
        if book_id is not None:
            return book_id

        ref = get_hardcover_book_ref(identifiers)
        if ref is None:
            return None
        ref_type, ref_value = ref
        if ref_type == "id":
            return ref_value
        if ref_type == "slug":
            result = self.client.execute(
                BOOK_ID_BY_SLUG, {"slug": ref_value}, timeout
            )
            books = (result or {}).get("books") or []
            if not books:
                return None
            return books[0]["id"]

        result = self.client.execute(
            BOOK_ID_BY_EDITION, {"edition_id": ref_value}, timeout
        )
        editions = (result or {}).get("editions") or []
        if not editions:
            return None
        book = editions[0].get("book") or {}
        return book.get("id")

    def find_list_book_id(
        self, list_id: int, book_id: int, timeout=30
    ) -> int | None:
        result = self.client.execute(
            LIST_BOOK_ENTRY,
            {"list_id": list_id, "book_id": book_id},
            timeout,
        )
        entries = (result or {}).get("list_books") or []
        if not entries:
            return None
        return entries[0]["id"]

    def add_book_to_list(
        self,
        book_id: int,
        list_id: int,
        edition_id: int | None = None,
        timeout=30,
    ) -> int:
        variables = {"list_id": list_id, "book_id": book_id, "edition_id": edition_id}
        result = self.client.execute(INSERT_LIST_BOOK, variables, timeout)
        entry = (result or {}).get("insert_list_book") or {}
        list_book_id = entry.get("id")
        if list_book_id is None:
            raise RuntimeError("Hardcover did not add the book to the list")
        return list_book_id

    def add_books_to_list(
        self,
        list_id: int,
        books: list[dict],
        timeout=30,
        chunk_size: int = 50,
    ) -> list[dict]:
        """Add many books to a list using batched GraphQL requests.

        Hardcover has no native bulk-insert mutation, but a single GraphQL
        request can contain many aliased ``insert_list_book`` fields, which the
        server executes serially. This collapses N inserts into a handful of
        HTTP requests (one per chunk) instead of one request per book.

        ``books`` is a list of dicts with at least ``book_id`` and an optional
        ``edition_id``. Returns a list (same order as input) of dicts:
        ``{"book": <input dict>, "list_book_id": int | None, "error": str | None}``.
        """
        results: list[dict] = []
        for start in range(0, len(books), chunk_size):
            chunk = books[start : start + chunk_size]
            var_defs: list[str] = []
            fields: list[str] = []
            variables: dict = {}
            for offset, book in enumerate(chunk):
                idx = start + offset
                var_defs.append(f"$list_{idx}: Int!")
                var_defs.append(f"$book_{idx}: Int!")
                var_defs.append(f"$edition_{idx}: Int")
                variables[f"list_{idx}"] = list_id
                variables[f"book_{idx}"] = book["book_id"]
                variables[f"edition_{idx}"] = book.get("edition_id")
                fields.append(
                    f"  b{idx}: insert_list_book("
                    f"object: {{list_id: $list_{idx}, book_id: $book_{idx}, "
                    f"edition_id: $edition_{idx}}}) {{ id }}"
                )
            query = (
                "mutation HardcoverBatchAddListBooks("
                + ", ".join(var_defs)
                + ") {\n"
                + "\n".join(fields)
                + "\n}"
            )

            try:
                data = self.client.execute(query, variables, timeout) or {}
                request_error = None
            except Exception as exc:  # noqa: BLE001 - reported per book below
                data = {}
                request_error = str(exc)

            for offset, book in enumerate(chunk):
                idx = start + offset
                entry = data.get(f"b{idx}") if request_error is None else None
                list_book_id = (entry or {}).get("id") if entry else None
                if list_book_id is not None:
                    book_error = None
                elif request_error is not None:
                    book_error = request_error
                else:
                    book_error = "Hardcover did not add the book to the list"
                results.append(
                    {
                        "book": book,
                        "list_book_id": list_book_id,
                        "error": book_error,
                    }
                )
        return results

    def remove_book_from_list(self, list_book_id: int, timeout=30) -> None:
        result = self.client.execute(
            DELETE_LIST_BOOK, {"id": list_book_id}, timeout
        )
        entry = (result or {}).get("delete_list_book") or {}
        if entry.get("id") is None:
            raise RuntimeError("Hardcover did not remove the book from the list")

    def _list_book_entries(
        self, list_id: int, book_ids: list[int], timeout=30
    ) -> dict[int, list[int]]:
        """Map each book_id to its list_book entry ids for the given list."""
        entries: dict[int, list[int]] = {}
        if not book_ids:
            return entries
        offset = 0
        while True:
            data = self.client.execute(
                LIST_BOOK_ENTRIES,
                {
                    "list_id": list_id,
                    "book_ids": book_ids,
                    "limit": LIST_BOOKS_PAGE_SIZE,
                    "offset": offset,
                },
                timeout,
            ) or {}
            rows = data.get("list_books") or []
            for row in rows:
                entries.setdefault(row["book_id"], []).append(row["id"])
            if len(rows) < LIST_BOOKS_PAGE_SIZE:
                break
            offset += LIST_BOOKS_PAGE_SIZE
        return entries

    def _delete_list_books(
        self, list_book_ids: list[int], timeout=30, chunk_size: int = 50
    ) -> dict[int, str | None]:
        """Delete many list_book entries via batched aliased mutations.

        Returns a map of list_book_id -> error string (None on success).
        """
        results: dict[int, str | None] = {}
        for start in range(0, len(list_book_ids), chunk_size):
            chunk = list_book_ids[start : start + chunk_size]
            var_defs: list[str] = []
            fields: list[str] = []
            variables: dict = {}
            for offset, lb_id in enumerate(chunk):
                idx = start + offset
                var_defs.append(f"$id_{idx}: Int!")
                variables[f"id_{idx}"] = lb_id
                fields.append(f"  d{idx}: delete_list_book(id: $id_{idx}) {{ id }}")
            query = (
                "mutation HardcoverBatchDeleteListBooks("
                + ", ".join(var_defs)
                + ") {\n"
                + "\n".join(fields)
                + "\n}"
            )
            try:
                data = self.client.execute(query, variables, timeout) or {}
                request_error = None
            except Exception as exc:  # noqa: BLE001 - reported per entry below
                data = {}
                request_error = str(exc)
            for offset, lb_id in enumerate(chunk):
                idx = start + offset
                entry = data.get(f"d{idx}") if request_error is None else None
                if entry and entry.get("id") is not None:
                    results[lb_id] = None
                elif request_error is not None:
                    results[lb_id] = request_error
                else:
                    results[lb_id] = "Hardcover did not remove the book from the list"
        return results

    def remove_books_from_list(
        self,
        list_id: int,
        books: list[dict],
        timeout=30,
        chunk_size: int = 50,
    ) -> list[dict]:
        """Remove many books from a list using batched GraphQL requests.

        ``books`` is a list of dicts with at least ``book_id``. Looks up all
        matching list_book entries in a single query, then deletes them with
        aliased batch mutations. Returns a list (input order) of dicts:
        ``{"book": <input dict>, "removed": int, "not_on_list": bool,
        "error": str | None}``.
        """
        entry_map = self._list_book_entries(
            list_id, [book["book_id"] for book in books], timeout
        )
        all_ids: list[int] = []
        for book in books:
            all_ids.extend(entry_map.get(book["book_id"], []))
        delete_results = self._delete_list_books(all_ids, timeout, chunk_size)

        results: list[dict] = []
        for book in books:
            ids = entry_map.get(book["book_id"], [])
            if not ids:
                results.append(
                    {"book": book, "removed": 0, "not_on_list": True, "error": None}
                )
                continue
            removed = sum(1 for lb_id in ids if delete_results.get(lb_id) is None)
            errors = [delete_results[lb_id] for lb_id in ids if delete_results.get(lb_id)]
            results.append(
                {
                    "book": book,
                    "removed": removed,
                    "not_on_list": False,
                    "error": errors[0] if errors and removed == 0 else None,
                }
            )
        return results

    def create_list(self, name: str, timeout=30) -> dict:
        trimmed = name.strip()
        if not trimmed:
            raise ValueError("List name cannot be empty")

        result = self.client.execute(INSERT_LIST, {"name": trimmed}, timeout)
        entry = (result or {}).get("insert_list") or {}
        errors = entry.get("errors") or []
        if errors:
            raise RuntimeError("; ".join(str(error) for error in errors))

        list_id = entry.get("id")
        if list_id is None:
            raise RuntimeError("Hardcover did not create the list")

        list_data = entry.get("list") or {}
        return {
            "id": list_id,
            "name": list_data.get("name") or trimmed,
            "slug": list_data.get("slug"),
        }
