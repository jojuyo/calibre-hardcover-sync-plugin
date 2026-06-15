"""Smoke-test that the installed Hardcover Sync plugin imports the way Calibre
actually imports it.

Run with Calibre's bundled Python so the real zip-plugin importer is used::

    calibre-debug scripts/check_plugin_import.py

A bare ``sys.path`` shim (as in earlier versions of this script) gives a false
positive: it lets bare top-level imports such as ``hcl_graphql`` resolve even
though Calibre's importer only resolves names under ``calibre_plugins``. So this
script drives Calibre's own plugin machinery instead.
"""

import sys
import traceback


def main() -> int:
    import calibre.customize.ui as ui

    # Force Calibre to initialise every installed plugin, mirroring GUI startup.
    list(ui.initialized_plugins())

    try:
        from calibre_plugins.hardcover_sync.ui import HardcoverSyncAction
    except Exception:
        traceback.print_exc()
        return 1

    print("SUCCESS", HardcoverSyncAction.name)
    if HardcoverSyncAction.name != "Hardcover Sync":
        print("unexpected action name:", HardcoverSyncAction.name)
        return 1

    # The bundled client must be reachable through the plugin namespace, not as a
    # bare top-level package (which would collide with the metadata plugin).
    from calibre_plugins.hardcover_sync.hcl_graphql.client import (
        DEFAULT_REQUESTS_PER_MINUTE,
    )

    if not DEFAULT_REQUESTS_PER_MINUTE:
        print("hcl_graphql client incomplete")
        return 1

    print("hcl_graphql OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
