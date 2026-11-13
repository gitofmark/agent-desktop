"""Role and action vocabulary.

Raw providers speak many dialects (UIA ``ControlType.Button``, IAccessible
``ROLE_SYSTEM_PUSHBUTTON``, AT-SPI ``push button``, X11 ``Button``).  Everything
is normalised to the small vocabulary in :data:`SHORT_ROLES` because the model
reading the snapshot pays for every distinct word.

Two maps are kept:

* ``RAW_ROLE_MAP``   raw role string -> short role
* ``ACTION_MAP``     raw action id  -> short action

and the inverse lookups (``action_for_raw`` / ``raw_for_action``) so an
executor can always get back to the provider's own action name.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

__all__ = [
    "SHORT_ROLES",
    "RAW_ROLE_MAP",
    "ACTION_MAP",
    "ROLE_ORDER",
    "ROLE_LABELS",
    "IMPLIED_ACTIONS",
    "normalize_role",
    "role_of",
    "normalize_action",
    "action_for_raw",
    "raw_for_action",
    "actions_of",
    "action_implied_by_role",
    "canonical_actions",
]

#: The closed vocabulary of short roles the renderer may print.
SHORT_ROLES: tuple[str, ...] = (
    "window",
    "application",
    "dialog",
    "document",
    "text",
    "pane",
    "group",
    "list",
    "listitem",
    "table",
    "row",
    "cell",
    "tree",
    "treeitem",
    "menu",
    "menubar",
    "menuitem",
    "edit",
    "button",
    "splitbutton",
    "checkbox",
    "radio",
    "combobox",
    "slider",
    "spin",
    "tab",
    "tabitem",
    "scrollbar",
    "toolbar",
    "statusbar",
    "link",
    "image",
    "progressbar",
    "separator",
    "header",
    "tooltip",
    "unknown",
)

#: Plural form used by the overflow line ("... 12 more buttons").
ROLE_LABELS: dict[str, str] = {
    "window": "windows",
    "application": "applications",
    "dialog": "dialogs",
    "document": "documents",
    "text": "texts",
    "pane": "panes",
    "group": "groups",
    "list": "lists",
    "listitem": "list items",
    "table": "tables",
    "row": "rows",
    "cell": "cells",
    "tree": "trees",
    "treeitem": "tree items",
    "menu": "menus",
    "menubar": "menu bars",
    "menuitem": "menu items",
    "edit": "edits",
    "button": "buttons",
    "splitbutton": "split buttons",
    "checkbox": "checkboxes",
    "radio": "radio buttons",
    "combobox": "combo boxes",
    "slider": "sliders",
    "spin": "spinners",
    "tab": "tabs",
    "tabitem": "tab items",
    "scrollbar": "scrollbars",
    "toolbar": "toolbars",
    "statusbar": "status bars",
    "link": "links",
    "image": "images",
    "progressbar": "progress bars",
    "separator": "separators",
    "header": "headers",
    "tooltip": "tooltips",
    "unknown": "elements",
}

#: Raw role spellings seen across UIA / MSAA / AT-SPI / our own dumper.
#: Keys are normalised with :func:`_norm_key` before lookup.
_RAW_ROLE_PAIRS: tuple[tuple[str, str], ...] = (
    # windows & containers
    ("window", "window"),
    ("application", "application"),
    ("pane", "pane"),
    ("panel", "pane"),
    ("frame", "window"),
    ("dialog", "dialog"),
    ("modal dialog", "dialog"),
    ("alert", "dialog"),
    ("group", "group"),
    ("grouping", "group"),
    ("document", "document"),
    ("text", "text"),
    ("static text", "text"),
    ("statictext", "text"),
    ("label", "text"),
    ("heading", "text"),
    ("paragraph", "text"),
    ("section", "group"),
    ("custom", "pane"),
    ("unknown", "unknown"),
    # lists & tables
    ("list", "list"),
    ("list item", "listitem"),
    ("listitem", "listitem"),
    ("datagrid", "table"),
    ("data grid", "table"),
    ("table", "table"),
    ("row", "row"),
    ("dataitem", "cell"),
    ("data item", "cell"),
    ("cell", "cell"),
    ("tree", "tree"),
    ("tree item", "treeitem"),
    ("treeitem", "treeitem"),
    ("outline", "tree"),
    ("outline item", "treeitem"),
    # menus
    ("menu", "menu"),
    ("menu bar", "menubar"),
    ("menubar", "menubar"),
    ("menu item", "menuitem"),
    ("menuitem", "menuitem"),
    ("popup menu", "menu"),
    # text entry
    ("edit", "edit"),
    ("text box", "edit"),
    ("textbox", "edit"),
    ("text field", "edit"),
    ("document text", "edit"),
    ("entry", "edit"),
    # buttons & friends
    ("button", "button"),
    ("push button", "button"),
    ("pushbutton", "button"),
    ("hyperlink", "link"),
    ("link", "link"),
    ("split button", "splitbutton"),
    ("splitbutton", "splitbutton"),
    ("check box", "checkbox"),
    ("checkbox", "checkbox"),
    ("radio button", "radio"),
    ("radiobutton", "radio"),
    ("radio", "radio"),
    ("toggle button", "checkbox"),
    ("togglebutton", "checkbox"),
    ("combo box", "combobox"),
    ("combobox", "combobox"),
    ("popup button", "combobox"),
    ("slider", "slider"),
    ("spinner", "spin"),
    ("scroll bar", "scrollbar"),
    ("scrollbar", "scrollbar"),
    ("tab", "tab"),
    ("tab item", "tabitem"),
    ("tabitem", "tabitem"),
    ("page tab", "tabitem"),
    ("tool bar", "toolbar"),
    ("toolbar", "toolbar"),
    ("status bar", "statusbar"),
    ("statusbar", "statusbar"),
    ("image", "image"),
    ("icon", "image"),
    ("graphic", "image"),
    ("progress bar", "progressbar"),
    ("progressbar", "progressbar"),
    ("separator", "separator"),
    ("header", "header"),
    ("header item", "header"),
    ("column header", "header"),
    ("tool tip", "tooltip"),
    ("tooltip", "tooltip"),
    ("title bar", "header"),
    ("titlebar", "header"),
)

RAW_ROLE_MAP: dict[str, str] = dict(_RAW_ROLE_PAIRS)

#: Raw action id -> short action.  Keys normalised through :func:`_norm_key`
#: which strips the UIA ``ControlPattern.`` / ``::`` noise and lower-cases.
_ACTION_PAIRS: tuple[tuple[str, str], ...] = (
    ("invoke", "click"),
    ("click", "click"),
    ("press", "click"),
    ("do default action", "click"),
    ("default action", "click"),
    ("double click", "double click"),
    ("right click", "right click"),
    ("setfocus", "focus"),
    ("set focus", "focus"),
    ("focus", "focus"),
    ("setvalue", "set value"),
    ("set value", "set value"),
    ("value", "set value"),
    ("scroll", "scroll"),
    ("scroll up", "scroll"),
    ("scroll down", "scroll"),
    ("scroll left", "scroll"),
    ("scroll right", "scroll"),
    ("expand", "expand"),
    ("expand collapse", "expand"),
    ("collapse", "collapse"),
    ("toggle", "toggle"),
    ("select", "select"),
    ("addtoselection", "select"),
    ("select item", "select"),
    ("type", "type"),
    ("type text", "type"),
    ("key", "key"),
    ("gettext", "read"),
    ("read", "read"),
)

ACTION_MAP: dict[str, str] = dict(_ACTION_PAIRS)

#: Render order inside the ``(...)`` state/action blob and inside ``inspect``.
ROLE_ORDER: tuple[str, ...] = SHORT_ROLES

#: Actions that a role already implies; printing them adds no information (R4).
IMPLIED_ACTIONS: dict[str, frozenset[str]] = {
    "button": frozenset({"click"}),
    "splitbutton": frozenset({"click"}),
    "link": frozenset({"click"}),
    "menuitem": frozenset({"click"}),
    "listitem": frozenset({"click", "select"}),
    "treeitem": frozenset({"click", "select", "expand", "collapse"}),
    "tabitem": frozenset({"click", "select"}),
    "cell": frozenset({"click", "select"}),
    "row": frozenset({"click", "select"}),
    "checkbox": frozenset({"click", "toggle"}),
    "radio": frozenset({"click", "select", "toggle"}),
    "edit": frozenset({"set value", "type", "focus"}),
    "document": frozenset({"set value", "type", "read", "focus"}),
    "combobox": frozenset({"click", "expand", "set value", "focus", "type"}),
    "menu": frozenset({"expand"}),
    "scrollbar": frozenset({"scroll"}),
    "slider": frozenset({"set value", "scroll"}),
    "spin": frozenset({"set value", "focus"}),
    "image": frozenset({"click"}),
    "pane": frozenset({"focus"}),
    "group": frozenset({"focus"}),
    "window": frozenset({"focus"}),
    "text": frozenset({"focus", "read"}),
    "statusbar": frozenset({"read"}),
}

_ROLE_PREFIXES = (
    "controltype.",
    "controltype",
    "role_system_",
    "rolesystem",
    "uia_",
    "atspi role ",
)


def _norm_key(value: str) -> str:
    """Normalise a raw role/action string for map lookup."""
    key = str(value or "").strip().lower()
    for prefix in _ROLE_PREFIXES:
        if key.startswith(prefix):
            key = key[len(prefix) :]
    key = key.replace("_", " ").replace("::", " ").replace(".", " ")
    key = re.sub(r"\s+", " ", key).strip()
    return key


def normalize_role(raw_role: str) -> str:
    """Map any provider's role spelling to the short vocabulary."""
    key = _norm_key(raw_role)
    if not key:
        return "unknown"
    if key in RAW_ROLE_MAP:
        return RAW_ROLE_MAP[key]
    squashed = key.replace(" ", "")
    if squashed in RAW_ROLE_MAP:
        return RAW_ROLE_MAP[squashed]
    for short in SHORT_ROLES:
        if squashed == short:
            return short
    # heuristics for provider strings that wrap a role name: "ButtonControl",
    # "PushButton1", "GtkButton", "XmPushButton".  A blind substring scan is
    # wrong here - "gedit" contains "edit", and app class names are not roles.
    for suffix in ("control", "button", "widget", "item"):
        if squashed.endswith(suffix) and squashed not in SHORT_ROLES:
            stem = squashed[: -len(suffix)]
            for short in SHORT_ROLES:
                if short != "unknown" and stem.endswith(short.replace(" ", "")):
                    return short
    for prefix in ("gtk", "qt", "xm", "tk", "wx", "win", "app"):
        if squashed.startswith(prefix) and len(squashed) > len(prefix):
            rest = squashed[len(prefix):]
            if rest in SHORT_ROLES:
                return rest
    return "unknown"


#: Alias kept for readability at call sites that already dealt with prefixes.
role_of = normalize_role


def normalize_action(raw_action: str) -> str:
    """Map a provider action id (``ControlPattern.Invoke``) to ``click``."""
    key = _norm_key(raw_action)
    if not key:
        return ""
    if key in ACTION_MAP:
        return ACTION_MAP[key]
    squashed = key.replace(" ", "")
    if squashed in ACTION_MAP:
        return ACTION_MAP[squashed]
    for short in set(ACTION_MAP.values()):
        if squashed == short.replace(" ", ""):
            return short
    for raw, short in ACTION_MAP.items():
        if raw.replace(" ", "") in squashed:
            return short
    return key.replace(" ", "-")


def canonical_actions(raw_actions: Iterable[str]) -> list[str]:
    """Normalise, de-duplicate and order a list of raw action ids."""
    seen: list[str] = []
    for raw in raw_actions or ():
        short = normalize_action(raw)
        if short and short not in seen:
            seen.append(short)
    order = {name: i for i, name in enumerate(
        ["click", "double click", "right click", "focus", "type", "set value",
         "read", "select", "toggle", "expand", "collapse", "scroll", "key"])}
    return sorted(seen, key=lambda a: (order.get(a, 99), a))


def action_for_raw(raw_action: str) -> str:
    """Reverse lookup helper: raw provider id -> short action."""
    return normalize_action(raw_action)


def raw_for_action(short_action: str, element=None) -> str:
    """Best raw provider id for a short action.

    When an :class:`~snapshot_parser.model.Element` is given its original
    ``action_ids`` mapping is preferred, so the executor calls exactly the
    pattern the provider advertised.
    """
    if element is not None:
        ids = getattr(element, "action_ids", None) or {}
        if short_action in ids:
            return ids[short_action]
    for raw, short in ACTION_MAP.items():
        if short == short_action:
            return raw
    return short_action


#: A state already tells the reader what the action would do (R4).
IMPLIED_BY_STATE: dict[str, frozenset[str]] = {
    "expanded": frozenset({"expand"}),
    "collapsed": frozenset({"collapse"}),
    "checked": frozenset({"toggle"}),
    "selected": frozenset({"select"}),
    "focused": frozenset({"focus"}),
    "disabled": frozenset({"click", "focus", "expand", "collapse", "set value", "type"}),
}


def actions_of(element) -> list[str]:
    """Actions worth printing: not defaults, not implied by role or state (R4)."""
    from .model import DEFAULT_ACTIONS

    hidden = set(IMPLIED_ACTIONS.get(element.role, frozenset()))
    hidden |= DEFAULT_ACTIONS
    for state_name, actions in IMPLIED_BY_STATE.items():
        if element.state.get(state_name):
            hidden |= actions
    return [a for a in element.actions if a not in hidden]


def action_implied_by_role(role: str, action: str) -> bool:
    return action in IMPLIED_ACTIONS.get(role, frozenset())


def role_label(role: str) -> str:
    return ROLE_LABELS.get(role, role if role.endswith("s") else role + "s")
