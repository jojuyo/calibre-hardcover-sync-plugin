from calibre.gui2 import gprefs

from .config import PLUGIN_PREFS

PLUGIN_ACTION_NAME = "Hardcover Sync"
# Names from earlier versions that should be replaced by the single
# "Hardcover Sync" entry when migrating an existing context-menu layout.
OBSOLETE_ACTION_NAMES = ("Hardcover Lists", "Cull ISBN")
CONTEXT_MENU_KEYS = (
    "action-layout-context-menu",
    "action-layout-context-menu-split",
    "action-layout-context-menu-cover-browser",
)


def _layout_actions(key: str) -> list:
    return list(gprefs.get(key, gprefs.defaults.get(key, ())))


def _layout_with_action(actions: list) -> list:
    # Remember where the old entries lived so the new one can take their place.
    obsolete_index = next(
        (i for i, a in enumerate(actions) if a in OBSOLETE_ACTION_NAMES),
        None,
    )
    updated = [a for a in actions if a not in OBSOLETE_ACTION_NAMES]
    if PLUGIN_ACTION_NAME in updated:
        return updated
    if obsolete_index is not None:
        updated.insert(min(obsolete_index, len(updated)), PLUGIN_ACTION_NAME)
        return updated
    try:
        idx = updated.index("Remove Books")
        updated.insert(idx, PLUGIN_ACTION_NAME)
    except ValueError:
        updated.append(PLUGIN_ACTION_NAME)
    return updated


def ensure_context_menu_action(gui) -> bool:
    """Add Hardcover Sync to the book context menu if it is missing."""
    if PLUGIN_ACTION_NAME not in gui.iactions:
        return False

    changed = False
    for key in CONTEXT_MENU_KEYS:
        current = _layout_actions(key)
        updated = _layout_with_action(current)
        if updated != current:
            gprefs[key] = tuple(updated)
            changed = True

    if changed:
        PLUGIN_PREFS["context_menu_configured"] = True
        if hasattr(gui, "build_context_menus"):
            gui.build_context_menus()
    return changed
