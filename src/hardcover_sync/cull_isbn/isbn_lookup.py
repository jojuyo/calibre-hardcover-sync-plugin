import json
from dataclasses import dataclass
from urllib import error, parse, request

from calibre.ebooks.metadata import check_isbn
from calibre_plugins.hardcover_sync.hcl_graphql.client import GraphQLClient

from calibre_plugins.hardcover_sync._version import __version__
from calibre_plugins.hardcover_sync.config import get_api_key

API_URL = "https://api.hardcover.app/v1/graphql"

LOOKUP_BY_ISBN = """
query LookupEditionByIsbn($isbn: String!) {
  editions(
    where: {
      _or: [
        {isbn_13: {_eq: $isbn}},
        {isbn_10: {_eq: $isbn}}
      ]
    }
    order_by: {users_count: desc_nulls_last}
    limit: 3
  ) {
    title
    isbn_10
    isbn_13
    edition_format
    reading_format {
      format
    }
    cached_contributors
    image {
      url
    }
    publisher {
      name
    }
    book {
      title
    }
  }
}
"""


@dataclass(frozen=True)
class IsbnLookupResult:
    title: str
    authors: str
    format_type: str
    source: str
    work_title: str | None = None
    isbn_10: str | None = None
    isbn_13: str | None = None
    publisher: str | None = None
    cover_url: str | None = None


def isbn10_to_isbn13(isbn10: str) -> str | None:
    normalized = check_isbn(isbn10)
    if not normalized or len(normalized) != 10:
        return None
    body = f"978{normalized[:9]}"
    total = sum((1 if index % 2 == 0 else 3) * int(digit) for index, digit in enumerate(body))
    check_digit = (10 - (total % 10)) % 10
    candidate = body + str(check_digit)
    return check_isbn(candidate)


def _normalized_isbn(value: str | None) -> str | None:
    if not value:
        return None
    return check_isbn(str(value))


def simplify_format(
    edition_format: str | None, reading_format: str | None
) -> str:
    """Collapse Hardcover's many formats into Book / Audio Book / E-Book.

    Hardcover's ``reading_format`` is one of Read / Listened / Ebook / Both,
    and ``edition_format`` is free text (Paperback, Mass Market, Audio CD, …).
    """
    rf = (reading_format or "").strip().lower()
    ef = (edition_format or "").strip().lower()
    if rf == "listened" or "audio" in ef:
        return _("Audio Book")
    if (
        rf == "ebook"
        or "ebook" in ef
        or "e-book" in ef
        or "kindle" in ef
        or "digital" in ef
    ):
        return _("E-Book")
    return _("Book")


def _authors_from_contributors(contributors) -> str:
    if not contributors:
        return _("Unknown")
    names = []
    for entry in contributors:
        author = entry.get("author") or {}
        name = author.get("name")
        if name:
            names.append(name)
    return ", ".join(names) if names else _("Unknown")


def _lookup_hardcover(isbn: str, api_key: str) -> list[IsbnLookupResult]:
    useragent = f"hardcover-sync-calibre-plugin/{__version__}"
    client = GraphQLClient(API_URL, useragent)
    client.set_token(api_key)
    data = client.execute(LOOKUP_BY_ISBN, {"isbn": isbn})
    editions = data.get("editions") or []
    results: list[IsbnLookupResult] = []
    for edition in editions:
        if not edition:
            continue
        book = edition.get("book") or {}
        reading_format = (edition.get("reading_format") or {}).get("format")
        isbn_10 = _normalized_isbn(edition.get("isbn_10"))
        isbn_13 = _normalized_isbn(edition.get("isbn_13"))
        if isbn_10 and not isbn_13:
            isbn_13 = isbn10_to_isbn13(isbn_10)
        results.append(
            IsbnLookupResult(
                title=edition.get("title") or book.get("title") or _("Unknown"),
                authors=_authors_from_contributors(edition.get("cached_contributors")),
                format_type=simplify_format(
                    edition.get("edition_format"), reading_format
                ),
                source="Hardcover",
                work_title=book.get("title"),
                isbn_10=isbn_10,
                isbn_13=isbn_13,
                publisher=(edition.get("publisher") or {}).get("name"),
                cover_url=(edition.get("image") or {}).get("url"),
            )
        )
    return results


def _lookup_open_library(isbn: str) -> IsbnLookupResult | None:
    url = (
        "https://openlibrary.org/api/books?"
        + parse.urlencode(
            {
                "bibkeys": f"ISBN:{isbn}",
                "format": "json",
                "jscmd": "data",
            }
        )
    )
    try:
        with request.urlopen(url, timeout=20) as response:  # noqa: S310
            payload = json.load(response)
    except error.URLError:
        return None

    entry = payload.get(f"ISBN:{isbn}")
    if not entry:
        return None

    authors = ", ".join(
        author.get("name", "")
        for author in entry.get("authors") or []
        if author.get("name")
    )
    normalized = _normalized_isbn(isbn)
    isbn_10 = normalized if normalized and len(normalized) == 10 else None
    isbn_13 = normalized if normalized and len(normalized) == 13 else None
    if isbn_10 and not isbn_13:
        isbn_13 = isbn10_to_isbn13(isbn_10)
    publishers = entry.get("publishers") or []
    publisher = publishers[0].get("name") if publishers else None
    cover = entry.get("cover") or {}
    cover_url = cover.get("medium") or cover.get("large") or cover.get("small")
    return IsbnLookupResult(
        title=entry.get("title") or _("Unknown"),
        authors=authors or _("Unknown"),
        format_type=simplify_format(entry.get("physical_format"), None),
        source="Open Library",
        isbn_10=isbn_10,
        isbn_13=isbn_13,
        publisher=publisher,
        cover_url=cover_url,
    )


def lookup_isbn(isbn: str) -> list[IsbnLookupResult]:
    api_key = get_api_key()
    if api_key:
        results = _lookup_hardcover(isbn, api_key)
        if results:
            return results

    open_library = _lookup_open_library(isbn)
    if open_library:
        return [open_library]

    if not api_key:
        raise LookupError(
            _(
                "No match found. Configure a Hardcover API key in the "
                "Hardcover Sync or Hardcover metadata plugin for richer results."
            )
        )
    raise LookupError(_("No book found for ISBN {isbn}.").format(isbn=isbn))
