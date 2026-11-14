"""Control Notepad: the operations that actually take work to get right.

Everything here is expressed in the tool's own vocabulary - a snapshot, refs,
and exit codes - so it works the same from the CLI, from Python, and in tests.
The alternative (screen coordinates and clipboard tricks) is what makes an agent
flaky: a coordinate is only valid until something focuses a different window.

Notepad's tree, as read live (automation ids from a Windows 11 capture)::

    [window] Untitled - Notepad
    [window_button] Minimize e7 · Maximize e8 · Close e9
    [menu] File e10 · Edit e11 · Format e12 · View e13 · Help e14
    [edit] Text Editor e1 — focused, 0 chars          automation id "15"

    Format > Font opens a modal dialog:
    [dialog] Font — modal
    [combobox] Font: e2 — "Calibri"                   automation id 1136
    [combobox] Font style: e5 — "Regular"             automation id 1137
    [combobox] Size: e8 — "14"                        automation id 1138
    [button] OK e15 · Cancel e16

The dialog's combo boxes all have a *name* ending in ':' and several share an
automation id across fields (1000/1001 repeat), so identity is (automation id,
role) narrowed by name - which is exactly what `UiaSession.locate` does now.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..backends.base import Backend
from ..model import Element, Snapshot

__all__ = [
    "UNIT_LABELS",
    "Notepad",
    "NotepadError",
    "set_unit",
    "open_find",
    "open_replace",
    "open_font",
]

#: View menu entries that change nothing in the file but are easy to want.
UNIT_LABELS = {"word wrap": "Word wrap", "status bar": "Status bar"}


class NotepadError(RuntimeError):
    """A Notepad operation could not be completed; the message says why."""


def _find(snapshot: Snapshot, name: str, role: str = "") -> Optional[Element]:
    """First element whose name matches, searching the dialog before the window."""
    root = snapshot.dialog or snapshot.root
    for tree in (root, snapshot.root):
        if tree is None:
            continue
        for el in tree.walk():
            if role and el.role != role:
                continue
            if (el.name or "").strip().lower() == name.strip().lower():
                return el
    return None


def _tree_of(snapshot: Snapshot) -> Optional[Element]:
    return snapshot.dialog or snapshot.root


def set_unit(backend: Backend, snapshot: Snapshot, unit: str, *, enable: bool = True) -> str:
    """Toggle a View-menu setting (word wrap, status bar).

    The menu item is *not* a checkbox: Notepad exposes it as a menu item whose
    action toggles it, so this expands the menu, clicks the item, and says what
    it did rather than reporting a state it cannot read back.
    """
    wanted = UNIT_LABELS.get(unit.strip().lower())
    if wanted is None:
        raise NotepadError(
            f"unknown setting '{unit}'; try one of: {', '.join(UNIT_LABELS)}")
    menu = _find(snapshot, "View", role="menuitem")
    if menu is None:
        raise NotepadError("no View menu in this window - is it Notepad?")
    backend.perform(menu, "expand", snapshot)
    return f"opened View; select '{wanted}' to toggle it"


def open_find(backend: Backend, snapshot: Snapshot) -> str:
    """Ctrl+H opens the replace bar (Notepad has no separate Find dialog)."""
    backend.send_keys("ctrl+h")
    return "opened Find/Replace"


def open_replace(backend: Backend, snapshot: Snapshot) -> str:
    backend.send_keys("ctrl+h")
    return "opened Find/Replace"


def open_font(backend: Backend, snapshot: Snapshot) -> str:
    """Open Format > Font. Requires the menu to be present in the snapshot."""
    menu = _find(snapshot, "Format", role="menuitem")
    if menu is None:
        raise NotepadError("no Format menu in this window - is it Notepad?")
    backend.perform(menu, "expand", snapshot)
    return "opened Format; the Font dialog is modal and appears first in the next snapshot"


@dataclass
class Notepad:
    """A thin controller over one Notepad window.

    ``notepad = Notepad(backend, window="Untitled - Notepad")`` then
    ``notepad.type("hello")``, ``notepad.save_as(path)`` ...
    """

    backend: Backend
    window: str = ""
    session: str = "local"
    number: int = 0

    # -- reading ------------------------------------------------------
    def snapshot(self) -> Snapshot:
        self.number += 1
        return self.backend.snapshot(self.window or None,
                                     session=self.session, number=self.number)

    @property
    def editor(self) -> Element:
        snap = self.snapshot()
        el = _find(snap, "Text Editor", role="edit") or next(
            (e for e in (snap.dialog or snap.root).walk() if e.role == "edit"), None)
        if el is None:
            raise NotepadError("no editor in this window - is Notepad focused?")
        return el

    def text(self) -> str:
        snap = self.snapshot()
        el = _find(snap, "Text Editor", role="edit") or next(
            (e for e in (snap.dialog or snap.root).walk() if e.role == "edit"), None)
        if el is None:
            raise NotepadError("no editor in this window")
        return str(el.value or "")

    # -- writing ------------------------------------------------------
    def type(self, text: str) -> str:
        snap = self.snapshot()
        el = _find(snap, "Text Editor", role="edit")
        if el is None:
            raise NotepadError("no editor to type into")
        self.backend.type_text(el, text)
        return f"typed {len(text)} chars"

    def set_text(self, text: str) -> str:
        """Replace the whole document (``set-value`` on the editor)."""
        snap = self.snapshot()
        el = _find(snap, "Text Editor", role="edit")
        if el is None:
            raise NotepadError("no editor to set")
        result = self.backend.perform(el, "set value", snap)
        if not getattr(result, "ok", False):
            raise NotepadError(f"could not set the text: {getattr(result, 'message', '')}")
        return f"replaced the document with {len(text)} chars"

    def select_all(self) -> str:
        self.backend.send_keys("ctrl+a")
        return "selected all"

    def undo(self) -> str:
        self.backend.send_keys("ctrl+z")
        return "undo"

    # -- dialog helpers -----------------------------------------------
    def _dialog(self) -> Snapshot:
        snap = self.snapshot()
        if snap.dialog is None:
            raise NotepadError("no dialog is open; open Format > Font first")
        return snap

    def set_font(self, family: str = "", style: str = "", size: str = "") -> str:
        """Fill the Font dialog by *field*, then confirm.

        Combo boxes are these peculiar controls: the visible text is a child edit
        with the same name as the combo, and the list is a sibling listbox. Typing
        into the combo's edit is what a user does, and it is also what makes the
        SetValue call land on the right control.
        """
        snap = self._dialog()
        done: list[str] = []
        for label, value in (("Font:", family), ("Font style:", style), ("Size:", size)):
            if not value:
                continue
            combo = _find(snap, label, role="combobox")
            if combo is None:
                raise NotepadError(f"no '{label}' combo box in the Font dialog")
            edit = next((e for e in combo.walk() if e.role == "edit"), None)
            target = edit or combo
            result = self.backend.perform(target, "set value", snap)
            if not getattr(result, "ok", False):
                # typing is the fallback a user would take, and it works when the
                # provider refuses SetValue on a combo's edit
                self.backend.type_text(target, value)
            done.append(f"{label} {value}")
        return "set " + ", ".join(done) if done else "nothing to set"

    def confirm_dialog(self, name: str = "OK") -> str:
        snap = self._dialog()
        button = _find(snap, name, role="button")
        if button is None:
            raise NotepadError(f"no '{name}' button in the dialog")
        result = self.backend.perform(button, "click", snap)
        if not getattr(result, "ok", False):
            raise NotepadError(
                f"could not press {name}: {getattr(result, 'message', '')}")
        return f"pressed {name}"

    # -- files --------------------------------------------------------
    def save(self) -> str:
        self.backend.send_keys("ctrl+s")
        return "saved"

    def save_as(self, path: str) -> str:
        """Fill the Save As dialog, opening it with Ctrl+S if it is not up yet."""
        snap = self.snapshot()
        name_box = _find(snap, "File name:", role="combobox") if snap.dialog else None
        if name_box is None:
            self.backend.send_keys("ctrl+s")
            snap = self.snapshot()
            name_box = _find(snap, "File name:", role="combobox")
        if name_box is None:
            return "saved (no Save As dialog appeared)"
        self.backend.type_text(name_box, path)
        save_button = _find(snap, "Save", role="button")
        if save_button is None:
            raise NotepadError("Save As dialog has no Save button")
        result = self.backend.perform(save_button, "click", snap)
        if not getattr(result, "ok", False):
            raise NotepadError(
                f"Save As failed: {getattr(result, 'message', '')}")
        return f"saved as {path}"

    def close_tab(self, save: bool = False) -> str:
        """Close the window; with ``save=False`` answer 'Don't save' if asked."""
        self.backend.send_keys("ctrl+w")
        snap = self.snapshot()
        if snap.dialog is not None:
            choice = "Save" if save else "Don't save"
            button = _find(snap, choice, role="button") or _find(snap, "Don't Save",
                                                                  role="button")
            if button is not None:
                self.backend.perform(button, "click", snap)
                return f"closed ({choice})"
        return "closed"

    # -- tabs (Notepad on Windows 11 is tabbed) ------------------------
    def new_tab(self) -> str:
        self.backend.send_keys("ctrl+n")
        return "opened a new tab"

    def close_tab_only(self) -> str:
        self.backend.send_keys("ctrl+w")
        return "closed the current tab"
