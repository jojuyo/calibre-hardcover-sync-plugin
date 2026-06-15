from functools import partial
from threading import Event, Thread
from time import monotonic

from calibre.db.listeners import EventType
from calibre.gui2 import error_dialog, info_dialog
from calibre.gui2.actions import InterfaceAction
from qt.core import (
    QIcon,
    QInputDialog,
    QMenu,
    QTimer,
    QToolButton,
    pyqtSignal,
)

from calibre_plugins.hardcover_sync.book_details import (
    COLUMN_KEY,
    NOTES_COLUMN_KEY,
    QUOTES_COLUMN_KEY,
    RATING_COLUMN_KEY,
    REVIEW_COLUMN_KEY,
    STATUS_COLUMN_KEY,
    _db,
    calibre_rating_to_hardcover,
    calibre_status_to_hardcover,
    ensure_hardcover_lists_column,
    ensure_hc_notes_column,
    ensure_hc_quotes_column,
    ensure_hc_rating_column,
    ensure_hc_review_column,
    ensure_hc_status_column,
    format_quote_entry,
    hardcover_rating_to_calibre,
    hardcover_status_to_calibre,
    join_journal_entries,
    parse_quote_page,
    review_value_to_slate,
    split_journal_entries,
)
from calibre_plugins.hardcover_sync.lists import (
    HardcoverListsClient,
    LOADING_TEXT,
    NO_IDENTIFIER,
    NOT_ON_LISTS,
    READ_STATUS_ID,
    SPECIAL_COLUMN_VALUES,
    column_values_equal,
    get_hardcover_edition_id,
    get_hardcover_lookup,
    has_hardcover_link,
    is_stale_lists_column_value,
    lists_text_to_field_value,
    normalize_lists_display,
)
from calibre_plugins.hardcover_sync.lists_cache import (
    get_cached_lists,
    restore_lists_cache_to_column,
    save_lists_cache_entry,
)
from calibre_plugins.hardcover_sync.config import (
    auto_push_prompted,
    ensure_plugin_prefs,
    get_auto_push,
    set_auto_push,
    set_auto_push_prompted,
)
from calibre_plugins.hardcover_sync.menu_setup import ensure_context_menu_action
from calibre_plugins.hardcover_sync.cull_isbn.isbn_select_dialog import (
    IsbnSelectDialog,
)
from calibre_plugins.hardcover_sync.read_date_dialog import ReadDateDialog
from calibre_plugins.hardcover_sync.auto_push_dialog import ask_auto_push

# Apply list results in small batches so multi-select updates show progress
# without waiting for the entire selection to finish.
FETCH_APPLY_SIZE = 3

# Custom column field -> internal push kind. Editing one of these columns is
# what can trigger an automatic push to Hardcover.
_AUTO_PUSH_FIELDS = {
    RATING_COLUMN_KEY: "rating",
    REVIEW_COLUMN_KEY: "review",
    STATUS_COLUMN_KEY: "status",
    NOTES_COLUMN_KEY: "note",
    QUOTES_COLUMN_KEY: "quote",
}

# How long (seconds) a column write we made ourselves (e.g. during a pull)
# stays flagged so the resulting edit event does not bounce straight back.
_SELF_WRITE_TTL = 30.0

# Temporary "marked" label used to put a red pin on rows whose last push/pull
# failed. Searchable in calibre with: marked:"=Hardcover sync error"
HARDCOVER_ERROR_MARK = "Hardcover sync error"


class HardcoverSyncAction(InterfaceAction):
    name = "Hardcover Sync"
    action_type = "current"
    popup_type = QToolButton.ToolButtonPopupMode.InstantPopup
    lists_batch_fetched = pyqtSignal(int, object)
    books_membership_changed = pyqtSignal(object)
    membership_delta = pyqtSignal(object, str, str)
    user_lists_loaded = pyqtSignal(object, object)
    list_operation_done = pyqtSignal(str, str)
    status_message = pyqtSignal(str, int)
    status_clear = pyqtSignal()
    isbns_found = pyqtSignal(int, int, object)
    ratings_pulled = pyqtSignal(int, object)
    ratings_pushed = pyqtSignal(int, object)
    reviews_pulled = pyqtSignal(int, object)
    reviews_pushed = pyqtSignal(int, object)
    statuses_pulled = pyqtSignal(int, object)
    statuses_pushed = pyqtSignal(int, object)
    journals_pulled = pyqtSignal(int, object)
    journals_pushed = pyqtSignal(int, object)
    tags_pulled = pyqtSignal(int, object)
    tags_pushed = pyqtSignal(int, object)
    read_date_requested = pyqtSignal(object)
    metadata_edited = pyqtSignal(str, object)
    action_spec = (
        "Hardcover Sync",
        None,
        _("Sync the selected books with Hardcover"),
        None,
    )
    dont_add_to = frozenset(
        [
            "menubar",
            "toolbar",
            "toolbar-child",
            "context-menu-device",
            "toolbar-device",
            "menubar-device",
        ]
    )

    def genesis(self):
        self._update_timer = QTimer(self)
        self._update_timer.setSingleShot(True)
        self._update_timer.setInterval(300)
        self._update_timer.timeout.connect(self._update_lists_for_selection)
        self._fetch_counter = 0
        self._fetch_status = None
        self._manual_refresh = False
        self._suppress_selection_updates = 0
        self._isbn_scan_counter = 0
        self._rating_pull_counter = 0
        self._rating_push_counter = 0
        self._review_pull_counter = 0
        self._review_push_counter = 0
        self._status_pull_counter = 0
        self._status_push_counter = 0
        self._journal_pull_counter = {"note": 0, "quote": 0}
        self._journal_push_counter = {"note": 0, "quote": 0}
        self._tag_pull_counter = 0
        self._tag_push_counter = 0
        self._read_date_event = None
        self._read_date_value = None
        self._auto_push_pending = {}
        self._auto_push_suppress = {}
        self._auto_push_prompt_open = False
        self._db_event_listener = None
        self._auto_push_timer = QTimer(self)
        self._auto_push_timer.setSingleShot(True)
        self._auto_push_timer.setInterval(1500)
        self._auto_push_timer.timeout.connect(self._flush_auto_push)
        self._lists_client = HardcoverListsClient()
        self._user_lists = []
        self._user_lists_loading = False
        self.lists_batch_fetched.connect(self._apply_lists_batch)
        self.books_membership_changed.connect(self._refresh_books_by_ids)
        self.membership_delta.connect(self._on_membership_delta)
        self.user_lists_loaded.connect(self._store_user_lists)
        self.list_operation_done.connect(self._show_operation_message)
        self.status_message.connect(self._on_status_message)
        self.status_clear.connect(self._on_status_clear)
        self.isbns_found.connect(self._on_isbns_found)
        self.ratings_pulled.connect(self._on_ratings_pulled)
        self.ratings_pushed.connect(self._on_ratings_pushed)
        self.reviews_pulled.connect(self._on_reviews_pulled)
        self.reviews_pushed.connect(self._on_reviews_pushed)
        self.statuses_pulled.connect(self._on_statuses_pulled)
        self.statuses_pushed.connect(self._on_statuses_pushed)
        self.journals_pulled.connect(self._on_journals_pulled)
        self.journals_pushed.connect(self._on_journals_pushed)
        self.tags_pulled.connect(self._on_tags_pulled)
        self.tags_pushed.connect(self._on_tags_pushed)
        self.read_date_requested.connect(self._show_read_date_dialog)
        self.metadata_edited.connect(self._on_metadata_edited)

        self._set_action_icon()
        self.menu = QMenu(self.gui)
        self.qaction.setMenu(self.menu)
        self._menu_book_ids: list[int] = []
        self.menu.aboutToShow.connect(self._on_menu_about_to_show)
        self.menu.aboutToShow.connect(self._populate_context_menu)

    def _set_action_icon(self):
        """Give the action (and its context-menu entry) a sync icon."""
        self.qaction.setIcon(self._icon("auto-reload.png"))

    @staticmethod
    def _icon(name: str) -> QIcon:
        """Load a built-in Calibre theme icon; never raise for a cosmetic icon."""
        try:
            return QIcon(I(name))
        except Exception:  # noqa: BLE001 - icon is cosmetic, never block
            return QIcon()

    def initialization_complete(self):
        ensure_plugin_prefs()
        ensure_context_menu_action(self.gui)
        QTimer.singleShot(0, self._setup_book_details)
        self.gui.library_view.selectionModel().selectionChanged.connect(
            self._schedule_list_update
        )
        self._register_db_listener()
        self._schedule_list_update()
        self._refresh_user_lists()

    def gui_layout_complete(self):
        ensure_context_menu_action(self.gui)
        QTimer.singleShot(0, self._refresh_tag_browser)

    def library_changed(self, db):
        QTimer.singleShot(0, self._setup_book_details)
        self._register_db_listener()
        self._auto_push_pending.clear()
        self._auto_push_suppress.clear()
        self._schedule_list_update()
        self._refresh_user_lists()

    def location_selected(self, loc):
        self.qaction.setEnabled(loc == "library")

    def _setup_book_details(self):
        if not getattr(self.gui, "current_db", None):
            return
        try:
            changed = ensure_hardcover_lists_column(self.gui)
            changed = ensure_hc_rating_column(self.gui) or changed
            changed = ensure_hc_review_column(self.gui) or changed
            changed = ensure_hc_status_column(self.gui) or changed
            changed = ensure_hc_notes_column(self.gui) or changed
            changed = ensure_hc_quotes_column(self.gui) or changed
        except Exception:
            import traceback

            traceback.print_exc()
            return

        if changed:
            from calibre.gui2.dialogs.confirm_delete import confirm

            confirm(
                _(
                    "Hardcover Sync updated the custom column in your library. "
                    "Restart calibre if the field does not appear in book details."
                ),
                "hardcover_lists_column_created",
                parent=self.gui,
            )

        def restore_and_refresh():
            restored = restore_lists_cache_to_column(self.gui)
            if restored:
                self._refresh_books_in_ui(restored)
            else:
                self._refresh_tag_browser()

        self._run_without_selection_updates(restore_and_refresh)
        self._schedule_list_update()

    def _schedule_list_update(self, *args):
        if self._suppress_selection_updates:
            return
        self._update_timer.start()

    def _run_without_selection_updates(self, callback):
        self._suppress_selection_updates += 1
        try:
            callback()
        finally:
            self._suppress_selection_updates -= 1

    def _on_status_message(self, message: str, timeout: int = 0):
        if not hasattr(self.gui, "status_bar"):
            return
        self.gui.status_bar.show_message(
            message, timeout=timeout, show_notification=False
        )

    def _on_status_clear(self):
        if not hasattr(self.gui, "status_bar"):
            return
        self.gui.status_bar.clear_message()

    def _set_status(self, message: str, timeout: int = 0):
        self.status_message.emit(message, timeout)

    def _clear_status(self):
        self.status_clear.emit()

    def _begin_fetch_status(self, fetch_id: int, total: int, *, manual: bool = False):
        self._fetch_status = {
            "fetch_id": fetch_id,
            "total": total,
            "completed": 0,
            "errors": 0,
            "changed": 0,
            "manual": manual,
        }
        self._set_status(
            _("Hardcover Sync: looking up 0/{total}…").format(total=total)
        )

    def _update_fetch_status(
        self, fetch_id: int, results: dict, *, changed: int = 0
    ):
        status = self._fetch_status
        if not status or status["fetch_id"] != fetch_id:
            return

        status["completed"] += len(results)
        status["errors"] += sum(
            1
            for value in results.values()
            if str(value).startswith("Hardcover error:")
        )
        status["changed"] += changed

        total = status["total"]
        done = status["completed"]
        if done >= total:
            errors = status["errors"]
            changed = status["changed"]
            manual = status.get("manual", False)
            if errors:
                message = _(
                    "Hardcover Sync: finished {done}/{total} ({errors} errors)"
                ).format(done=done, total=total, errors=errors)
            elif manual and changed:
                message = _(
                    "Hardcover Sync: updated {changed} of {done} books"
                ).format(changed=changed, done=done)
            else:
                message = _("Hardcover Sync: finished {done} books").format(
                    done=done
                )
            self._set_status(message, timeout=5000)
            self._fetch_status = None
            self._manual_refresh = False
        else:
            self._set_status(
                _("Hardcover Sync: looking up {done}/{total}…").format(
                    done=done, total=total
                )
            )

    def _on_menu_about_to_show(self):
        self._menu_book_ids = list(self.gui.library_view.get_selected_ids())

    def _schedule_refresh_selected_books(self):
        QTimer.singleShot(0, self._refresh_selected_books_membership)

    def _selected_book_ids(self):
        return list(self.gui.library_view.get_selected_ids())

    def _selected_book_id(self):
        book_ids = self._selected_book_ids()
        if not book_ids:
            return None
        return book_ids[-1]

    def _books_with_hardcover_ids(self, db, book_ids):
        books = []
        for book_id in book_ids:
            identifiers = dict(db.field_for("identifiers", book_id))
            if not has_hardcover_link(identifiers):
                continue
            books.append((book_id, identifiers))
        return books

    def _partition_by_hardcover_link(self, db, book_ids):
        with_link = []
        without_link = []
        for book_id in book_ids:
            identifiers = dict(db.field_for("identifiers", book_id))
            if has_hardcover_link(identifiers):
                with_link.append((book_id, identifiers))
            else:
                without_link.append(book_id)
        return with_link, without_link

    def _mark_books_without_identifier(self, db, book_ids):
        """Label books that have no Hardcover identifier so they form a list."""
        value = lists_text_to_field_value(NO_IDENTIFIER)
        updates = {}
        for book_id in book_ids:
            current = db.field_for(COLUMN_KEY, book_id)
            if not column_values_equal(current, value):
                updates[book_id] = value
        if not updates:
            return []

        def apply_no_identifier():
            db.set_field(COLUMN_KEY, updates)
            self._refresh_books_in_ui(list(updates))

        self._run_without_selection_updates(apply_no_identifier)
        return list(updates)

    def _book_needs_api_refresh(self, db, book_id, identifiers) -> bool:
        current = db.field_for(COLUMN_KEY, book_id)
        if not is_stale_lists_column_value(current):
            return False
        return get_cached_lists(identifiers) is None

    def _apply_cache_to_stale_books(self, db, books) -> list[int]:
        updates = {}
        for book_id, identifiers in books:
            if not is_stale_lists_column_value(db.field_for(COLUMN_KEY, book_id)):
                continue
            cached = get_cached_lists(identifiers)
            if not cached:
                continue
            updates[book_id] = lists_text_to_field_value(cached)

        if not updates:
            return []

        def apply_cache():
            db.set_field(COLUMN_KEY, updates)
            self._refresh_books_in_ui(list(updates))

        self._run_without_selection_updates(apply_cache)
        return list(updates)

    def _update_lists_for_selection(self):
        if self._manual_refresh or self._fetch_status is not None:
            return
        if not getattr(self.gui, "current_db", None):
            return
        db = _db(self.gui)
        if COLUMN_KEY not in db.field_metadata:
            return

        book_ids = self._selected_book_ids()
        if not book_ids:
            return

        books, without_link = self._partition_by_hardcover_link(db, book_ids)
        self._mark_books_without_identifier(db, without_link)
        if not books:
            return

        restored_ids = self._apply_cache_to_stale_books(db, books)

        books_to_fetch = [
            (book_id, identifiers)
            for book_id, identifiers in books
            if self._book_needs_api_refresh(db, book_id, identifiers)
        ]
        if not books_to_fetch:
            if restored_ids:
                self._set_status(
                    _("Hardcover Sync: restored {count} from cache").format(
                        count=len(restored_ids)
                    ),
                    timeout=3000,
                )
            return

        self._queue_books_for_fetch(books_to_fetch, show_loading=True)

    def _queue_books_for_fetch(
        self, books, *, show_loading=True, manual=False, snapshot=False
    ):
        if not books:
            return

        self._fetch_counter += 1
        fetch_id = self._fetch_counter
        if manual:
            self._manual_refresh = True
        self._begin_fetch_status(fetch_id, len(books), manual=manual)

        db = _db(self.gui)
        if show_loading:
            loading = {}
            for book_id, identifiers in books:
                current = db.field_for(COLUMN_KEY, book_id)
                if manual or (
                    is_stale_lists_column_value(current)
                    and get_cached_lists(identifiers) is None
                ):
                    loading[book_id] = LOADING_TEXT
            if loading:

                def apply_loading():
                    db.set_field(
                        COLUMN_KEY,
                        {
                            book_id: lists_text_to_field_value(LOADING_TEXT)
                            for book_id in loading
                        },
                    )
                    self._refresh_books_in_ui(list(loading))

                self._run_without_selection_updates(apply_loading)

        worker = self._fetch_lists_snapshot if snapshot else self._fetch_lists_batch
        Thread(
            target=worker,
            args=(books, fetch_id),
            daemon=True,
        ).start()

    def _fetch_lists_batch(self, books, fetch_id):
        batch = {}
        for book_id, identifiers in books:
            if fetch_id != self._fetch_counter:
                if batch:
                    self.lists_batch_fetched.emit(fetch_id, dict(batch))
                return
            try:
                lists_text, resolved_id = self._lists_client.lists_for_book(
                    identifiers
                )
                batch[book_id] = (lists_text, resolved_id)
            except Exception as exc:
                batch[book_id] = (f"Hardcover error: {exc}", None)

            if len(batch) >= FETCH_APPLY_SIZE:
                self.lists_batch_fetched.emit(fetch_id, dict(batch))
                batch.clear()

        if batch:
            self.lists_batch_fetched.emit(fetch_id, batch)

    def _fetch_lists_snapshot(self, books, fetch_id):
        """Resolve membership for many books from a single bulk snapshot.

        Fetches all of the user's list entries in a few requests, batch-resolves
        any edition-only books, then maps every selected book locally instead of
        making one request per book.
        """
        self._set_status(_("Hardcover Sync: fetching list memberships…"))
        try:
            snapshot = self._lists_client.snapshot_list_memberships()
        except Exception as exc:
            self._emit_snapshot_results(
                fetch_id, [(book_id, (f"Hardcover error: {exc}", None)) for book_id, _ in books]
            )
            return

        if fetch_id != self._fetch_counter:
            return

        resolved_books = []
        edition_ids = set()
        for book_id, identifiers in books:
            hc_id, slug, edition_id = get_hardcover_lookup(identifiers)
            if hc_id is None and slug is None and edition_id is not None:
                edition_ids.add(edition_id)
            resolved_books.append((book_id, hc_id, slug, edition_id))

        edition_map = {}
        if edition_ids:
            self._set_status(
                _("Hardcover Sync: resolving {count} editions…").format(
                    count=len(edition_ids)
                )
            )
            try:
                edition_map = self._lists_client.resolve_editions(edition_ids)
            except Exception:
                edition_map = {}

        if fetch_id != self._fetch_counter:
            return

        results = []
        for book_id, hc_id, slug, edition_id in resolved_books:
            resolved_id = hc_id
            resolved_slug = slug
            if resolved_id is None and edition_id is not None:
                mapped = edition_map.get(edition_id)
                if mapped:
                    resolved_id, mapped_slug = mapped
                    if not resolved_slug:
                        resolved_slug = mapped_slug
            text = snapshot.lists_text(resolved_id, resolved_slug)
            results.append((book_id, (text, resolved_id)))

        self._emit_snapshot_results(fetch_id, results)

    def _emit_snapshot_results(self, fetch_id, results):
        batch = {}
        for book_id, payload in results:
            batch[book_id] = payload
            if len(batch) >= 50:
                self.lists_batch_fetched.emit(fetch_id, dict(batch))
                batch.clear()
        if batch:
            self.lists_batch_fetched.emit(fetch_id, batch)

    def _apply_lists_batch(self, fetch_id: int, results: dict):
        if fetch_id != self._fetch_counter:
            return

        db = _db(self.gui)
        updates = {}
        status_results = {}
        changed = 0

        for book_id, payload in results.items():
            if isinstance(payload, tuple):
                lists_text, resolved_id = payload
            else:
                lists_text, resolved_id = payload, None
            status_results[book_id] = lists_text
            if not db.has_id(book_id):
                continue
            identifiers = dict(db.field_for("identifiers", book_id))
            save_lists_cache_entry(
                identifiers, lists_text, resolved_book_id=resolved_id
            )
            field_value = lists_text_to_field_value(lists_text)
            current = db.field_for(COLUMN_KEY, book_id)
            if not column_values_equal(current, field_value):
                updates[book_id] = field_value
                changed += 1

        self._update_fetch_status(fetch_id, status_results, changed=changed)

        if not updates:
            return

        def apply_updates():
            db.set_field(COLUMN_KEY, updates)
            self._refresh_books_in_ui(list(updates))
            if book_id := self._selected_book_id():
                if book_id in updates:
                    self._refresh_book_details()

        self._run_without_selection_updates(apply_updates)

    def _refresh_tag_browser(self):
        if not hasattr(self.gui, "tags_view"):
            return
        tags_view = self.gui.tags_view

        def refresh():
            if not getattr(self.gui, "current_db", None):
                return
            tags_view.recount()

        QTimer.singleShot(0, refresh)

    def _refresh_books_in_ui(self, book_ids):
        if not book_ids:
            return
        model = self.gui.library_view.model()
        model.refresh_ids(tuple(book_ids))
        self._refresh_tag_browser()

    def _refresh_books_by_ids(self, book_ids):
        if not getattr(self.gui, "current_db", None):
            return
        db = _db(self.gui)
        books = self._books_with_hardcover_ids(db, book_ids)
        self._queue_books_for_fetch(books, show_loading=False)

    def _books_needing_forced_refresh(self, db, book_ids):
        return self._books_with_hardcover_ids(db, book_ids)

    def _refresh_selected_books_membership(self):
        if not getattr(self.gui, "current_db", None):
            return
        db = _db(self.gui)
        book_ids = list(self._menu_book_ids) or self._selected_book_ids()
        if not book_ids:
            info_dialog(
                self.gui,
                _("Hardcover Sync"),
                _(
                    "No books are selected. Select books in the library, "
                    "then choose Refresh selected books."
                ),
            ).exec()
            return

        books, without_link = self._partition_by_hardcover_link(db, book_ids)
        labeled = self._mark_books_without_identifier(db, without_link)

        if not books:
            self._set_status(
                _(
                    "Hardcover Sync: marked {count} books without a "
                    "Hardcover identifier"
                ).format(count=len(without_link)),
                timeout=5000,
            )
            return
        if labeled:
            self._set_status(
                _(
                    "Hardcover Sync: refreshing {count} books "
                    "({labeled} marked without Hardcover id)…"
                ).format(count=len(books), labeled=len(without_link))
            )
        self._queue_books_for_fetch(
            books, show_loading=True, manual=True, snapshot=True
        )

    def _refresh_book_details(self):
        current_index = self.gui.library_view.currentIndex()
        if current_index.isValid():
            self.gui.library_view.model().current_changed(
                current_index, current_index
            )

    def _refresh_user_lists(self):
        if self._user_lists_loading:
            return
        self._user_lists_loading = True
        self._set_status(_("Hardcover Sync: loading your lists…"))
        Thread(target=self._fetch_user_lists, daemon=True).start()

    def _fetch_user_lists(self):
        try:
            lists = self._lists_client.fetch_user_lists()
            error = None
        except Exception as exc:
            lists = None
            error = str(exc)
        self.user_lists_loaded.emit(lists, error)

    def _store_user_lists(self, lists, error):
        self._user_lists_loading = False
        if error is not None:
            self._user_lists = []
            self._set_status(
                _("Hardcover Sync: failed to load lists ({error})").format(
                    error=error
                ),
                timeout=5000,
            )
            return
        self._user_lists = lists or []
        if not self._fetch_status:
            self._set_status(
                _("Hardcover Sync: loaded {count} lists").format(
                    count=len(self._user_lists)
                ),
                timeout=3000,
            )

    def _populate_context_menu(self):
        self.menu.clear()
        book_ids = self.gui.library_view.get_selected_ids()
        if not book_ids:
            action = self.menu.addAction(_("No books selected"))
            action.setEnabled(False)
            return

        cull_action = self.menu.addAction(
            self._icon("identifiers.png"), _("Cull ISBN")
        )
        cull_action.triggered.connect(self._start_cull_isbn)

        self.menu.addSeparator()
        self._add_lists_menu()
        self._add_ratings_menu()
        self._add_reviews_menu()
        self._add_status_menu()
        self._add_journal_menu(_("Notes"), "note", "notes.png")
        self._add_journal_menu(_("Quotes"), "quote", "highlight.png")
        self._add_tags_menu()

    def _add_ratings_menu(self):
        ratings_menu = self.menu.addMenu(self._icon("rating.png"), _("Ratings"))
        has_token = bool(self._lists_client.client.token)

        push_action = ratings_menu.addAction(
            self._icon("arrow-up.png"), _("Push Rating")
        )
        if has_token:
            push_action.triggered.connect(self._start_push_rating)
        else:
            push_action.setEnabled(False)

        pull_action = ratings_menu.addAction(
            self._icon("arrow-down.png"), _("Pull Rating")
        )
        if has_token:
            pull_action.triggered.connect(self._start_pull_rating)
        else:
            pull_action.setEnabled(False)

    def _add_reviews_menu(self):
        reviews_menu = self.menu.addMenu(
            self._icon("edit_input.png"), _("Reviews")
        )
        has_token = bool(self._lists_client.client.token)

        push_action = reviews_menu.addAction(
            self._icon("arrow-up.png"), _("Push Review")
        )
        if has_token:
            push_action.triggered.connect(self._start_push_review)
        else:
            push_action.setEnabled(False)

        pull_action = reviews_menu.addAction(
            self._icon("arrow-down.png"), _("Pull Review")
        )
        if has_token:
            pull_action.triggered.connect(self._start_pull_review)
        else:
            pull_action.setEnabled(False)

    def _add_status_menu(self):
        status_menu = self.menu.addMenu(self._icon("reader.png"), _("Status"))
        has_token = bool(self._lists_client.client.token)

        push_action = status_menu.addAction(
            self._icon("arrow-up.png"), _("Push Status")
        )
        if has_token:
            push_action.triggered.connect(self._start_push_status)
        else:
            push_action.setEnabled(False)

        pull_action = status_menu.addAction(
            self._icon("arrow-down.png"), _("Pull Status")
        )
        if has_token:
            pull_action.triggered.connect(self._start_pull_status)
        else:
            pull_action.setEnabled(False)

    def _add_tags_menu(self):
        tags_menu = self.menu.addMenu(self._icon("tags.png"), _("Tags"))
        has_token = bool(self._lists_client.client.token)

        push_action = tags_menu.addAction(
            self._icon("arrow-up.png"), _("Push Tags")
        )
        if has_token:
            push_action.triggered.connect(self._start_push_tags)
        else:
            push_action.setEnabled(False)

        pull_action = tags_menu.addAction(
            self._icon("arrow-down.png"), _("Pull Tags")
        )
        if has_token:
            pull_action.triggered.connect(self._start_pull_tags)
        else:
            pull_action.setEnabled(False)

    def _add_journal_menu(self, title: str, kind: str, icon: str):
        menu = self.menu.addMenu(self._icon(icon), title)
        has_token = bool(self._lists_client.client.token)

        push_action = menu.addAction(
            self._icon("arrow-up.png"), _("Push {title}").format(title=title)
        )
        if has_token:
            push_action.triggered.connect(partial(self._start_push_journal, kind))
        else:
            push_action.setEnabled(False)

        pull_action = menu.addAction(
            self._icon("arrow-down.png"), _("Pull {title}").format(title=title)
        )
        if has_token:
            pull_action.triggered.connect(partial(self._start_pull_journal, kind))
        else:
            pull_action.setEnabled(False)

    def _add_lists_menu(self):
        lists_menu = self.menu.addMenu(
            self._icon("format-list-unordered.png"), _("Lists")
        )

        if not self._lists_client.client.token:
            action = lists_menu.addAction(_("Configure Hardcover API key"))
            action.setEnabled(False)
            return

        self._add_manage_lists_menu(lists_menu)
        create_action = lists_menu.addAction(
            self._icon("plus.png"), _("Create New")
        )
        create_action.triggered.connect(self._create_new_list)
        refresh_action = lists_menu.addAction(
            self._icon("view-refresh.png"), _("Refresh")
        )
        refresh_action.triggered.connect(self._schedule_refresh_selected_books)

    def _add_manage_lists_menu(self, lists_menu):
        manage_menu = lists_menu.addMenu(self._icon("gear.png"), _("Manage Lists"))

        if self._user_lists_loading and not self._user_lists:
            action = manage_menu.addAction(_("Loading lists..."))
            action.setEnabled(False)
            self._refresh_user_lists()
            return

        if not self._user_lists:
            action = manage_menu.addAction(_("No Hardcover lists found"))
            action.setEnabled(False)
            return

        for user_list in self._user_lists:
            list_menu = manage_menu.addMenu(
                self._icon("bookmarks.png"), user_list["name"]
            )
            list_id = user_list["id"]
            add_action = self.create_menu_action(
                list_menu,
                f"add-{list_id}",
                _("Add to List"),
                triggered=partial(self._add_to_list, list_id, user_list),
                shortcut=False,
            )
            add_action.setIcon(self._icon("plus.png"))
            remove_action = self.create_menu_action(
                list_menu,
                f"remove-{list_id}",
                _("Remove from List"),
                triggered=partial(self._remove_from_list, list_id, user_list),
                shortcut=False,
            )
            remove_action.setIcon(self._icon("minus.png"))

    def _show_read_date_dialog(self, titles):
        from qt.core import QDialog

        dialog = ReadDateDialog(self.gui, titles)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._read_date_value = {
                "accepted": True,
                "finished_at": dialog.result_value(),
            }
        else:
            self._read_date_value = {"accepted": False, "finished_at": None}
        if self._read_date_event is not None:
            self._read_date_event.set()

    def _request_read_date(self, titles) -> dict:
        """Ask the user (on the GUI thread) for the read date to apply.

        ``titles`` is the list of book titles being newly marked as Read.
        Blocks the calling worker thread until the modal dialog is answered.
        Returns ``{"accepted": bool, "finished_at": str | None}`` where a None
        finished_at means "Today" (Hardcover's default).
        """
        self._read_date_event = Event()
        self._read_date_value = None
        self.read_date_requested.emit(list(titles))
        self._read_date_event.wait()
        return self._read_date_value or {"accepted": False, "finished_at": None}

    # --- Automatic push on column edits -------------------------------------

    def _register_db_listener(self):
        """Listen for metadata edits so pushable columns can auto-sync."""
        if not getattr(self.gui, "current_db", None):
            return
        # Calibre keeps listeners as weak references, so a bare bound method is
        # collected immediately and never fires. Hold a strong reference.
        if self._db_event_listener is None:
            self._db_event_listener = self._on_db_event
        db = _db(self.gui)
        try:
            db.add_listener(self._db_event_listener, check_already_added=True)
        except TypeError:
            db.add_listener(self._db_event_listener)
        except Exception:  # noqa: BLE001, S110 - listener wiring is best-effort
            pass

    def _on_db_event(self, event_type, library_id, event_data):
        """Runs on the DB event thread; hand pushable edits to the GUI thread."""
        try:
            if event_type is not EventType.metadata_changed:
                return
            field, book_ids = event_data
            if field not in _AUTO_PUSH_FIELDS:
                return
            ids = [int(book_id) for book_id in book_ids]
            if ids:
                self.metadata_edited.emit(field, ids)
        except Exception:  # noqa: BLE001, S110 - never break DB event dispatch
            pass

    def _mark_self_write(self, field: str, book_ids):
        """Flag column writes we make ourselves so they don't trigger a push."""
        deadline = monotonic() + _SELF_WRITE_TTL
        store = self._auto_push_suppress
        for book_id in book_ids:
            store[(field, int(book_id))] = deadline

    def _consume_self_writes(self, field: str, book_ids):
        store = self._auto_push_suppress
        now = monotonic()
        if store:
            for key in [key for key, dl in store.items() if dl < now]:
                store.pop(key, None)
        remaining = []
        for book_id in book_ids:
            key = (field, int(book_id))
            if key in store:
                store.pop(key, None)
                continue
            remaining.append(int(book_id))
        return remaining

    def _on_metadata_edited(self, field: str, book_ids):
        kind = _AUTO_PUSH_FIELDS.get(field)
        if kind is None:
            return
        ids = self._consume_self_writes(field, book_ids)
        if not ids:
            return

        if not get_auto_push():
            if auto_push_prompted() or self._auto_push_prompt_open:
                return
            self._auto_push_prompt_open = True
            try:
                enabled = ask_auto_push(self.gui)
            finally:
                self._auto_push_prompt_open = False
            set_auto_push(enabled)
            set_auto_push_prompted(True)
            if not enabled:
                return

        pending = self._auto_push_pending.setdefault(kind, set())
        pending.update(ids)
        self._auto_push_timer.start()

    def _flush_auto_push(self):
        pending = self._auto_push_pending
        self._auto_push_pending = {}
        if not pending or not getattr(self.gui, "current_db", None):
            return
        if not get_auto_push():
            return
        dispatch = {
            "rating": self._start_push_rating,
            "review": self._start_push_review,
            "status": self._start_push_status,
        }
        for kind, ids in pending.items():
            ids = sorted(ids)
            if not ids:
                continue
            if kind in dispatch:
                dispatch[kind](book_ids=ids, auto=True)
            elif kind in ("note", "quote"):
                self._start_push_journal(kind, book_ids=ids, auto=True)

    # --- Red error pins on failed rows --------------------------------------

    def _force_red_pin(self, label: str):
        """Force the marked-text pin for ``label`` to be red."""
        try:
            from calibre.gui2.library.models import render_pin

            model = self.gui.library_view.model()
            model.marked_text_icons[label] = ("red", QIcon(render_pin("red")))
        except Exception:  # noqa: BLE001, S110 - pin colour is best-effort
            pass

    def _mark_error_rows(self, error_ids, processed_ids):
        """Pin rows that failed to sync red; clear the pin once they succeed.

        ``error_ids`` are calibre book ids in an error state. ``processed_ids``
        are all calibre book ids that took part in the operation, so books that
        previously failed but now succeeded lose their red pin.
        """
        if not getattr(self.gui, "current_db", None):
            return
        error_ids = {int(book_id) for book_id in (error_ids or [])}
        processed_ids = {int(book_id) for book_id in (processed_ids or [])}
        processed_ids |= error_ids
        if not processed_ids:
            return

        data = self.gui.current_db.data
        current = dict(getattr(data, "marked_ids", {}) or {})
        changed_ids = []
        for book_id in processed_ids:
            if book_id in error_ids:
                if current.get(book_id) != HARDCOVER_ERROR_MARK:
                    current[book_id] = HARDCOVER_ERROR_MARK
                    changed_ids.append(book_id)
            elif current.get(book_id) == HARDCOVER_ERROR_MARK:
                current.pop(book_id, None)
                changed_ids.append(book_id)

        if not changed_ids:
            return

        if error_ids:
            self._force_red_pin(HARDCOVER_ERROR_MARK)
        data.set_marked_ids(current)
        try:
            self.gui.library_view.model().refresh_ids(changed_ids)
        except Exception:  # noqa: BLE001, S110 - repaint is best-effort
            pass

    def _start_pull_rating(self):
        if not getattr(self.gui, "current_db", None):
            return
        db = _db(self.gui)
        book_ids = self._selected_book_ids()
        if not book_ids:
            error_dialog(
                self.gui,
                _("Pull Rating"),
                _("Select one or more books to pull ratings for."),
            ).exec()
            return

        books, without_link = self._partition_by_hardcover_link(db, book_ids)
        if not books:
            error_dialog(
                self.gui,
                _("Pull Rating"),
                _(
                    "None of the selected books have a Hardcover identifier. "
                    "Download metadata from Hardcover first."
                ),
            ).exec()
            return

        self._rating_pull_counter += 1
        pull_id = self._rating_pull_counter
        self._set_status(
            _("Hardcover Sync: pulling ratings for {count} books…").format(
                count=len(books)
            )
        )
        Thread(
            target=self._pull_ratings_worker,
            args=(pull_id, books, len(without_link)),
            daemon=True,
        ).start()

    def _pull_ratings_worker(self, pull_id: int, books, skipped: int):
        try:
            self._set_status(_("Hardcover Sync: fetching your ratings…"))
            snapshot = self._lists_client.snapshot_user_ratings()
        except Exception as exc:
            self.ratings_pulled.emit(pull_id, {"error": str(exc)})
            return

        if pull_id != self._rating_pull_counter:
            return

        resolved_books = []
        edition_ids = set()
        for book_id, identifiers in books:
            hc_id, slug, edition_id = get_hardcover_lookup(identifiers)
            if hc_id is None and slug is None and edition_id is not None:
                edition_ids.add(edition_id)
            resolved_books.append((book_id, hc_id, slug, edition_id))

        edition_map = {}
        if edition_ids:
            self._set_status(
                _("Hardcover Sync: resolving {count} editions…").format(
                    count=len(edition_ids)
                )
            )
            try:
                edition_map = self._lists_client.resolve_editions(edition_ids)
            except Exception:
                edition_map = {}

        if pull_id != self._rating_pull_counter:
            return

        updates: dict[int, int] = {}
        unresolved = 0
        error_ids = []
        for book_id, hc_id, slug, edition_id in resolved_books:
            resolved_id = hc_id
            resolved_slug = slug
            if resolved_id is None and edition_id is not None:
                mapped = edition_map.get(edition_id)
                if mapped:
                    resolved_id, mapped_slug = mapped
                    if not resolved_slug:
                        resolved_slug = mapped_slug
            if resolved_id is None and resolved_slug is None:
                unresolved += 1
                error_ids.append(book_id)
                continue
            value = hardcover_rating_to_calibre(
                snapshot.rating_for(resolved_id, resolved_slug)
            )
            if value is not None:
                updates[book_id] = value

        self.ratings_pulled.emit(
            pull_id,
            {
                "error": None,
                "updates": updates,
                "unresolved": unresolved,
                "skipped": skipped,
                "error_ids": error_ids,
                "processed_ids": [bid for bid, *_ in resolved_books],
            },
        )

    def _on_ratings_pulled(self, pull_id: int, payload: dict):
        if pull_id != self._rating_pull_counter:
            return

        if payload.get("error"):
            self._clear_status()
            error_dialog(
                self.gui,
                _("Pull Rating"),
                _("Failed to pull ratings: {error}").format(
                    error=payload["error"]
                ),
            ).exec()
            return

        updates = payload.get("updates") or {}
        if updates and getattr(self.gui, "current_db", None):
            db = _db(self.gui)

            def apply():
                self._mark_self_write(RATING_COLUMN_KEY, updates.keys())
                db.set_field(RATING_COLUMN_KEY, updates)
                self._refresh_books_in_ui(list(updates))

            self._run_without_selection_updates(apply)

        self._mark_error_rows(
            payload.get("error_ids"), payload.get("processed_ids")
        )

        message = _("Hardcover Sync: set {count} ratings").format(
            count=len(updates)
        )
        unresolved = payload.get("unresolved", 0) + payload.get("skipped", 0)
        if unresolved:
            message += " " + _("({count} without a Hardcover match)").format(
                count=unresolved
            )
        self._set_status(message, timeout=5000)

    def _start_push_rating(self, book_ids=None, auto=False):
        if not getattr(self.gui, "current_db", None):
            return
        db = _db(self.gui)
        # QAction.triggered passes a `checked` bool; only an explicit
        # collection (from auto-push) should override the live selection.
        if not isinstance(book_ids, (list, set, tuple)):
            book_ids = self._selected_book_ids()
        if not book_ids:
            if not auto:
                error_dialog(
                    self.gui,
                    _("Push Rating"),
                    _("Select one or more books to push ratings for."),
                ).exec()
            return

        books, without_link = self._partition_by_hardcover_link(db, book_ids)
        if not books:
            if not auto:
                error_dialog(
                    self.gui,
                    _("Push Rating"),
                    _(
                        "None of the selected books have a Hardcover identifier. "
                        "Download metadata from Hardcover first."
                    ),
                ).exec()
            return

        rated = []
        blank = 0
        for book_id, identifiers in books:
            rating = calibre_rating_to_hardcover(
                db.field_for(RATING_COLUMN_KEY, book_id)
            )
            if rating is None:
                blank += 1
                continue
            rated.append(
                {
                    "identifiers": identifiers,
                    "rating": rating,
                    "title": db.field_for("title", book_id),
                    "calibre_id": book_id,
                }
            )

        if not rated:
            if not auto:
                info_dialog(
                    self.gui,
                    _("Push Rating"),
                    _("None of the selected books have a rating to push."),
                ).exec()
            return

        self._rating_push_counter += 1
        push_id = self._rating_push_counter
        self._set_status(
            _("Hardcover Sync: pushing ratings for {count} books…").format(
                count=len(rated)
            )
        )
        Thread(
            target=self._push_ratings_worker,
            args=(push_id, rated, len(without_link), blank, auto),
            daemon=True,
        ).start()

    def _push_ratings_worker(
        self, push_id: int, rated, skipped: int, blank: int, auto: bool = False
    ):
        try:
            edition_ids = set()
            for entry in rated:
                hc_id, slug, edition_id = get_hardcover_lookup(entry["identifiers"])
                entry["hc_id"] = hc_id
                entry["slug"] = slug
                entry["edition_id"] = edition_id
                if hc_id is None and slug is None and edition_id is not None:
                    edition_ids.add(edition_id)

            edition_map = {}
            if edition_ids:
                self._set_status(
                    _("Hardcover Sync: resolving {count} editions…").format(
                        count=len(edition_ids)
                    )
                )
                try:
                    edition_map = self._lists_client.resolve_editions(edition_ids)
                except Exception:
                    edition_map = {}

            if push_id != self._rating_push_counter:
                return

            unresolved = 0
            error_ids = []
            processed_ids = [entry["calibre_id"] for entry in rated]
            resolved_items = []
            for entry in rated:
                book_id = entry["hc_id"]
                if book_id is None and entry["edition_id"] is not None:
                    mapped = edition_map.get(entry["edition_id"])
                    if mapped:
                        book_id = mapped[0]
                if book_id is None and entry["slug"] is not None:
                    book_id = self._lists_client.resolve_book_id(
                        entry["identifiers"]
                    )
                if book_id is None:
                    unresolved += 1
                    error_ids.append(entry["calibre_id"])
                    continue
                entry["resolved_book_id"] = int(book_id)
                resolved_items.append(entry)

            if push_id != self._rating_push_counter:
                return

            existing = self._lists_client.user_book_ids(
                {entry["resolved_book_id"] for entry in resolved_items}
            )

            items = [
                {
                    "book_id": entry["resolved_book_id"],
                    "rating": entry["rating"],
                    "user_book_id": existing.get(entry["resolved_book_id"]),
                    "_book": {
                        "title": entry["title"],
                        "calibre_id": entry["calibre_id"],
                    },
                }
                for entry in resolved_items
            ]

            insert_titles = [
                item["_book"]["title"]
                for item in items
                if item["user_book_id"] is None
            ]
            read_finished_at = None
            if insert_titles:
                choice = self._request_read_date(insert_titles)
                if not choice["accepted"]:
                    self.ratings_pushed.emit(push_id, {"cancelled": True})
                    return
                read_finished_at = choice["finished_at"]

            results = self._lists_client.push_ratings(
                items, read_finished_at=read_finished_at
            )

            pushed = sum(1 for result in results if result["error"] is None)
            errors = [
                f'{result["book"]["title"]}: {result["error"]}'
                for result in results
                if result["error"]
            ]
            error_ids.extend(
                result["book"].get("calibre_id")
                for result in results
                if result["error"]
            )
            payload = {
                "error": None,
                "pushed": pushed,
                "errors": errors,
                "unresolved": unresolved + skipped,
                "blank": blank,
                "auto": auto,
                "error_ids": [bid for bid in error_ids if bid is not None],
                "processed_ids": processed_ids,
            }
        except Exception as exc:
            payload = {"error": str(exc), "auto": auto}

        self.ratings_pushed.emit(push_id, payload)

    def _on_ratings_pushed(self, push_id: int, payload: dict):
        if push_id != self._rating_push_counter:
            return

        auto = payload.get("auto", False)

        if payload.get("cancelled"):
            self._set_status(_("Hardcover Sync: push cancelled"), timeout=3000)
            return

        if payload.get("error"):
            self._clear_status()
            if auto:
                self._set_status(
                    _("Hardcover Sync: auto-push failed: {error}").format(
                        error=payload["error"]
                    ),
                    timeout=5000,
                )
                return
            error_dialog(
                self.gui,
                _("Push Rating"),
                _("Failed to push ratings: {error}").format(
                    error=payload["error"]
                ),
            ).exec()
            return

        pushed = payload.get("pushed", 0)
        message = _("Hardcover Sync: pushed {count} ratings").format(count=pushed)
        unresolved = payload.get("unresolved", 0)
        if unresolved:
            message += " " + _("({count} without a Hardcover match)").format(
                count=unresolved
            )
        self._set_status(message, timeout=5000)

        self._mark_error_rows(
            payload.get("error_ids"), payload.get("processed_ids")
        )

        errors = payload.get("errors") or []
        if errors and not auto:
            info_dialog(
                self.gui,
                _("Push Rating"),
                _("Some ratings could not be pushed:") + "\n\n" + "\n".join(errors),
            ).exec()

    def _start_pull_review(self):
        if not getattr(self.gui, "current_db", None):
            return
        db = _db(self.gui)
        book_ids = self._selected_book_ids()
        if not book_ids:
            error_dialog(
                self.gui,
                _("Pull Review"),
                _("Select one or more books to pull reviews for."),
            ).exec()
            return

        books, without_link = self._partition_by_hardcover_link(db, book_ids)
        if not books:
            error_dialog(
                self.gui,
                _("Pull Review"),
                _(
                    "None of the selected books have a Hardcover identifier. "
                    "Download metadata from Hardcover first."
                ),
            ).exec()
            return

        self._review_pull_counter += 1
        pull_id = self._review_pull_counter
        self._set_status(
            _("Hardcover Sync: pulling reviews for {count} books…").format(
                count=len(books)
            )
        )
        Thread(
            target=self._pull_reviews_worker,
            args=(pull_id, books, len(without_link)),
            daemon=True,
        ).start()

    def _pull_reviews_worker(self, pull_id: int, books, skipped: int):
        try:
            self._set_status(_("Hardcover Sync: fetching your reviews…"))
            snapshot = self._lists_client.snapshot_user_reviews()
        except Exception as exc:
            self.reviews_pulled.emit(pull_id, {"error": str(exc)})
            return

        if pull_id != self._review_pull_counter:
            return

        resolved_books = []
        edition_ids = set()
        for book_id, identifiers in books:
            hc_id, slug, edition_id = get_hardcover_lookup(identifiers)
            if hc_id is None and slug is None and edition_id is not None:
                edition_ids.add(edition_id)
            resolved_books.append((book_id, hc_id, slug, edition_id))

        edition_map = {}
        if edition_ids:
            self._set_status(
                _("Hardcover Sync: resolving {count} editions…").format(
                    count=len(edition_ids)
                )
            )
            try:
                edition_map = self._lists_client.resolve_editions(edition_ids)
            except Exception:
                edition_map = {}

        if pull_id != self._review_pull_counter:
            return

        updates: dict[int, str] = {}
        unresolved = 0
        error_ids = []
        for book_id, hc_id, slug, edition_id in resolved_books:
            resolved_id = hc_id
            resolved_slug = slug
            if resolved_id is None and edition_id is not None:
                mapped = edition_map.get(edition_id)
                if mapped:
                    resolved_id, mapped_slug = mapped
                    if not resolved_slug:
                        resolved_slug = mapped_slug
            if resolved_id is None and resolved_slug is None:
                unresolved += 1
                error_ids.append(book_id)
                continue
            review = snapshot.review_for(resolved_id, resolved_slug)
            if review:
                updates[book_id] = review

        self.reviews_pulled.emit(
            pull_id,
            {
                "error": None,
                "updates": updates,
                "unresolved": unresolved,
                "skipped": skipped,
                "error_ids": error_ids,
                "processed_ids": [bid for bid, *_ in resolved_books],
            },
        )

    def _on_reviews_pulled(self, pull_id: int, payload: dict):
        if pull_id != self._review_pull_counter:
            return

        if payload.get("error"):
            self._clear_status()
            error_dialog(
                self.gui,
                _("Pull Review"),
                _("Failed to pull reviews: {error}").format(
                    error=payload["error"]
                ),
            ).exec()
            return

        updates = payload.get("updates") or {}
        if updates and getattr(self.gui, "current_db", None):
            db = _db(self.gui)

            def apply():
                self._mark_self_write(REVIEW_COLUMN_KEY, updates.keys())
                db.set_field(REVIEW_COLUMN_KEY, updates)
                self._refresh_books_in_ui(list(updates))
                if book_id := self._selected_book_id():
                    if book_id in updates:
                        self._refresh_book_details()

            self._run_without_selection_updates(apply)

        self._mark_error_rows(
            payload.get("error_ids"), payload.get("processed_ids")
        )

        message = _("Hardcover Sync: set {count} reviews").format(
            count=len(updates)
        )
        unresolved = payload.get("unresolved", 0) + payload.get("skipped", 0)
        if unresolved:
            message += " " + _("({count} without a Hardcover match)").format(
                count=unresolved
            )
        self._set_status(message, timeout=5000)

    def _start_push_review(self, book_ids=None, auto=False):
        if not getattr(self.gui, "current_db", None):
            return
        db = _db(self.gui)
        # QAction.triggered passes a `checked` bool; only an explicit
        # collection (from auto-push) should override the live selection.
        if not isinstance(book_ids, (list, set, tuple)):
            book_ids = self._selected_book_ids()
        if not book_ids:
            if not auto:
                error_dialog(
                    self.gui,
                    _("Push Review"),
                    _("Select one or more books to push reviews for."),
                ).exec()
            return

        books, without_link = self._partition_by_hardcover_link(db, book_ids)
        if not books:
            if not auto:
                error_dialog(
                    self.gui,
                    _("Push Review"),
                    _(
                        "None of the selected books have a Hardcover identifier. "
                        "Download metadata from Hardcover first."
                    ),
                ).exec()
            return

        reviewed = []
        blank = 0
        for book_id, identifiers in books:
            slate = review_value_to_slate(db.field_for(REVIEW_COLUMN_KEY, book_id))
            if slate is None:
                blank += 1
                continue
            reviewed.append(
                {
                    "identifiers": identifiers,
                    "review_slate": slate,
                    "title": db.field_for("title", book_id),
                    "calibre_id": book_id,
                }
            )

        if not reviewed:
            if not auto:
                info_dialog(
                    self.gui,
                    _("Push Review"),
                    _("None of the selected books have a review to push."),
                ).exec()
            return

        self._review_push_counter += 1
        push_id = self._review_push_counter
        self._set_status(
            _("Hardcover Sync: pushing reviews for {count} books…").format(
                count=len(reviewed)
            )
        )
        Thread(
            target=self._push_reviews_worker,
            args=(push_id, reviewed, len(without_link), blank, auto),
            daemon=True,
        ).start()

    def _push_reviews_worker(
        self, push_id: int, reviewed, skipped: int, blank: int, auto: bool = False
    ):
        try:
            edition_ids = set()
            for entry in reviewed:
                hc_id, slug, edition_id = get_hardcover_lookup(entry["identifiers"])
                entry["hc_id"] = hc_id
                entry["slug"] = slug
                entry["edition_id"] = edition_id
                if hc_id is None and slug is None and edition_id is not None:
                    edition_ids.add(edition_id)

            edition_map = {}
            if edition_ids:
                self._set_status(
                    _("Hardcover Sync: resolving {count} editions…").format(
                        count=len(edition_ids)
                    )
                )
                try:
                    edition_map = self._lists_client.resolve_editions(edition_ids)
                except Exception:
                    edition_map = {}

            if push_id != self._review_push_counter:
                return

            unresolved = 0
            error_ids = []
            processed_ids = [entry["calibre_id"] for entry in reviewed]
            resolved_items = []
            for entry in reviewed:
                book_id = entry["hc_id"]
                if book_id is None and entry["edition_id"] is not None:
                    mapped = edition_map.get(entry["edition_id"])
                    if mapped:
                        book_id = mapped[0]
                if book_id is None and entry["slug"] is not None:
                    book_id = self._lists_client.resolve_book_id(
                        entry["identifiers"]
                    )
                if book_id is None:
                    unresolved += 1
                    error_ids.append(entry["calibre_id"])
                    continue
                entry["resolved_book_id"] = int(book_id)
                resolved_items.append(entry)

            if push_id != self._review_push_counter:
                return

            states = self._lists_client.review_states(
                {entry["resolved_book_id"] for entry in resolved_items}
            )

            items = []
            for entry in resolved_items:
                state = states.get(entry["resolved_book_id"])
                user_book_id = state["id"] if state else None
                # Stamp reviewed_at on inserts and on entries that have none yet,
                # but leave an existing review date untouched on updates.
                set_reviewed_at = state is None or not state.get("reviewed_at")
                items.append(
                    {
                        "book_id": entry["resolved_book_id"],
                        "review_slate": entry["review_slate"],
                        "user_book_id": user_book_id,
                        "set_reviewed_at": set_reviewed_at,
                        "_book": {
                            "title": entry["title"],
                            "calibre_id": entry["calibre_id"],
                        },
                    }
                )

            insert_titles = [
                item["_book"]["title"]
                for item in items
                if item["user_book_id"] is None
            ]
            read_finished_at = None
            if insert_titles:
                choice = self._request_read_date(insert_titles)
                if not choice["accepted"]:
                    self.reviews_pushed.emit(push_id, {"cancelled": True})
                    return
                read_finished_at = choice["finished_at"]

            results = self._lists_client.push_reviews(
                items, read_finished_at=read_finished_at
            )

            pushed = sum(1 for result in results if result["error"] is None)
            errors = [
                f'{result["book"]["title"]}: {result["error"]}'
                for result in results
                if result["error"]
            ]
            error_ids.extend(
                result["book"].get("calibre_id")
                for result in results
                if result["error"]
            )
            payload = {
                "error": None,
                "pushed": pushed,
                "errors": errors,
                "unresolved": unresolved + skipped,
                "blank": blank,
                "auto": auto,
                "error_ids": [bid for bid in error_ids if bid is not None],
                "processed_ids": processed_ids,
            }
        except Exception as exc:
            payload = {"error": str(exc), "auto": auto}

        self.reviews_pushed.emit(push_id, payload)

    def _on_reviews_pushed(self, push_id: int, payload: dict):
        if push_id != self._review_push_counter:
            return

        auto = payload.get("auto", False)

        if payload.get("cancelled"):
            self._set_status(_("Hardcover Sync: push cancelled"), timeout=3000)
            return

        if payload.get("error"):
            self._clear_status()
            if auto:
                self._set_status(
                    _("Hardcover Sync: auto-push failed: {error}").format(
                        error=payload["error"]
                    ),
                    timeout=5000,
                )
                return
            error_dialog(
                self.gui,
                _("Push Review"),
                _("Failed to push reviews: {error}").format(
                    error=payload["error"]
                ),
            ).exec()
            return

        pushed = payload.get("pushed", 0)
        message = _("Hardcover Sync: pushed {count} reviews").format(count=pushed)
        unresolved = payload.get("unresolved", 0)
        if unresolved:
            message += " " + _("({count} without a Hardcover match)").format(
                count=unresolved
            )
        self._set_status(message, timeout=5000)

        self._mark_error_rows(
            payload.get("error_ids"), payload.get("processed_ids")
        )

        errors = payload.get("errors") or []
        if errors and not auto:
            info_dialog(
                self.gui,
                _("Push Review"),
                _("Some reviews could not be pushed:") + "\n\n" + "\n".join(errors),
            ).exec()

    def _start_pull_status(self):
        if not getattr(self.gui, "current_db", None):
            return
        db = _db(self.gui)
        book_ids = self._selected_book_ids()
        if not book_ids:
            error_dialog(
                self.gui,
                _("Pull Status"),
                _("Select one or more books to pull statuses for."),
            ).exec()
            return

        books, without_link = self._partition_by_hardcover_link(db, book_ids)
        if not books:
            error_dialog(
                self.gui,
                _("Pull Status"),
                _(
                    "None of the selected books have a Hardcover identifier. "
                    "Download metadata from Hardcover first."
                ),
            ).exec()
            return

        self._status_pull_counter += 1
        pull_id = self._status_pull_counter
        self._set_status(
            _("Hardcover Sync: pulling statuses for {count} books…").format(
                count=len(books)
            )
        )
        Thread(
            target=self._pull_statuses_worker,
            args=(pull_id, books, len(without_link)),
            daemon=True,
        ).start()

    def _pull_statuses_worker(self, pull_id: int, books, skipped: int):
        try:
            self._set_status(_("Hardcover Sync: fetching your statuses…"))
            snapshot = self._lists_client.snapshot_user_statuses()
        except Exception as exc:
            self.statuses_pulled.emit(pull_id, {"error": str(exc)})
            return

        if pull_id != self._status_pull_counter:
            return

        resolved_books = []
        edition_ids = set()
        for book_id, identifiers in books:
            hc_id, slug, edition_id = get_hardcover_lookup(identifiers)
            if hc_id is None and slug is None and edition_id is not None:
                edition_ids.add(edition_id)
            resolved_books.append((book_id, hc_id, slug, edition_id))

        edition_map = {}
        if edition_ids:
            self._set_status(
                _("Hardcover Sync: resolving {count} editions…").format(
                    count=len(edition_ids)
                )
            )
            try:
                edition_map = self._lists_client.resolve_editions(edition_ids)
            except Exception:
                edition_map = {}

        if pull_id != self._status_pull_counter:
            return

        updates: dict[int, str] = {}
        unresolved = 0
        error_ids = []
        for book_id, hc_id, slug, edition_id in resolved_books:
            resolved_id = hc_id
            resolved_slug = slug
            if resolved_id is None and edition_id is not None:
                mapped = edition_map.get(edition_id)
                if mapped:
                    resolved_id, mapped_slug = mapped
                    if not resolved_slug:
                        resolved_slug = mapped_slug
            if resolved_id is None and resolved_slug is None:
                unresolved += 1
                error_ids.append(book_id)
                continue
            name = hardcover_status_to_calibre(
                snapshot.status_for(resolved_id, resolved_slug)
            )
            if name is not None:
                updates[book_id] = name

        self.statuses_pulled.emit(
            pull_id,
            {
                "error": None,
                "updates": updates,
                "unresolved": unresolved,
                "skipped": skipped,
                "error_ids": error_ids,
                "processed_ids": [bid for bid, *_ in resolved_books],
            },
        )

    def _on_statuses_pulled(self, pull_id: int, payload: dict):
        if pull_id != self._status_pull_counter:
            return

        if payload.get("error"):
            self._clear_status()
            error_dialog(
                self.gui,
                _("Pull Status"),
                _("Failed to pull statuses: {error}").format(
                    error=payload["error"]
                ),
            ).exec()
            return

        updates = payload.get("updates") or {}
        if updates and getattr(self.gui, "current_db", None):
            db = _db(self.gui)

            def apply():
                self._mark_self_write(STATUS_COLUMN_KEY, updates.keys())
                db.set_field(STATUS_COLUMN_KEY, updates)
                self._refresh_books_in_ui(list(updates))
                if book_id := self._selected_book_id():
                    if book_id in updates:
                        self._refresh_book_details()

            self._run_without_selection_updates(apply)

        self._mark_error_rows(
            payload.get("error_ids"), payload.get("processed_ids")
        )

        message = _("Hardcover Sync: set {count} statuses").format(
            count=len(updates)
        )
        unresolved = payload.get("unresolved", 0) + payload.get("skipped", 0)
        if unresolved:
            message += " " + _("({count} without a Hardcover match)").format(
                count=unresolved
            )
        self._set_status(message, timeout=5000)

    def _start_push_status(self, book_ids=None, auto=False):
        if not getattr(self.gui, "current_db", None):
            return
        db = _db(self.gui)
        # QAction.triggered passes a `checked` bool; only an explicit
        # collection (from auto-push) should override the live selection.
        if not isinstance(book_ids, (list, set, tuple)):
            book_ids = self._selected_book_ids()
        if not book_ids:
            if not auto:
                error_dialog(
                    self.gui,
                    _("Push Status"),
                    _("Select one or more books to push statuses for."),
                ).exec()
            return

        books, without_link = self._partition_by_hardcover_link(db, book_ids)
        if not books:
            if not auto:
                error_dialog(
                    self.gui,
                    _("Push Status"),
                    _(
                        "None of the selected books have a Hardcover identifier. "
                        "Download metadata from Hardcover first."
                    ),
                ).exec()
            return

        chosen = []
        blank = 0
        for book_id, identifiers in books:
            status_id = calibre_status_to_hardcover(
                db.field_for(STATUS_COLUMN_KEY, book_id)
            )
            if status_id is None:
                blank += 1
                continue
            chosen.append(
                {
                    "identifiers": identifiers,
                    "status_id": status_id,
                    "title": db.field_for("title", book_id),
                    "calibre_id": book_id,
                }
            )

        if not chosen:
            if not auto:
                info_dialog(
                    self.gui,
                    _("Push Status"),
                    _("None of the selected books have a status to push."),
                ).exec()
            return

        self._status_push_counter += 1
        push_id = self._status_push_counter
        self._set_status(
            _("Hardcover Sync: pushing statuses for {count} books…").format(
                count=len(chosen)
            )
        )
        Thread(
            target=self._push_statuses_worker,
            args=(push_id, chosen, len(without_link), blank, auto),
            daemon=True,
        ).start()

    def _push_statuses_worker(
        self, push_id: int, chosen, skipped: int, blank: int, auto: bool = False
    ):
        try:
            edition_ids = set()
            for entry in chosen:
                hc_id, slug, edition_id = get_hardcover_lookup(entry["identifiers"])
                entry["hc_id"] = hc_id
                entry["slug"] = slug
                entry["edition_id"] = edition_id
                if hc_id is None and slug is None and edition_id is not None:
                    edition_ids.add(edition_id)

            edition_map = {}
            if edition_ids:
                self._set_status(
                    _("Hardcover Sync: resolving {count} editions…").format(
                        count=len(edition_ids)
                    )
                )
                try:
                    edition_map = self._lists_client.resolve_editions(edition_ids)
                except Exception:
                    edition_map = {}

            if push_id != self._status_push_counter:
                return

            unresolved = 0
            error_ids = []
            processed_ids = [entry["calibre_id"] for entry in chosen]
            resolved_items = []
            for entry in chosen:
                book_id = entry["hc_id"]
                if book_id is None and entry["edition_id"] is not None:
                    mapped = edition_map.get(entry["edition_id"])
                    if mapped:
                        book_id = mapped[0]
                if book_id is None and entry["slug"] is not None:
                    book_id = self._lists_client.resolve_book_id(
                        entry["identifiers"]
                    )
                if book_id is None:
                    unresolved += 1
                    error_ids.append(entry["calibre_id"])
                    continue
                entry["resolved_book_id"] = int(book_id)
                resolved_items.append(entry)

            if push_id != self._status_push_counter:
                return

            existing = self._lists_client.user_book_ids(
                {entry["resolved_book_id"] for entry in resolved_items}
            )

            items = [
                {
                    "book_id": entry["resolved_book_id"],
                    "status_id": entry["status_id"],
                    "user_book_id": existing.get(entry["resolved_book_id"]),
                    "_book": {
                        "title": entry["title"],
                        "calibre_id": entry["calibre_id"],
                    },
                }
                for entry in resolved_items
            ]

            # Only a fresh "Read" insert creates a read date worth choosing.
            read_insert_titles = [
                item["_book"]["title"]
                for item in items
                if item["user_book_id"] is None
                and item["status_id"] == READ_STATUS_ID
            ]
            read_finished_at = None
            if read_insert_titles:
                choice = self._request_read_date(read_insert_titles)
                if not choice["accepted"]:
                    self.statuses_pushed.emit(push_id, {"cancelled": True})
                    return
                read_finished_at = choice["finished_at"]

            results = self._lists_client.push_statuses(
                items, read_finished_at=read_finished_at
            )

            pushed = sum(1 for result in results if result["error"] is None)
            errors = [
                f'{result["book"]["title"]}: {result["error"]}'
                for result in results
                if result["error"]
            ]
            error_ids.extend(
                result["book"].get("calibre_id")
                for result in results
                if result["error"]
            )
            payload = {
                "error": None,
                "pushed": pushed,
                "errors": errors,
                "unresolved": unresolved + skipped,
                "blank": blank,
                "auto": auto,
                "error_ids": [bid for bid in error_ids if bid is not None],
                "processed_ids": processed_ids,
            }
        except Exception as exc:
            payload = {"error": str(exc), "auto": auto}

        self.statuses_pushed.emit(push_id, payload)

    def _on_statuses_pushed(self, push_id: int, payload: dict):
        if push_id != self._status_push_counter:
            return

        auto = payload.get("auto", False)

        if payload.get("cancelled"):
            self._set_status(_("Hardcover Sync: push cancelled"), timeout=3000)
            return

        if payload.get("error"):
            self._clear_status()
            if auto:
                self._set_status(
                    _("Hardcover Sync: auto-push failed: {error}").format(
                        error=payload["error"]
                    ),
                    timeout=5000,
                )
                return
            error_dialog(
                self.gui,
                _("Push Status"),
                _("Failed to push statuses: {error}").format(
                    error=payload["error"]
                ),
            ).exec()
            return

        pushed = payload.get("pushed", 0)
        message = _("Hardcover Sync: pushed {count} statuses").format(count=pushed)
        unresolved = payload.get("unresolved", 0)
        if unresolved:
            message += " " + _("({count} without a Hardcover match)").format(
                count=unresolved
            )
        self._set_status(message, timeout=5000)

        self._mark_error_rows(
            payload.get("error_ids"), payload.get("processed_ids")
        )

        errors = payload.get("errors") or []
        if errors and not auto:
            info_dialog(
                self.gui,
                _("Push Status"),
                _("Some statuses could not be pushed:") + "\n\n" + "\n".join(errors),
            ).exec()

    def _journal_config(self, kind: str):
        if kind == "quote":
            return QUOTES_COLUMN_KEY, _("Quotes")
        return NOTES_COLUMN_KEY, _("Notes")

    def _start_pull_journal(self, kind: str):
        if not getattr(self.gui, "current_db", None):
            return
        db = _db(self.gui)
        _column_key, label = self._journal_config(kind)
        book_ids = self._selected_book_ids()
        if not book_ids:
            error_dialog(
                self.gui,
                _("Pull {label}").format(label=label),
                _("Select one or more books to pull from Hardcover."),
            ).exec()
            return

        books, without_link = self._partition_by_hardcover_link(db, book_ids)
        if not books:
            error_dialog(
                self.gui,
                _("Pull {label}").format(label=label),
                _(
                    "None of the selected books have a Hardcover identifier. "
                    "Download metadata from Hardcover first."
                ),
            ).exec()
            return

        self._journal_pull_counter[kind] += 1
        pull_id = self._journal_pull_counter[kind]
        self._set_status(
            _("Hardcover Sync: pulling {label} for {count} books…").format(
                label=label.lower(), count=len(books)
            )
        )
        Thread(
            target=self._pull_journal_worker,
            args=(kind, pull_id, books, len(without_link)),
            daemon=True,
        ).start()

    def _pull_journal_worker(self, kind: str, pull_id: int, books, skipped: int):
        try:
            self._set_status(_("Hardcover Sync: fetching your entries…"))
            snapshot = self._lists_client.snapshot_user_journals()
        except Exception as exc:
            self.journals_pulled.emit(
                pull_id, {"kind": kind, "error": str(exc)}
            )
            return

        if pull_id != self._journal_pull_counter[kind]:
            return

        resolved_books = []
        edition_ids = set()
        for book_id, identifiers in books:
            hc_id, slug, edition_id = get_hardcover_lookup(identifiers)
            if hc_id is None and slug is None and edition_id is not None:
                edition_ids.add(edition_id)
            resolved_books.append((book_id, hc_id, slug, edition_id))

        edition_map = {}
        if edition_ids:
            try:
                edition_map = self._lists_client.resolve_editions(edition_ids)
            except Exception:
                edition_map = {}

        if pull_id != self._journal_pull_counter[kind]:
            return

        updates: dict[int, str] = {}
        unresolved = 0
        error_ids = []
        for book_id, hc_id, slug, edition_id in resolved_books:
            resolved_id = hc_id
            resolved_slug = slug
            if resolved_id is None and edition_id is not None:
                mapped = edition_map.get(edition_id)
                if mapped:
                    resolved_id, mapped_slug = mapped
                    if not resolved_slug:
                        resolved_slug = mapped_slug
            if resolved_id is None and resolved_slug is None:
                unresolved += 1
                error_ids.append(book_id)
                continue
            entries = snapshot.entries_for(resolved_id, resolved_slug, kind)
            if not entries:
                continue
            if kind == "quote":
                text = join_journal_entries(
                    format_quote_entry(e["entry"], e["page"]) for e in entries
                )
            else:
                text = join_journal_entries(entries)
            if text:
                updates[book_id] = text

        self.journals_pulled.emit(
            pull_id,
            {
                "kind": kind,
                "error": None,
                "updates": updates,
                "unresolved": unresolved,
                "skipped": skipped,
                "error_ids": error_ids,
                "processed_ids": [bid for bid, *_ in resolved_books],
            },
        )

    def _on_journals_pulled(self, pull_id: int, payload: dict):
        kind = payload.get("kind")
        if pull_id != self._journal_pull_counter.get(kind):
            return
        column_key, label = self._journal_config(kind)

        if payload.get("error"):
            self._clear_status()
            error_dialog(
                self.gui,
                _("Pull {label}").format(label=label),
                _("Failed to pull from Hardcover: {error}").format(
                    error=payload["error"]
                ),
            ).exec()
            return

        updates = payload.get("updates") or {}
        if updates and getattr(self.gui, "current_db", None):
            db = _db(self.gui)

            def apply():
                self._mark_self_write(column_key, updates.keys())
                db.set_field(column_key, updates)
                self._refresh_books_in_ui(list(updates))
                if book_id := self._selected_book_id():
                    if book_id in updates:
                        self._refresh_book_details()

            self._run_without_selection_updates(apply)

        self._mark_error_rows(
            payload.get("error_ids"), payload.get("processed_ids")
        )

        message = _("Hardcover Sync: set {label} on {count} books").format(
            label=label.lower(), count=len(updates)
        )
        unresolved = payload.get("unresolved", 0) + payload.get("skipped", 0)
        if unresolved:
            message += " " + _("({count} without a Hardcover match)").format(
                count=unresolved
            )
        self._set_status(message, timeout=5000)

    def _start_push_journal(self, kind: str, book_ids=None, auto=False):
        if not getattr(self.gui, "current_db", None):
            return
        db = _db(self.gui)
        column_key, label = self._journal_config(kind)
        # QAction.triggered passes a `checked` bool; only an explicit
        # collection (from auto-push) should override the live selection.
        if not isinstance(book_ids, (list, set, tuple)):
            book_ids = self._selected_book_ids()
        if not book_ids:
            if not auto:
                error_dialog(
                    self.gui,
                    _("Push {label}").format(label=label),
                    _("Select one or more books to push to Hardcover."),
                ).exec()
            return

        books, without_link = self._partition_by_hardcover_link(db, book_ids)
        if not books:
            if not auto:
                error_dialog(
                    self.gui,
                    _("Push {label}").format(label=label),
                    _(
                        "None of the selected books have a Hardcover identifier. "
                        "Download metadata from Hardcover first."
                    ),
                ).exec()
            return

        chosen = []
        blank = 0
        for book_id, identifiers in books:
            entries = split_journal_entries(db.field_for(column_key, book_id))
            if not entries:
                # A blank column is treated as "no change" rather than a request
                # to delete every entry on Hardcover.
                blank += 1
                continue
            if kind == "quote":
                desired = [parse_quote_page(text) for text in entries]
            else:
                desired = [(text, None) for text in entries]
            chosen.append(
                {
                    "identifiers": identifiers,
                    "desired": desired,
                    "title": db.field_for("title", book_id),
                    "calibre_id": book_id,
                }
            )

        if not chosen:
            if not auto:
                info_dialog(
                    self.gui,
                    _("Push {label}").format(label=label),
                    _("None of the selected books have {label} to push.").format(
                        label=label.lower()
                    ),
                ).exec()
            return

        self._journal_push_counter[kind] += 1
        push_id = self._journal_push_counter[kind]
        self._set_status(
            _("Hardcover Sync: pushing {label} for {count} books…").format(
                label=label.lower(), count=len(chosen)
            )
        )
        Thread(
            target=self._push_journal_worker,
            args=(kind, push_id, chosen, len(without_link), blank, auto),
            daemon=True,
        ).start()

    def _push_journal_worker(
        self, kind: str, push_id: int, chosen, skipped: int, blank: int,
        auto: bool = False,
    ):
        try:
            edition_ids = set()
            for entry in chosen:
                hc_id, slug, edition_id = get_hardcover_lookup(entry["identifiers"])
                entry["hc_id"] = hc_id
                entry["slug"] = slug
                entry["edition_id"] = edition_id
                if hc_id is None and slug is None and edition_id is not None:
                    edition_ids.add(edition_id)

            edition_map = {}
            if edition_ids:
                try:
                    edition_map = self._lists_client.resolve_editions(edition_ids)
                except Exception:
                    edition_map = {}

            if push_id != self._journal_push_counter[kind]:
                return

            unresolved = 0
            error_ids = []
            processed_ids = [entry["calibre_id"] for entry in chosen]
            items = []
            for entry in chosen:
                book_id = entry["hc_id"]
                if book_id is None and entry["edition_id"] is not None:
                    mapped = edition_map.get(entry["edition_id"])
                    if mapped:
                        book_id = mapped[0]
                if book_id is None and entry["slug"] is not None:
                    book_id = self._lists_client.resolve_book_id(
                        entry["identifiers"]
                    )
                if book_id is None:
                    unresolved += 1
                    error_ids.append(entry["calibre_id"])
                    continue
                items.append(
                    {
                        "book_id": int(book_id),
                        "desired": entry["desired"],
                        "_book": {
                            "title": entry["title"],
                            "calibre_id": entry["calibre_id"],
                        },
                    }
                )

            if push_id != self._journal_push_counter[kind]:
                return

            results = self._lists_client.push_journals(kind, items)
            inserted = sum(r["inserted"] for r in results)
            deleted = sum(r["deleted"] for r in results)
            errors = [
                f'{r["book"]["title"]}: {r["error"]}'
                for r in results
                if r["error"]
            ]
            error_ids.extend(
                r["book"].get("calibre_id") for r in results if r["error"]
            )
            payload = {
                "kind": kind,
                "error": None,
                "inserted": inserted,
                "deleted": deleted,
                "errors": errors,
                "unresolved": unresolved + skipped,
                "blank": blank,
                "auto": auto,
                "error_ids": [bid for bid in error_ids if bid is not None],
                "processed_ids": processed_ids,
            }
        except Exception as exc:
            payload = {"kind": kind, "error": str(exc), "auto": auto}

        self.journals_pushed.emit(push_id, payload)

    def _on_journals_pushed(self, push_id: int, payload: dict):
        kind = payload.get("kind")
        if push_id != self._journal_push_counter.get(kind):
            return
        _column_key, label = self._journal_config(kind)
        auto = payload.get("auto", False)

        if payload.get("error"):
            self._clear_status()
            if auto:
                self._set_status(
                    _("Hardcover Sync: auto-push failed: {error}").format(
                        error=payload["error"]
                    ),
                    timeout=5000,
                )
                return
            error_dialog(
                self.gui,
                _("Push {label}").format(label=label),
                _("Failed to push to Hardcover: {error}").format(
                    error=payload["error"]
                ),
            ).exec()
            return

        inserted = payload.get("inserted", 0)
        deleted = payload.get("deleted", 0)
        message = _(
            "Hardcover Sync: {label} added {inserted}, removed {deleted}"
        ).format(label=label.lower(), inserted=inserted, deleted=deleted)
        unresolved = payload.get("unresolved", 0)
        if unresolved:
            message += " " + _("({count} without a Hardcover match)").format(
                count=unresolved
            )
        self._set_status(message, timeout=5000)

        self._mark_error_rows(
            payload.get("error_ids"), payload.get("processed_ids")
        )

        errors = payload.get("errors") or []
        if errors and not auto:
            info_dialog(
                self.gui,
                _("Push {label}").format(label=label),
                _("Some entries could not be pushed:") + "\n\n" + "\n".join(errors),
            ).exec()

    def _start_pull_tags(self):
        if not getattr(self.gui, "current_db", None):
            return
        db = _db(self.gui)
        book_ids = self._selected_book_ids()
        if not book_ids:
            error_dialog(
                self.gui,
                _("Pull Tags"),
                _("Select one or more books to pull tags for."),
            ).exec()
            return

        books, without_link = self._partition_by_hardcover_link(db, book_ids)
        if not books:
            error_dialog(
                self.gui,
                _("Pull Tags"),
                _(
                    "None of the selected books have a Hardcover identifier. "
                    "Download metadata from Hardcover first."
                ),
            ).exec()
            return

        self._tag_pull_counter += 1
        pull_id = self._tag_pull_counter
        self._set_status(
            _("Hardcover Sync: pulling tags for {count} books…").format(
                count=len(books)
            )
        )
        Thread(
            target=self._pull_tags_worker,
            args=(pull_id, books, len(without_link)),
            daemon=True,
        ).start()

    def _pull_tags_worker(self, pull_id: int, books, skipped: int):
        try:
            self._set_status(_("Hardcover Sync: fetching your tags…"))
            snapshot = self._lists_client.snapshot_user_tags()
        except Exception as exc:
            self.tags_pulled.emit(pull_id, {"error": str(exc)})
            return

        if pull_id != self._tag_pull_counter:
            return

        resolved_books = []
        edition_ids = set()
        for book_id, identifiers in books:
            hc_id, slug, edition_id = get_hardcover_lookup(identifiers)
            if hc_id is None and slug is None and edition_id is not None:
                edition_ids.add(edition_id)
            resolved_books.append((book_id, hc_id, slug, edition_id))

        edition_map = {}
        if edition_ids:
            self._set_status(
                _("Hardcover Sync: resolving {count} editions…").format(
                    count=len(edition_ids)
                )
            )
            try:
                edition_map = self._lists_client.resolve_editions(edition_ids)
            except Exception:
                edition_map = {}

        if pull_id != self._tag_pull_counter:
            return

        updates: dict[int, list] = {}
        unresolved = 0
        error_ids = []
        for book_id, hc_id, slug, edition_id in resolved_books:
            resolved_id = hc_id
            resolved_slug = slug
            if resolved_id is None and edition_id is not None:
                mapped = edition_map.get(edition_id)
                if mapped:
                    resolved_id, mapped_slug = mapped
                    if not resolved_slug:
                        resolved_slug = mapped_slug
            if resolved_id is None and resolved_slug is None:
                unresolved += 1
                error_ids.append(book_id)
                continue
            # Replace semantics: an empty list clears Calibre's tags for books
            # with no free-form tags on Hardcover.
            updates[book_id] = list(
                snapshot.tags_for(resolved_id, resolved_slug)
            )

        self.tags_pulled.emit(
            pull_id,
            {
                "error": None,
                "updates": updates,
                "unresolved": unresolved,
                "skipped": skipped,
                "error_ids": error_ids,
                "processed_ids": [bid for bid, *_ in resolved_books],
            },
        )

    def _on_tags_pulled(self, pull_id: int, payload: dict):
        if pull_id != self._tag_pull_counter:
            return

        if payload.get("error"):
            self._clear_status()
            error_dialog(
                self.gui,
                _("Pull Tags"),
                _("Failed to pull tags: {error}").format(error=payload["error"]),
            ).exec()
            return

        updates = payload.get("updates") or {}
        if updates and getattr(self.gui, "current_db", None):
            db = _db(self.gui)

            def apply():
                self._mark_self_write("tags", updates.keys())
                db.set_field(
                    "tags",
                    {bid: tuple(vals) for bid, vals in updates.items()},
                )
                self._refresh_books_in_ui(list(updates))

            self._run_without_selection_updates(apply)

        self._mark_error_rows(
            payload.get("error_ids"), payload.get("processed_ids")
        )

        count = sum(1 for vals in updates.values() if vals)
        message = _("Hardcover Sync: set tags on {count} books").format(
            count=count
        )
        unresolved = payload.get("unresolved", 0) + payload.get("skipped", 0)
        if unresolved:
            message += " " + _("({count} without a Hardcover match)").format(
                count=unresolved
            )
        self._set_status(message, timeout=5000)

    def _start_push_tags(self, book_ids=None, auto=False):
        if not getattr(self.gui, "current_db", None):
            return
        db = _db(self.gui)
        # QAction.triggered passes a `checked` bool; only an explicit
        # collection (from auto-push) should override the live selection.
        if not isinstance(book_ids, (list, set, tuple)):
            book_ids = self._selected_book_ids()
        if not book_ids:
            if not auto:
                error_dialog(
                    self.gui,
                    _("Push Tags"),
                    _("Select one or more books to push tags for."),
                ).exec()
            return

        books, without_link = self._partition_by_hardcover_link(db, book_ids)
        if not books:
            if not auto:
                error_dialog(
                    self.gui,
                    _("Push Tags"),
                    _(
                        "None of the selected books have a Hardcover identifier. "
                        "Download metadata from Hardcover first."
                    ),
                ).exec()
            return

        entries = []
        for book_id, identifiers in books:
            raw = db.field_for("tags", book_id)
            tags = [str(t) for t in (raw or ()) if str(t).strip()]
            entries.append(
                {
                    "identifiers": identifiers,
                    "tags": tags,
                    "title": db.field_for("title", book_id),
                    "calibre_id": book_id,
                }
            )

        self._tag_push_counter += 1
        push_id = self._tag_push_counter
        self._set_status(
            _("Hardcover Sync: pushing tags for {count} books…").format(
                count=len(entries)
            )
        )
        Thread(
            target=self._push_tags_worker,
            args=(push_id, entries, len(without_link), auto),
            daemon=True,
        ).start()

    def _push_tags_worker(self, push_id: int, entries, skipped: int, auto=False):
        try:
            edition_ids = set()
            for entry in entries:
                hc_id, slug, edition_id = get_hardcover_lookup(entry["identifiers"])
                entry["hc_id"] = hc_id
                entry["slug"] = slug
                entry["edition_id"] = edition_id
                if hc_id is None and slug is None and edition_id is not None:
                    edition_ids.add(edition_id)

            edition_map = {}
            if edition_ids:
                self._set_status(
                    _("Hardcover Sync: resolving {count} editions…").format(
                        count=len(edition_ids)
                    )
                )
                try:
                    edition_map = self._lists_client.resolve_editions(edition_ids)
                except Exception:
                    edition_map = {}

            if push_id != self._tag_push_counter:
                return

            unresolved = 0
            error_ids = []
            processed_ids = [entry["calibre_id"] for entry in entries]
            items = []
            for entry in entries:
                book_id = entry["hc_id"]
                if book_id is None and entry["edition_id"] is not None:
                    mapped = edition_map.get(entry["edition_id"])
                    if mapped:
                        book_id = mapped[0]
                if book_id is None and entry["slug"] is not None:
                    book_id = self._lists_client.resolve_book_id(
                        entry["identifiers"]
                    )
                if book_id is None:
                    unresolved += 1
                    error_ids.append(entry["calibre_id"])
                    continue
                items.append(
                    {
                        "book_id": int(book_id),
                        "tags": entry["tags"],
                        "_book": {
                            "title": entry["title"],
                            "calibre_id": entry["calibre_id"],
                        },
                    }
                )

            if push_id != self._tag_push_counter:
                return

            results = self._lists_client.push_tags(items)
            pushed = sum(1 for result in results if result["error"] is None)
            errors = [
                f'{result["book"]["title"]}: {result["error"]}'
                for result in results
                if result["error"]
            ]
            error_ids.extend(
                result["book"].get("calibre_id")
                for result in results
                if result["error"]
            )
            payload = {
                "error": None,
                "pushed": pushed,
                "errors": errors,
                "unresolved": unresolved + skipped,
                "auto": auto,
                "error_ids": [bid for bid in error_ids if bid is not None],
                "processed_ids": processed_ids,
            }
        except Exception as exc:
            payload = {"error": str(exc), "auto": auto}

        self.tags_pushed.emit(push_id, payload)

    def _on_tags_pushed(self, push_id: int, payload: dict):
        if push_id != self._tag_push_counter:
            return

        auto = payload.get("auto", False)

        if payload.get("error"):
            self._clear_status()
            if auto:
                self._set_status(
                    _("Hardcover Sync: auto-push failed: {error}").format(
                        error=payload["error"]
                    ),
                    timeout=5000,
                )
                return
            error_dialog(
                self.gui,
                _("Push Tags"),
                _("Failed to push tags: {error}").format(error=payload["error"]),
            ).exec()
            return

        pushed = payload.get("pushed", 0)
        message = _("Hardcover Sync: pushed tags for {count} books").format(
            count=pushed
        )
        unresolved = payload.get("unresolved", 0)
        if unresolved:
            message += " " + _("({count} without a Hardcover match)").format(
                count=unresolved
            )
        self._set_status(message, timeout=5000)

        self._mark_error_rows(
            payload.get("error_ids"), payload.get("processed_ids")
        )

        errors = payload.get("errors") or []
        if errors and not auto:
            info_dialog(
                self.gui,
                _("Push Tags"),
                _("Some tags could not be pushed:") + "\n\n" + "\n".join(errors),
            ).exec()

    def _start_cull_isbn(self):
        book_ids = list(self.gui.library_view.get_selected_ids())
        if not book_ids:
            error_dialog(
                self.gui,
                _("Cull ISBN"),
                _("Select a book to search for ISBNs."),
            ).exec()
            return
        if len(book_ids) != 1:
            error_dialog(
                self.gui,
                _("Cull ISBN"),
                _("Select exactly one book to search for ISBNs."),
            ).exec()
            return

        if not getattr(self.gui, "current_db", None):
            return

        book_id = book_ids[0]
        title = self.gui.current_db.new_api.field_for("title", book_id)
        self._isbn_scan_counter += 1
        scan_id = self._isbn_scan_counter

        if hasattr(self.gui, "status_bar"):
            self.gui.status_bar.show_message(
                _("Cull ISBN: scanning “{title}”…").format(title=title),
                show_notification=False,
            )

        Thread(
            target=self._scan_book_for_isbns,
            args=(scan_id, book_id),
            daemon=True,
        ).start()

    def _scan_book_for_isbns(self, scan_id: int, book_id: int):
        from calibre_plugins.hardcover_sync.cull_isbn.isbn_scan import (
            find_isbns_for_book,
        )

        try:
            isbns = find_isbns_for_book(self.gui.current_db.new_api, book_id)
        except Exception as exc:
            isbns = None
            error = str(exc)
        else:
            error = None

        self.isbns_found.emit(scan_id, book_id, (isbns, error))

    def _on_isbns_found(self, scan_id: int, book_id: int, payload):
        if scan_id != self._isbn_scan_counter:
            return

        isbns, error = payload
        if hasattr(self.gui, "status_bar"):
            self.gui.status_bar.clear_message()

        if error is not None:
            error_dialog(
                self.gui,
                _("Cull ISBN"),
                _("Failed to scan the book for ISBNs: {error}").format(error=error),
            ).exec()
            return

        if not isbns:
            info_dialog(
                self.gui,
                _("Cull ISBN"),
                _("No ISBN-10 or ISBN-13 numbers were found in the book text."),
            ).exec()
            return

        dialog = IsbnSelectDialog(
            self.gui,
            isbns,
            _("Select an ISBN to save on this book:"),
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return

        isbn = dialog.selected_isbn()
        if not isbn:
            return
        db = self.gui.current_db.new_api
        identifiers = dict(db.field_for("identifiers", book_id))
        identifiers["isbn"] = isbn
        db.set_field("identifiers", {book_id: identifiers})

        title = db.field_for("title", book_id)
        info_dialog(
            self.gui,
            _("Cull ISBN"),
            _('Saved ISBN {isbn} on “{title}”.').format(isbn=isbn, title=title),
        ).exec()

    def _create_new_list(self):
        name, ok = QInputDialog.getText(
            self.gui,
            _("Create Hardcover List"),
            _("List name:"),
        )
        if not ok:
            return

        name = name.strip()
        if not name:
            error_dialog(
                self.gui,
                _("Hardcover Sync"),
                _("List name cannot be empty."),
            ).exec()
            return

        Thread(target=self._do_create_list, args=(name,), daemon=True).start()

    def _do_create_list(self, name: str):
        try:
            new_list = self._lists_client.create_list(name)
        except Exception as exc:
            self.list_operation_done.emit(
                _("Hardcover Sync"),
                _("Failed to create list: {error}").format(error=exc),
            )
            return

        self.list_operation_done.emit(
            _("Hardcover Sync"),
            _('Created list "{name}".').format(name=new_list["name"]),
        )
        self._refresh_user_lists()

    def _selected_hardcover_books(self):
        db = _db(self.gui)
        book_ids = self.gui.library_view.get_selected_ids()
        books = []
        skipped = 0
        for book_id in book_ids:
            identifiers = dict(db.field_for("identifiers", book_id))
            hardcover_id = self._lists_client.resolve_book_id(identifiers)
            if hardcover_id is None:
                skipped += 1
                continue
            books.append(
                {
                    "book_id": book_id,
                    "hardcover_id": hardcover_id,
                    "edition_id": get_hardcover_edition_id(identifiers),
                    "title": db.field_for("title", book_id),
                }
            )
        return books, skipped

    def _add_to_list(self, list_id: int, user_list: dict):
        books, skipped = self._selected_hardcover_books()
        if not books:
            error_dialog(
                self.gui,
                _("Hardcover Sync"),
                _(
                    "None of the selected books have a Hardcover identifier. "
                    "Download metadata from Hardcover first."
                ),
            ).exec()
            return

        list_name = user_list["name"]
        Thread(
            target=self._do_add_to_list,
            args=(list_id, list_name, books, skipped),
            daemon=True,
        ).start()

    def _remove_from_list(self, list_id: int, user_list: dict):
        books, skipped = self._selected_hardcover_books()
        if not books:
            error_dialog(
                self.gui,
                _("Hardcover Sync"),
                _(
                    "None of the selected books have a Hardcover identifier. "
                    "Download metadata from Hardcover first."
                ),
            ).exec()
            return

        list_name = user_list["name"]
        Thread(
            target=self._do_remove_from_list,
            args=(list_id, list_name, books, skipped),
            daemon=True,
        ).start()

    def _do_add_to_list(
        self, list_id: int, list_name: str, books: list[dict], skipped: int
    ):
        payload = [
            {
                "book_id": book["hardcover_id"],
                "edition_id": book["edition_id"],
                "_book": book,
            }
            for book in books
        ]
        results = self._lists_client.add_books_to_list(list_id, payload)

        added = 0
        errors = []
        updated_ids = []
        for result in results:
            book = result["book"]["_book"]
            if result["error"] is None:
                added += 1
                updated_ids.append(book["book_id"])
            else:
                errors.append(f'{book["title"]}: {result["error"]}')

        if added == 1:
            message = _("Added 1 book to {list_name}.").format(list_name=list_name)
        else:
            message = _("Added {count} books to {list_name}.").format(
                count=added, list_name=list_name
            )
        if skipped == 1:
            message += " " + _("Skipped 1 book without a Hardcover identifier.")
        elif skipped:
            message += " " + _(
                "Skipped {count} books without a Hardcover identifier."
            ).format(count=skipped)
        if errors:
            message += "\n\n" + "\n".join(errors)
        self.list_operation_done.emit(_("Hardcover Sync"), message)
        if updated_ids:
            self.membership_delta.emit(updated_ids, list_name, "")

    def _do_remove_from_list(
        self, list_id: int, list_name: str, books: list[dict], skipped: int
    ):
        payload = [
            {"book_id": book["hardcover_id"], "_book": book} for book in books
        ]
        results = self._lists_client.remove_books_from_list(list_id, payload)

        removed = 0
        not_on_list = 0
        errors = []
        updated_ids = []
        for result in results:
            book = result["book"]["_book"]
            if result["error"]:
                errors.append(f'{book["title"]}: {result["error"]}')
            elif result["not_on_list"]:
                not_on_list += 1
            elif result["removed"] > 0:
                removed += 1
                updated_ids.append(book["book_id"])

        if removed == 1:
            message = _("Removed 1 book from {list_name}.").format(list_name=list_name)
        else:
            message = _("Removed {count} books from {list_name}.").format(
                count=removed, list_name=list_name
            )
        if not_on_list == 1:
            message += " " + _("1 book was not on the list.")
        elif not_on_list:
            message += " " + _("{count} books were not on the list.").format(
                count=not_on_list
            )
        if skipped == 1:
            message += " " + _("Skipped 1 book without a Hardcover identifier.")
        elif skipped:
            message += " " + _(
                "Skipped {count} books without a Hardcover identifier."
            ).format(count=skipped)
        if errors:
            message += "\n\n" + "\n".join(errors)
        self.list_operation_done.emit(_("Hardcover Sync"), message)
        if updated_ids:
            self.membership_delta.emit(updated_ids, "", list_name)

    @staticmethod
    def _current_list_names(value) -> set[str]:
        text = normalize_lists_display(value).strip()
        if (
            not text
            or text in SPECIAL_COLUMN_VALUES
            or text.startswith("Hardcover error:")
        ):
            return set()
        return {part.strip() for part in text.split(",") if part.strip()}

    def _on_membership_delta(self, book_ids, add_name: str, remove_name: str):
        if not getattr(self.gui, "current_db", None) or not book_ids:
            return
        db = _db(self.gui)
        updates = {}
        for book_id in book_ids:
            if not db.has_id(book_id):
                continue
            current = db.field_for(COLUMN_KEY, book_id)
            names = self._current_list_names(current)
            if add_name:
                names.add(add_name)
            if remove_name:
                names.discard(remove_name)
            text = ", ".join(sorted(names)) if names else NOT_ON_LISTS
            value = lists_text_to_field_value(text)
            identifiers = dict(db.field_for("identifiers", book_id))
            resolved_id, _slug, _edition = get_hardcover_lookup(identifiers)
            save_lists_cache_entry(identifiers, text, resolved_book_id=resolved_id)
            if not column_values_equal(current, value):
                updates[book_id] = value
        if not updates:
            return

        def apply():
            db.set_field(COLUMN_KEY, updates)
            self._refresh_books_in_ui(list(updates))

        self._run_without_selection_updates(apply)

    def _show_operation_message(self, title: str, message: str):
        info_dialog(self.gui, title, message).exec()
