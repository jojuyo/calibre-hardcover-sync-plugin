from qt.core import (
    QButtonGroup,
    QDate,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QLabel,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from calibre_plugins.hardcover_list.lists import UNKNOWN_READ_DATE


class ReadDateDialog(QDialog):
    """Ask which read date to apply to books newly marked as Read."""

    def __init__(self, parent, titles):
        super().__init__(parent)
        self.setWindowTitle(_("Set read date"))

        if isinstance(titles, int):  # tolerate a bare count being passed
            titles = []
        titles = [t for t in (titles or []) if t]
        count = len(titles)

        layout = QVBoxLayout(self)
        if count == 1:
            text = _('"{title}" will be newly marked as Read on Hardcover.').format(
                title=titles[0]
            )
        else:
            text = _(
                "{count} books will be newly marked as Read on Hardcover:"
            ).format(count=count)
        layout.addWidget(QLabel(text))

        if count > 1:
            layout.addWidget(self._build_title_list(titles))

        layout.addWidget(QLabel(_("Which read date should be used?")))

        self._group = QButtonGroup(self)
        self._today = QRadioButton(_("Today"))
        self._today.setChecked(True)
        self._unknown = QRadioButton(_("I don't know (leave blank on Hardcover)"))
        self._specific = QRadioButton(_("Specific date:"))
        for button in (self._today, self._unknown, self._specific):
            self._group.addButton(button)
            layout.addWidget(button)

        self._date_edit = QDateEdit(QDate.currentDate())
        self._date_edit.setCalendarPopup(True)
        self._date_edit.setDisplayFormat("yyyy-MM-dd")
        self._date_edit.setEnabled(False)
        self._specific.toggled.connect(self._date_edit.setEnabled)
        layout.addWidget(self._date_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _build_title_list(self, titles) -> QScrollArea:
        """A scrollable, bulleted list of the affected book titles."""
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(8, 4, 8, 4)
        for title in titles:
            label = QLabel(f"\u2022 {title}")
            label.setWordWrap(True)
            inner_layout.addWidget(label)
        inner_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidget(inner)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.StyledPanel)
        scroll.setMaximumHeight(160)
        return scroll

    def result_value(self):
        """Return the chosen finished_at, or None for "Today" (server default)."""
        if self._today.isChecked():
            return None
        if self._unknown.isChecked():
            return UNKNOWN_READ_DATE
        date = self._date_edit.date()
        return f"{date.year():04d}-{date.month():02d}-{date.day():02d}"
