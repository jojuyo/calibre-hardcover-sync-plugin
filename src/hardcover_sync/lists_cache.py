from .book_details import COLUMN_KEY, _db
from .config import PLUGIN_PREFS
from .lists import (
    get_hardcover_book_ref,
    get_hardcover_edition_id,
    is_stale_lists_column_value,
    lists_text_to_field_value,
)

LISTS_CACHE_KEY = "lists_cache"


def lists_cache_key(identifiers: dict) -> str | None:
    hardcover_id = identifiers.get("hardcover-id")
    if hardcover_id:
        return f"id:{hardcover_id}"

    edition_id = get_hardcover_edition_id(identifiers)
    if edition_id is not None:
        return f"edition:{edition_id}"

    ref = get_hardcover_book_ref(identifiers)
    if ref is None:
        return None
    if ref[0] == "id":
        return f"id:{ref[1]}"
    if ref[0] == "edition":
        return f"edition:{ref[1]}"
    return f"slug:{ref[1]}"


def get_lists_cache() -> dict[str, str]:
    cache = PLUGIN_PREFS.get(LISTS_CACHE_KEY, {})
    return dict(cache) if cache else {}


def get_cached_lists(identifiers: dict) -> str | None:
    key = lists_cache_key(identifiers)
    if key is None:
        return None
    return get_lists_cache().get(key)


def save_lists_cache_entry(
    identifiers: dict, lists_text: str, *, resolved_book_id: int | None = None
) -> None:
    key = f"id:{resolved_book_id}" if resolved_book_id else lists_cache_key(identifiers)
    if key is None or not lists_text:
        return
    if lists_text.startswith("Hardcover error:"):
        return
    cache = get_lists_cache()
    if cache.get(key) == lists_text:
        return
    cache[key] = lists_text
    PLUGIN_PREFS[LISTS_CACHE_KEY] = cache


def restore_lists_cache_to_column(gui) -> list[int]:
    """Fill stale column values from the plugin cache. Returns updated book ids."""
    db = _db(gui)
    if COLUMN_KEY not in db.field_metadata:
        return []

    cache = get_lists_cache()
    if not cache:
        return []

    updates = {}
    for book_id in db.all_book_ids():
        identifiers = dict(db.field_for("identifiers", book_id))
        key = lists_cache_key(identifiers)
        if key is None:
            continue
        cached = cache.get(key)
        if not cached:
            continue
        current = db.field_for(COLUMN_KEY, book_id)
        if not is_stale_lists_column_value(current):
            continue
        updates[book_id] = lists_text_to_field_value(cached)

    if updates:
        db.set_field(COLUMN_KEY, updates)
    return list(updates)