from qt.core import QCheckBox, QLabel, QLineEdit, QSpinBox, QVBoxLayout, QWidget

from .config import (
    PLUGIN_PREFS,
    get_auto_push,
    get_requests_per_minute,
    set_auto_push,
    set_auto_push_prompted,
    sync_rate_limit_config,
)


class ConfigWidget(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout()
        self.setLayout(layout)

        layout.addWidget(QLabel(_("Hardcover API key:")))
        self.api_key = QLineEdit(self)
        self.api_key.setText(str(PLUGIN_PREFS.get("api_key", "") or ""))
        self.api_key.setToolTip(
            _(
                "Leave blank to use the API key from the Hardcover metadata plugin, "
                "if one is configured."
            )
        )
        layout.addWidget(self.api_key)

        layout.addWidget(QLabel(_("API requests per minute:")))
        self.requests_per_minute = QSpinBox(self)
        self.requests_per_minute.setRange(1, 120)
        self.requests_per_minute.setValue(get_requests_per_minute())
        self.requests_per_minute.setToolTip(
            _(
                "Maximum Hardcover API requests per minute. This limit is shared "
                "with the Hardcover metadata plugin. Hardcover allows about 60/min."
            )
        )
        layout.addWidget(self.requests_per_minute)

        self.auto_push = QCheckBox(
            _("Automatically push column edits to Hardcover"), self
        )
        self.auto_push.setChecked(get_auto_push())
        self.auto_push.setToolTip(
            _(
                "When enabled, editing a book's rating, review, status, notes or "
                "quotes pushes the change to Hardcover automatically."
            )
        )
        layout.addWidget(self.auto_push)

        layout.addStretch(1)

    def save_settings(self):
        PLUGIN_PREFS["api_key"] = self.api_key.text().strip()
        PLUGIN_PREFS["requests_per_minute"] = self.requests_per_minute.value()
        set_auto_push(self.auto_push.isChecked())
        # Configuring it explicitly counts as having answered the prompt.
        set_auto_push_prompted(True)
        sync_rate_limit_config()
