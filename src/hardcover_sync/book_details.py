import json
import re

COLUMN_LABEL = "hardcover_lists"
COLUMN_KEY = "#hardcover_lists"
COLUMN_NAME = "Hardcover Lists"
BOOK_DETAILS_PREF = "book_display_fields"

TEXT_DISPLAY = {"description": ""}

RATING_COLUMN_LABEL = "hc_rating"
RATING_COLUMN_KEY = "#hc_rating"
RATING_COLUMN_NAME = "Rating"
RATING_DISPLAY = {"allow_half_stars": True}

REVIEW_COLUMN_LABEL = "review"
REVIEW_COLUMN_KEY = "#review"
REVIEW_COLUMN_NAME = "Review"
# A long-text (comments-style) column rendered as HTML, matching the column a
# user would create by hand for reviews.
REVIEW_DISPLAY = {"interpret_as": "html", "heading_position": "hide"}

NOTES_COLUMN_LABEL = "hc_notes"
NOTES_COLUMN_KEY = "#hc_notes"
NOTES_COLUMN_NAME = "Notes"

QUOTES_COLUMN_LABEL = "hc_quotes"
QUOTES_COLUMN_KEY = "#hc_quotes"
QUOTES_COLUMN_NAME = "Quotes"

# A long-text column shown as plain text, with the comments-style editor. Used
# for the Notes and Quotes columns, which can hold multiple journal entries.
JOURNAL_DISPLAY = {"interpret_as": "long-text", "heading_position": "side"}

# Journal entries are joined into one column with this separator; on push the
# column is split back on any line that is just dashes.
JOURNAL_SEPARATOR = "\n\n---\n\n"
_JOURNAL_SPLIT_RE = re.compile(r"(?m)^[ \t]*-{3,}[ \t]*$")
# A quote line may be prefixed with a page like "p123: ..." to set position.
_QUOTE_PAGE_RE = re.compile(r"^[ \t]*p\.?[ \t]*(\d+)[ \t]*[:\-][ \t]*", re.IGNORECASE)


def split_journal_entries(value) -> list[str]:
    """Split a Notes/Quotes column value into individual trimmed entries."""
    text = comments_to_plain_text(value)
    parts = _JOURNAL_SPLIT_RE.split(text)
    return [part.strip() for part in parts if part.strip()]


def join_journal_entries(entries) -> str:
    """Join individual entries into a single column value."""
    return JOURNAL_SEPARATOR.join(entry.strip() for entry in entries if entry.strip())


def parse_quote_page(text):
    """Return (quote_text, page or None), reading an optional 'p123:' prefix."""
    match = _QUOTE_PAGE_RE.match(text or "")
    if not match:
        return text.strip(), None
    page = int(match.group(1))
    return text[match.end():].strip(), page


def format_quote_entry(entry: str, page) -> str:
    """Render a quote entry with an optional page prefix for the column."""
    entry = (entry or "").strip()
    if page is None:
        return entry
    return f"p{page}: {entry}"


STATUS_COLUMN_LABEL = "hc_status"
STATUS_COLUMN_KEY = "#hc_status"
STATUS_COLUMN_NAME = "Status"
# Hardcover user_book_statuses, in shelf order. The ids are stable on the API.
STATUS_VALUES = (
    (1, "Want to Read"),
    (2, "Currently Reading"),
    (3, "Read"),
    (4, "Paused"),
    (5, "Did Not Finish"),
    (6, "Ignored"),
)
STATUS_ID_TO_NAME = {sid: name for sid, name in STATUS_VALUES}
STATUS_NAME_TO_ID = {name: sid for sid, name in STATUS_VALUES}
STATUS_DISPLAY = {
    "enum_values": [name for _sid, name in STATUS_VALUES],
    "enum_colors": [],
    "use_decorations": 0,
}


def hardcover_status_to_calibre(status_id) -> str | None:
    """Map a Hardcover status_id to its enumeration name, or None if unknown."""
    if status_id is None:
        return None
    try:
        return STATUS_ID_TO_NAME.get(int(status_id))
    except (TypeError, ValueError):
        return None


def calibre_status_to_hardcover(value) -> int | None:
    """Map a status name to its Hardcover status_id, or None when blank."""
    if not value:
        return None
    return STATUS_NAME_TO_ID.get(str(value).strip())


def comments_to_plain_text(value) -> str:
    """Reduce a Comments value (usually HTML) to plain text with line breaks."""
    if value is None:
        return ""
    text = str(value)
    if "<" in text and ">" in text:
        text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
        text = re.sub(r"(?i)</\s*(p|div|li|h[1-6])\s*>", "\n", text)
        text = re.sub(r"(?s)<[^>]+>", "", text)
        import html as _html

        text = _html.unescape(text)
    return text.replace("\r\n", "\n").replace("\r", "\n")


def review_value_to_slate(value) -> dict | None:
    """Convert a Comments-field value into Hardcover's Slate document.

    Accepts plain text or HTML, returning None for blank input so callers can
    skip it. Each non-empty line becomes a paragraph block, mirroring the shape
    Hardcover stores in ``review_slate``.
    """
    text = comments_to_plain_text(value)
    lines = [line.strip() for line in text.split("\n")]
    lines = [line for line in lines if line]
    if not lines:
        return None
    children = [
        {
            "data": {},
            "type": "paragraph",
            "object": "block",
            "children": [{"text": line, "object": "text"}],
        }
        for line in lines
    ]
    return {"document": {"object": "document", "children": children}}


def hardcover_rating_to_calibre(rating) -> int | None:
    """Convert a Hardcover rating (0-5, half steps) to Calibre's 0-10 scale.

    Returns None when there is no usable rating, so the column stays blank.
    """
    if rating is None:
        return None
    try:
        value = float(rating)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return max(1, min(10, int(round(value * 2))))


def calibre_rating_to_hardcover(value) -> float | None:
    """Convert Calibre's 0-10 rating to Hardcover's 0-5 scale (half steps).

    Returns None for a blank/zero rating so callers can skip it.
    """
    if value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return min(5.0, max(0.5, number / 2.0))


def _db(gui):
    """Return the Calibre Cache API for library read/write operations."""
    current = gui.current_db
    if hasattr(current, "new_api"):
        return current.new_api
    return current


def _existing_custom_column(db, label):
    query = db.backend.execute(
        "SELECT id, label, datatype, is_multiple, display "
        "FROM custom_columns WHERE label=?",
        (label,),
    )
    row = next(iter(query), None)
    if row is None:
        return None
    try:
        display = json.loads(row[4]) if row[4] else {}
    except (TypeError, ValueError):
        display = {}
    return {
        "num": row[0],
        "label": row[1],
        "datatype": row[2],
        "is_multiple": bool(row[3]),
        "display": display,
    }


def _delete_column(db, label):
    try:
        db.delete_custom_column(label=label)
    except KeyError:
        db.backend.delete_custom_column(label=label)


def _migrate_column_to_tags(db, gui) -> bool:
    from .lists import lists_text_to_field_value, normalize_lists_display

    saved = {}
    for book_id in db.all_book_ids():
        value = db.field_for(COLUMN_KEY, book_id)
        text = normalize_lists_display(value)
        if text:
            saved[book_id] = text

    _delete_column(db, COLUMN_LABEL)
    db.create_custom_column(
        COLUMN_LABEL, COLUMN_NAME, "text", True, display=TEXT_DISPLAY
    )

    updates = {
        book_id: lists_text_to_field_value(text)
        for book_id, text in saved.items()
    }
    if updates:
        db.set_field(COLUMN_KEY, updates)
    gui.library_view.model().reset()
    return True


def ensure_hardcover_lists_column(gui) -> bool:
    """Ensure the Hardcover Lists tags-like column exists and is shown in book details."""
    db = _db(gui)
    changed = False

    display = _existing_custom_column(db, "hardcover_lists_view")
    if display is not None:
        _delete_column(db, "hardcover_lists_view")
        changed = True

    existing = _existing_custom_column(db, COLUMN_LABEL)
    if existing is None:
        db.create_custom_column(
            COLUMN_LABEL, COLUMN_NAME, "text", True, display=TEXT_DISPLAY
        )
        changed = True
    elif existing["datatype"] != "text":
        _delete_column(db, COLUMN_LABEL)
        db.create_custom_column(
            COLUMN_LABEL, COLUMN_NAME, "text", True, display=TEXT_DISPLAY
        )
        changed = True
    elif not existing["is_multiple"]:
        changed = _migrate_column_to_tags(db, gui) or changed

    if _ensure_field_in_book_details(db):
        changed = True

    if changed:
        gui.library_view.model().reset()
    return changed


def _enable_half_stars(db, existing) -> bool:
    """Turn on half stars for an existing rating column that lacks it."""
    if existing.get("display", {}).get("allow_half_stars"):
        return False
    display = dict(existing.get("display") or {})
    display["allow_half_stars"] = True
    db.set_custom_column_metadata(existing["num"], display=display)
    return True


def ensure_hc_rating_column(gui) -> bool:
    """Ensure the hc_rating star column (with half stars) exists."""
    db = _db(gui)
    changed = False

    existing = _existing_custom_column(db, RATING_COLUMN_LABEL)
    if existing is None:
        db.create_custom_column(
            RATING_COLUMN_LABEL,
            RATING_COLUMN_NAME,
            "rating",
            False,
            display=dict(RATING_DISPLAY),
        )
        changed = True
    elif existing["datatype"] != "rating":
        _delete_column(db, RATING_COLUMN_LABEL)
        db.create_custom_column(
            RATING_COLUMN_LABEL,
            RATING_COLUMN_NAME,
            "rating",
            False,
            display=dict(RATING_DISPLAY),
        )
        changed = True
    elif _enable_half_stars(db, existing):
        changed = True

    if _ensure_key_in_book_details(db, RATING_COLUMN_KEY):
        changed = True

    if changed:
        gui.library_view.model().reset()
    return changed


def ensure_hc_review_column(gui) -> bool:
    """Ensure a long-text Review column exists, without touching an existing one.

    Reviews sync to the ``#review`` column. If the user already has one (any
    long-text/comments column with that label), we leave its settings alone and
    only create the column when it is missing. Also removes the obsolete
    ``hc_review`` column from earlier builds.
    """
    db = _db(gui)
    changed = False

    if _existing_custom_column(db, "hc_review") is not None:
        _delete_column(db, "hc_review")
        fieldlist = list(db.pref(BOOK_DETAILS_PREF) or ())
        if any(field == "#hc_review" for field, _ in fieldlist):
            db.set_pref(
                BOOK_DETAILS_PREF,
                [
                    (field, show)
                    for field, show in fieldlist
                    if field != "#hc_review"
                ],
            )
        changed = True

    existing = _existing_custom_column(db, REVIEW_COLUMN_LABEL)
    if existing is None:
        db.create_custom_column(
            REVIEW_COLUMN_LABEL,
            REVIEW_COLUMN_NAME,
            "comments",
            False,
            display=dict(REVIEW_DISPLAY),
        )
        _ensure_key_in_book_details(db, REVIEW_COLUMN_KEY)
        changed = True

    if changed:
        gui.library_view.model().reset()
    return changed


def ensure_hc_status_column(gui) -> bool:
    """Ensure the hc_status enumeration column exists and shows in book details."""
    db = _db(gui)
    changed = False

    existing = _existing_custom_column(db, STATUS_COLUMN_LABEL)
    if existing is None:
        db.create_custom_column(
            STATUS_COLUMN_LABEL,
            STATUS_COLUMN_NAME,
            "enumeration",
            False,
            display=dict(STATUS_DISPLAY),
        )
        changed = True
    elif existing["datatype"] != "enumeration":
        _delete_column(db, STATUS_COLUMN_LABEL)
        db.create_custom_column(
            STATUS_COLUMN_LABEL,
            STATUS_COLUMN_NAME,
            "enumeration",
            False,
            display=dict(STATUS_DISPLAY),
        )
        changed = True

    if _ensure_key_in_book_details(db, STATUS_COLUMN_KEY):
        changed = True

    if changed:
        gui.library_view.model().reset()
    return changed


def _ensure_long_text_column(gui, label, name, key) -> bool:
    """Ensure a long-text (comments) column exists and shows in book details."""
    db = _db(gui)
    changed = False

    existing = _existing_custom_column(db, label)
    if existing is None:
        db.create_custom_column(
            label, name, "comments", False, display=dict(JOURNAL_DISPLAY)
        )
        changed = True
    elif existing["datatype"] != "comments":
        _delete_column(db, label)
        db.create_custom_column(
            label, name, "comments", False, display=dict(JOURNAL_DISPLAY)
        )
        changed = True

    if _ensure_key_in_book_details(db, key):
        changed = True

    if changed:
        gui.library_view.model().reset()
    return changed


def ensure_hc_notes_column(gui) -> bool:
    """Ensure the hc_notes long-text column exists."""
    return _ensure_long_text_column(
        gui, NOTES_COLUMN_LABEL, NOTES_COLUMN_NAME, NOTES_COLUMN_KEY
    )


def ensure_hc_quotes_column(gui) -> bool:
    """Ensure the hc_quotes long-text column exists."""
    return _ensure_long_text_column(
        gui, QUOTES_COLUMN_LABEL, QUOTES_COLUMN_NAME, QUOTES_COLUMN_KEY
    )


def _ensure_key_in_book_details(db, key) -> bool:
    """Show the given column in the Book Details panel, inserted after authors."""
    fieldlist = list(db.pref(BOOK_DETAILS_PREF) or ())
    if any(field == key for field, _ in fieldlist):
        return False

    insert_idx = len(fieldlist)
    for index, (field, _) in enumerate(fieldlist):
        if field == "authors":
            insert_idx = index + 1
            break
    fieldlist.insert(insert_idx, (key, True))
    db.set_pref(BOOK_DETAILS_PREF, fieldlist)
    return True


def _ensure_field_in_book_details(db) -> bool:
    fieldlist = list(db.pref(BOOK_DETAILS_PREF) or ())
    updated = False

    if any(field == "#hardcover_lists_view" for field, _ in fieldlist):
        fieldlist = [
            (field, show)
            for field, show in fieldlist
            if field != "#hardcover_lists_view"
        ]
        db.set_pref(BOOK_DETAILS_PREF, fieldlist)
        updated = True

    if _ensure_key_in_book_details(db, COLUMN_KEY):
        updated = True

    return updated
