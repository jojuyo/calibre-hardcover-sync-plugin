from threading import Event, Thread
from urllib import error, request

from qt.core import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QIcon,
    QLabel,
    QPixmap,
    QSize,
    Qt,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    pyqtSignal,
)

from calibre_plugins.hardcover_list._version import __version__
from calibre_plugins.hardcover_list.cull_isbn.isbn_scan import FoundIsbn

COVER_WIDTH = 56
COVER_HEIGHT = 84
COL_COVER, COL_TITLE, COL_AUTHOR, COL_PUBLISHER, COL_FORMAT, COL_ISBN = range(6)


def _isbn_kind(entry: FoundIsbn) -> str:
    return _("ISBN-13") if len(entry.isbn) == 13 else _("ISBN-10")


def _isbn_tooltip(entry: FoundIsbn) -> str:
    kind = _isbn_kind(entry)
    if entry.is_correction:
        return _("{kind}, suggested correction").format(kind=kind)
    if entry.verified:
        return kind
    return _("{kind}, unverified").format(kind=kind)


def _download_cover(url: str) -> bytes | None:
    if not url or not url.startswith(("http:", "https:")):
        return None
    # The Hardcover asset CDN returns 403 without a User-Agent header.
    headers = {"User-Agent": f"hardcover-list-calibre-plugin/{__version__}"}
    try:
        req = request.Request(url, headers=headers)  # noqa: S310
        with request.urlopen(req, timeout=20) as response:  # noqa: S310
            return response.read()
    except (error.URLError, ValueError, OSError):
        return None


class IsbnSelectDialog(QDialog):
    """Pick an ISBN from a table of looked-up editions (cover, title, …)."""

    row_ready = pyqtSignal(int, object)
    cover_ready = pyqtSignal(int, object)

    def __init__(self, parent, found_isbns: list[FoundIsbn], prompt: str):
        super().__init__(parent)
        self._found_isbns = found_isbns
        self._cancelled = Event()
        self._worker = None
        self.setWindowTitle(_("Cull ISBN"))

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(prompt))

        self.table = QTableWidget(len(found_isbns), 6, self)
        self.table.setHorizontalHeaderLabels(
            [
                _("Cover"),
                _("Title"),
                _("Author"),
                _("Publisher"),
                _("Format"),
                _("ISBN"),
            ]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.table.setIconSize(QSize(COVER_WIDTH, COVER_HEIGHT))
        self.table.verticalHeader().setDefaultSectionSize(COVER_HEIGHT + 8)
        self.table.setColumnWidth(COL_COVER, COVER_WIDTH + 12)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_COVER, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(COL_TITLE, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_AUTHOR, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(
            COL_PUBLISHER, QHeaderView.ResizeMode.ResizeToContents
        )
        header.setSectionResizeMode(
            COL_FORMAT, QHeaderView.ResizeMode.ResizeToContents
        )
        header.setSectionResizeMode(
            COL_ISBN, QHeaderView.ResizeMode.ResizeToContents
        )

        for row, entry in enumerate(found_isbns):
            cover_item = QTableWidgetItem()
            cover_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, COL_COVER, cover_item)
            title_item = QTableWidgetItem(_("Looking up…"))
            self.table.setItem(row, COL_TITLE, title_item)
            self.table.setItem(row, COL_AUTHOR, QTableWidgetItem(""))
            self.table.setItem(row, COL_PUBLISHER, QTableWidgetItem(""))
            self.table.setItem(row, COL_FORMAT, QTableWidgetItem(""))
            isbn_item = QTableWidgetItem(entry.isbn)
            isbn_item.setToolTip(_isbn_tooltip(entry))
            self.table.setItem(row, COL_ISBN, isbn_item)

        if found_isbns:
            self.table.selectRow(0)
        self.table.doubleClicked.connect(self.accept)
        layout.addWidget(self.table)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self.row_ready.connect(self._apply_row)
        self.cover_ready.connect(self._apply_cover)
        self.resize(760, max(320, 150 + len(found_isbns) * (COVER_HEIGHT + 8)))

        self._worker = Thread(target=self._lookup_worker, daemon=True)
        self._worker.start()

    def selected_isbn(self) -> str | None:
        row = self.table.currentRow()
        if 0 <= row < len(self._found_isbns):
            return self._found_isbns[row].isbn
        return None

    def reject(self) -> None:
        self._cancelled.set()
        super().reject()

    def accept(self) -> None:
        self._cancelled.set()
        super().accept()

    def _lookup_worker(self) -> None:
        from calibre_plugins.hardcover_list.cull_isbn.isbn_lookup import (
            lookup_isbn,
        )

        for row, entry in enumerate(self._found_isbns):
            if self._cancelled.is_set():
                return
            try:
                results = lookup_isbn(entry.isbn)
                result = results[0] if results else None
            except Exception:  # noqa: BLE001 - shown as "no match" in the row
                result = None
            if self._cancelled.is_set():
                return
            self.row_ready.emit(row, result)
            if result and result.cover_url:
                data = _download_cover(result.cover_url)
                if data and not self._cancelled.is_set():
                    self.cover_ready.emit(row, data)

    def _apply_row(self, row: int, result) -> None:
        if self._cancelled.is_set() or row >= self.table.rowCount():
            return
        if result is None:
            self.table.item(row, COL_TITLE).setText(_("No match found"))
            self.table.item(row, COL_AUTHOR).setText("")
            self.table.item(row, COL_PUBLISHER).setText("")
            self.table.item(row, COL_FORMAT).setText("")
            return
        self.table.item(row, COL_TITLE).setText(result.title or "")
        self.table.item(row, COL_AUTHOR).setText(result.authors or "")
        self.table.item(row, COL_PUBLISHER).setText(result.publisher or "")
        self.table.item(row, COL_FORMAT).setText(result.format_type or "")

    def _apply_cover(self, row: int, data) -> None:
        if self._cancelled.is_set() or row >= self.table.rowCount():
            return
        pixmap = QPixmap()
        if not pixmap.loadFromData(data):
            return
        scaled = pixmap.scaled(
            COVER_WIDTH,
            COVER_HEIGHT,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.table.item(row, COL_COVER).setIcon(QIcon(scaled))
