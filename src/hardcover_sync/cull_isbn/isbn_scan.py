import os
import re
from dataclasses import dataclass

from calibre.ebooks.conversion.preprocess import HTMLPreProcessor
from calibre.ebooks.metadata import check_isbn
from calibre.ebooks.oeb.iterator import EbookIterator
from calibre.utils.logging import GUILog

# Hyphens/dashes often used between ISBN groups (incl. U+2011 non-breaking hyphen in EPUBs).
# Spaces are intentionally excluded: they appear after the check digit in colophons
# (e.g. "eISBN: 978-0-345-52269-6   1. Time travel") and cause over-matching.
_ISBN_SEP = r"\-\.\u00ad\u2010\u2011\u2012\u2013\u2014\u2015\u2212\^"
_ISBN_BODY = rf"[0-9X][0-9{_ISBN_SEP}]{{8,24}}[0-9X]"
RE_LABELED_ISBN = re.compile(
    rf"(?i)(?:e[\s\-]*)?ISBN[\s\-]*(?:13|10)?[\s\-:]*({_ISBN_BODY})"
)
RE_ISBN = re.compile(rf"(?<![0-9X])([0-9][0-9{_ISBN_SEP}]{{8,24}}[0-9X])", re.UNICODE)
RE_STRIP_STYLE = re.compile(r"<style[^>]*>.*?</style>", re.MULTILINE | re.DOTALL)
RE_STRIP_MARKUP = re.compile(r"<[^>]+>", re.UNICODE)

EPUB_FILE_SCANS = [
    (15, 10, -5),
    (10, 6, -4),
    (6, 4, -2),
    (3, 2, -1),
    (2, 1, -1),
    (1, 1, 0),
]


def _extract_digits(text: str) -> str:
    text = text.strip()
    text = re.sub(r"(?i)^(?:e[\s\-]*)?ISBN[\s\-]*(?:13|10)?[\s\-:]*", "", text)
    if re.match(r"^-1[03][:\s]", text):
        text = text[4:].lstrip(": ")
    return re.sub(r"[^0-9X]", "", text.upper())


def _coerce_isbn(digits: str) -> tuple[str, bool] | None:
    if not digits or re.match(r"(\d)\1{9,12}$", digits):
        return None

    verified = check_isbn(digits)
    if verified:
        return verified, True

    if len(digits) == 13 and digits[:3] in ("978", "979") and digits.isdigit():
        return digits, False
    if len(digits) == 10 and re.fullmatch(r"\d{9}[\dX]", digits):
        return digits, False
    return None


def is_isbn_verified(isbn: str) -> bool:
    return check_isbn(isbn) == isbn


@dataclass(frozen=True)
class FoundIsbn:
    isbn: str
    verified: bool
    is_correction: bool = False
    corrects: str | None = None


def _correct_isbn13_check_digit(digits: str) -> str | None:
    if len(digits) != 13 or not digits.isdigit():
        return None
    body = digits[:12]
    total = sum(
        (1 if index % 2 == 0 else 3) * int(digit)
        for index, digit in enumerate(body)
    )
    check_digit = (10 - (total % 10)) % 10
    corrected = body + str(check_digit)
    if corrected == digits:
        return None
    return corrected if check_isbn(corrected) else None


def _correct_isbn10_check_digit(digits: str) -> str | None:
    digits = digits.upper()
    if len(digits) != 10 or not digits[:9].isdigit():
        return None
    body = digits[:9]
    total = sum((index + 1) * int(digit) for index, digit in enumerate(body))
    remainder = total % 11
    check_char = "X" if remainder == 10 else str(remainder)
    corrected = body + check_char
    if corrected == digits:
        return None
    return corrected if check_isbn(corrected) else None


def suggest_isbn_correction(isbn: str) -> str | None:
    if is_isbn_verified(isbn):
        return None
    digits = re.sub(r"[^0-9X]", "", isbn.upper())
    if len(digits) == 13:
        return _correct_isbn13_check_digit(digits)
    if len(digits) == 10:
        return _correct_isbn10_check_digit(digits)
    return None


def found_isbn_from_text(isbn: str) -> FoundIsbn:
    return FoundIsbn(isbn=isbn, verified=is_isbn_verified(isbn))


def finalize_found_isbns(entries: list[FoundIsbn]) -> list[FoundIsbn]:
    seen = {entry.isbn for entry in entries}
    result: list[FoundIsbn] = []

    for entry in entries:
        if entry.verified and not entry.is_correction:
            result.append(entry)

    for entry in entries:
        if entry.verified or entry.is_correction:
            continue
        result.append(entry)
        correction = suggest_isbn_correction(entry.isbn)
        if correction and correction not in seen:
            seen.add(correction)
            result.append(
                FoundIsbn(
                    isbn=correction,
                    verified=True,
                    is_correction=True,
                    corrects=entry.isbn,
                )
            )

    return result


class IsbnCollector:
    def __init__(self):
        self._seen: set[str] = set()
        self.isbns: list[str] = []

    def add_match(self, original_text: str, *, labeled: bool = False) -> None:
        digits = _extract_digits(original_text)
        coerced = _coerce_isbn(digits)
        if not coerced:
            return
        isbn, verified = coerced
        # Unlabeled 10-digit matches are often library catalog numbers (e.g. LCCN).
        if not labeled and len(isbn) == 10 and not verified:
            return
        if isbn in self._seen:
            return
        self._seen.add(isbn)
        self.isbns.append(isbn)

    def look_in_text(self, text: str, *, forward: bool = True) -> None:
        cleaned = RE_STRIP_STYLE.sub("", text)
        cleaned = RE_STRIP_MARKUP.sub(" ", cleaned)

        labeled = RE_LABELED_ISBN.findall(cleaned)
        if not forward:
            labeled = list(reversed(labeled))
        for candidate in labeled:
            self.add_match(candidate, labeled=True)

        if forward:
            for match in RE_ISBN.finditer(cleaned):
                candidate = re.sub(r"\n", "", match.group(1))
                self.add_match(candidate, labeled=False)
        else:
            for match in reversed(RE_ISBN.findall(cleaned)):
                candidate = re.sub(r"\n", "", match)
                self.add_match(candidate, labeled=False)


def find_isbns_in_book(path: str, log=None) -> list[FoundIsbn]:
    log = log or GUILog()
    collector = IsbnCollector()
    iterator = EbookIterator(path)
    try:
        iterator.__enter__(
            only_input_plugin=True,
            run_char_count=False,
            read_anchor_map=False,
        )
        if not iterator.spine:
            return []

        preprocessor = HTMLPreProcessor()

        def process_file(file_path: str, *, forward: bool = True) -> None:
            if not os.path.exists(file_path):
                return
            with open(file_path, "rb") as handle:
                html = handle.read().decode("utf-8", "replace")
            html = preprocessor(html, get_preprocess_html=True)
            collector.look_in_text(html, forward=forward)

        count = len(iterator.spine)
        for min_files, front_count, rear_count in EPUB_FILE_SCANS:
            if count >= min_files:
                first_files = iterator.spine[:front_count]
                last_files = iterator.spine[rear_count:] if rear_count else []
                middle_files = (
                    iterator.spine[front_count:rear_count] if count > min_files else []
                )
                break
        else:
            first_files = iterator.spine
            last_files = []
            middle_files = []

        for file_path in first_files:
            process_file(file_path, forward=True)
        for file_path in reversed(last_files):
            process_file(file_path, forward=False)
        for file_path in middle_files:
            process_file(file_path, forward=True)
    finally:
        iterator.__exit__(None, None, None)

    verified = [found_isbn_from_text(isbn) for isbn in collector.isbns if is_isbn_verified(isbn)]
    unverified = [
        found_isbn_from_text(isbn) for isbn in collector.isbns if not is_isbn_verified(isbn)
    ]
    return finalize_found_isbns(verified + unverified)


def find_isbns_for_book(db, book_id: int, log=None) -> list[FoundIsbn]:
    from calibre.gui2.convert.single import sort_formats_by_preference
    from calibre.utils.config import prefs

    log = log or GUILog()
    formats = db.formats(book_id)
    if not formats:
        return []

    input_map = prefs["input_format_order"]
    sorted_formats = sort_formats_by_preference(formats, input_map)
    found: list[FoundIsbn] = []
    seen: set[str] = set()

    for fmt in sorted_formats:
        path = db.format_abspath(book_id, fmt)
        if not path or not os.path.exists(path):
            continue
        try:
            for entry in find_isbns_in_book(path, log=log):
                if entry.isbn not in seen:
                    seen.add(entry.isbn)
                    found.append(entry)
        except Exception as exc:
            log.error(f"Failed to scan {fmt} for ISBNs: {exc}")

    verified = [entry for entry in found if entry.verified and not entry.is_correction]
    unverified = [entry for entry in found if not entry.verified]
    return finalize_found_isbns(verified + unverified)
