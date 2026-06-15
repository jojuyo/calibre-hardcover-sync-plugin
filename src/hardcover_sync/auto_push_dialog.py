from qt.core import QMessageBox


def ask_auto_push(parent) -> bool:
    """Ask the user whether to push column edits to Hardcover automatically.

    Returns True for automatic pushing, False to keep pushing manually.
    """
    box = QMessageBox(parent)
    box.setWindowTitle(_("Automatic Hardcover sync"))
    box.setIcon(QMessageBox.Icon.Question)
    box.setText(
        _(
            "You just edited a column that Hardcover Sync can push "
            "(rating, review, status, notes or quotes)."
        )
    )
    box.setInformativeText(
        _(
            "Would you like Hardcover Sync to push these changes to Hardcover "
            "automatically from now on?\n\n"
            "You can change this later in the plugin's settings."
        )
    )
    auto_button = box.addButton(
        _("Push automatically"), QMessageBox.ButtonRole.AcceptRole
    )
    box.addButton(_("Push manually"), QMessageBox.ButtonRole.RejectRole)
    box.exec()
    return box.clickedButton() is auto_button
