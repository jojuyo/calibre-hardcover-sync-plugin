LIST_MEMBERSHIP_BY_SLUG = """
query HardcoverListMembershipBySlug($book_slug: String!) {
  me {
    lists(order_by: {created_at: desc}) {
      name
      slug
      list_books(
        where: {book: {slug: {_eq: $book_slug}, editions: {}}}
        limit: 1
      ) {
        book {
          title
        }
      }
    }
  }
}
"""

LIST_MEMBERSHIP_BY_ID = """
query HardcoverListMembershipById($book_id: Int!) {
  me {
    lists(order_by: {created_at: desc}) {
      name
      slug
      list_books(
        where: {book: {id: {_eq: $book_id}, editions: {}}}
        limit: 1
      ) {
        book {
          title
        }
      }
    }
  }
}
"""

USER_LISTS = """
query HardcoverUserLists {
  me {
    lists(order_by: {created_at: desc}) {
      id
      name
      slug
    }
  }
}
"""

CURRENT_USER_ID = """
query HardcoverCurrentUserId {
  me {
    id
  }
}
"""

# Stream every rated book for the user in a few paginated requests, mirroring
# ALL_LIST_BOOKS. Lets us build a rating snapshot once instead of probing or
# resolving each selected book individually.
ALL_USER_RATINGS = """
query HardcoverAllUserRatings($user_id: Int!, $limit: Int!, $offset: Int!) {
  user_books(
    where: {user_id: {_eq: $user_id}, rating: {_is_null: false}}
    order_by: {id: asc}
    limit: $limit
    offset: $offset
  ) {
    book_id
    rating
    book {
      slug
    }
  }
}
"""

# The reading rows for given user_books, used to retarget the auto-created
# "finished" date when a book is freshly marked as Read.
USER_BOOK_READS = """
query HardcoverUserBookReads($ids: [Int!]!) {
  user_book_reads(where: {user_book_id: {_in: $ids}}, order_by: {id: asc}) {
    id
    user_book_id
  }
}
"""

# Existing user_book entry ids for the given books, so a push can decide
# between updating an existing entry and inserting a new one.
USER_BOOK_IDS = """
query HardcoverUserBookIds($user_id: Int!, $book_ids: [Int!]!) {
  user_books(
    where: {user_id: {_eq: $user_id}, book_id: {_in: $book_ids}}
  ) {
    id
    book_id
  }
}
"""

# Stream every reviewed book for the user, mirroring ALL_USER_RATINGS. We pull
# the rendered HTML (review) into the built-in Comments field.
ALL_USER_REVIEWS = """
query HardcoverAllUserReviews($user_id: Int!, $limit: Int!, $offset: Int!) {
  user_books(
    where: {user_id: {_eq: $user_id}, review: {_is_null: false}}
    order_by: {id: asc}
    limit: $limit
    offset: $offset
  ) {
    book_id
    review
    book {
      slug
    }
  }
}
"""

# Stream every book the user has shelved (each user_book carries a status_id),
# mirroring ALL_USER_RATINGS. Used to populate the local Status column.
ALL_USER_STATUSES = """
query HardcoverAllUserStatuses($user_id: Int!, $limit: Int!, $offset: Int!) {
  user_books(
    where: {user_id: {_eq: $user_id}, status_id: {_is_null: false}}
    order_by: {id: asc}
    limit: $limit
    offset: $offset
  ) {
    book_id
    status_id
    book {
      slug
    }
  }
}
"""

# Existing user_book entry id + reviewed_at for the given books, so a review
# push can update in place and only stamp reviewed_at when none exists yet.
USER_BOOK_REVIEW_STATE = """
query HardcoverUserBookReviewState($user_id: Int!, $book_ids: [Int!]!) {
  user_books(
    where: {user_id: {_eq: $user_id}, book_id: {_in: $book_ids}}
  ) {
    id
    book_id
    reviewed_at
  }
}
"""

# Fetch every book entry across all of the user's lists in one paginated stream.
ALL_LIST_BOOKS = """
query HardcoverAllListBooks($user_id: Int!, $limit: Int!, $offset: Int!) {
  list_books(
    where: {list: {user_id: {_eq: $user_id}}}
    order_by: {id: asc}
    limit: $limit
    offset: $offset
  ) {
    book_id
    book {
      slug
    }
    list {
      name
    }
  }
}
"""

# Resolve many edition ids to their canonical book in a single request.
BOOKS_BY_EDITIONS = """
query HardcoverBooksByEditions($ids: [Int!]!) {
  editions(where: {id: {_in: $ids}}) {
    id
    book {
      id
      slug
    }
  }
}
"""

BOOK_ID_BY_SLUG = """
query HardcoverBookIdBySlug($slug: String!) {
  books(where: {slug: {_eq: $slug}}, limit: 1) {
    id
  }
}
"""

BOOK_ID_BY_EDITION = """
query HardcoverBookIdByEdition($edition_id: Int!) {
  editions(where: {id: {_eq: $edition_id}}, limit: 1) {
    book {
      id
    }
  }
}
"""

LIST_BOOK_ENTRY = """
query HardcoverListBookEntry($list_id: Int!, $book_id: Int!) {
  list_books(
    where: {list_id: {_eq: $list_id}, book_id: {_eq: $book_id}}
    limit: 1
  ) {
    id
  }
}
"""

LIST_BOOK_ENTRIES = """
query HardcoverListBookEntries(
  $list_id: Int!
  $book_ids: [Int!]!
  $limit: Int!
  $offset: Int!
) {
  list_books(
    where: {list_id: {_eq: $list_id}, book_id: {_in: $book_ids}}
    order_by: {id: asc}
    limit: $limit
    offset: $offset
  ) {
    id
    book_id
  }
}
"""

INSERT_LIST_BOOK = """
mutation HardcoverInsertListBook(
  $list_id: Int!
  $book_id: Int!
  $edition_id: Int
) {
  insert_list_book(
    object: {list_id: $list_id, book_id: $book_id, edition_id: $edition_id}
  ) {
    id
  }
}
"""

INSERT_LIST = """
mutation HardcoverInsertList($name: String!) {
  insert_list(object: {name: $name}) {
    id
    errors
    list {
      id
      name
      slug
    }
  }
}
"""

DELETE_LIST_BOOK = """
mutation HardcoverDeleteListBook($id: Int!) {
  delete_list_book(id: $id) {
    id
  }
}
"""

# Stream every note/quote journal entry for the user, paginated, for pulling
# them into the local Notes/Quotes columns.
ALL_USER_JOURNALS = """
query HardcoverAllUserJournals($user_id: Int!, $limit: Int!, $offset: Int!) {
  reading_journals(
    where: {
      user_id: {_eq: $user_id}
      event: {_in: ["note", "quote"]}
      entry: {_is_null: false}
    }
    order_by: {id: asc}
    limit: $limit
    offset: $offset
  ) {
    id
    book_id
    event
    entry
    metadata
    book {
      slug
    }
  }
}
"""

# Existing note/quote entries for specific books, so a push can reconcile the
# column against what is already on Hardcover.
JOURNAL_ENTRIES_FOR_BOOKS = """
query HardcoverJournalEntriesForBooks($user_id: Int!, $book_ids: [Int!]!) {
  reading_journals(
    where: {
      user_id: {_eq: $user_id}
      book_id: {_in: $book_ids}
      event: {_in: ["note", "quote"]}
      entry: {_is_null: false}
    }
    order_by: {id: asc}
  ) {
    id
    book_id
    event
    entry
    metadata
  }
}
"""

# Note: insert/delete of reading_journal entries are built as aliased batch
# mutations in lists.py (push_journals), so there are no single-op constants.

# Stream every free-form ("Tag" category) tagging the user applied to books,
# paginated, for pulling them into Calibre's native tags field. Structured
# categories (Genre, Mood, …) are intentionally excluded.
ALL_USER_TAGS = """
query HardcoverAllUserTags($user_id: Int!, $limit: Int!, $offset: Int!) {
  taggings(
    where: {
      user_id: {_eq: $user_id}
      taggable_type: {_eq: "Book"}
      tag: {tag_category: {category: {_eq: "Tag"}}}
    }
    order_by: {id: asc}
    limit: $limit
    offset: $offset
  ) {
    taggable_id
    tag {
      tag
    }
    book {
      slug
    }
  }
}
"""

# All of the user's taggings on the given books across every category, so a
# tag push can preserve structured categories (Genre, Mood, …) while replacing
# only the free-form "Tag" entries (upsert_tags replaces the whole set).
TAGGINGS_FOR_BOOKS = """
query HardcoverTaggingsForBooks($user_id: Int!, $book_ids: [bigint!]!) {
  taggings(
    where: {
      user_id: {_eq: $user_id}
      taggable_type: {_eq: "Book"}
      taggable_id: {_in: $book_ids}
    }
  ) {
    taggable_id
    spoiler
    tag {
      tag
      tag_category {
        category
      }
    }
  }
}
"""

# Note: upsert_tags is built as an aliased batch mutation in lists.py
# (push_tags), so there is no single-op constant for it.
