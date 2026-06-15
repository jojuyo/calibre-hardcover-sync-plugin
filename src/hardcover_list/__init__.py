from calibre.customize import InterfaceActionBase

from ._version import __version_tuple__


class HardcoverListPlugin(InterfaceActionBase):
    name = "Hardcover Sync"
    description = (
        "Syncs Hardcover lists (and, soon, ratings, reviews, and progress) and "
        "scans book text for ISBNs"
    )
    supported_platforms = ["windows", "osx", "linux"]
    author = "Juan York"
    version = __version_tuple__
    minimum_calibre_version = (7, 7, 0)

    actual_plugin = "calibre_plugins.hardcover_list.ui:HardcoverListsAction"

    def is_customizable(self):
        return True

    def config_widget(self):
        from calibre_plugins.hardcover_list.config_widget import ConfigWidget

        return ConfigWidget()

    def save_settings(self, config_widget):
        config_widget.save_settings()
