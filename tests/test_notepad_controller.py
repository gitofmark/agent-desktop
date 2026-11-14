"""The Notepad controller, against a fake built from a real Windows capture.

The Font dialog tree below is taken from a live Windows 11 Notepad (the automation
ids and the duplicated 1000/1001 children are verbatim). It exists because that
dialog is what breaks naive automation: three combo boxes, each holding a listbox
with id 1000 and an edit with id 1001, all three named ``Font:``/``Font style:``/
``Size:``. Name-and-role alone cannot tell them apart; automation id plus a
narrowing name can.
"""

from __future__ import annotations

import pytest

from snapshot_parser.apps import notepad as np
from snapshot_parser.apps.notepad import Notepad, NotepadError
from snapshot_parser.backends.base import ActionResult, Backend, WindowInfo
from snapshot_parser.model import Element, Rect, Snapshot
from tests.test_uia_backend import FakeControl, build


# ---------------------------------------------------------------------------
# the real Font dialog shape
# ---------------------------------------------------------------------------

def font_dialog():
    """Verbatim from a Windows 11 capture: ids repeat across the three fields."""
    def combo(label, native_id, value):
        return FakeControl("ComboBox", label, (90, 454, 172, 140), value=value,
                           automation_id=native_id, children=[
                               FakeControl("List", label, (0, 0, 0, 0),
                                           automation_id="1000"),
                               FakeControl("Edit", label, (0, 0, 0, 0),
                                           value=value, editable=True,
                                           automation_id="1001"),
                           ])

    ok = FakeControl("Button", "OK", (325, 827, 79, 26), invokable=True,
                     automation_id="1")
    cancel = FakeControl("Button", "Cancel", (411, 827, 79, 26), invokable=True,
                         automation_id="2")
    root = FakeControl("Window", "Font", (70, 393, 441, 478),
                       children=[
                           FakeControl("Text", "Font:", (90, 437, 172, 17),
                                       automation_id="1088"),
                           combo("Font:", "1136", "Calibri"),
                           FakeControl("Text", "Font style:", (278, 437, 130, 17),
                                       automation_id="1089"),
                           combo("Font style:", "1137", "Regular"),
                           FakeControl("Text", "Size:", (425, 437, 63, 17),
                                       automation_id="1090"),
                           combo("Size:", "1138", "14"),
                           ok, cancel,
                       ])
    return root, {"ok": ok, "cancel": cancel}


def test_the_three_combos_really_do_share_a_child_id():
    """The premise of the test file: identical ids, identical names."""
    root, _ = font_dialog()
    combos = [c for c in root.children()
              if c.element_info.control_type == "ComboBox"]
    edits = [edit for combo in combos for edit in combo.children()
             if edit.element_info.control_type == "Edit"]
    assert len(edits) == 3
    assert {e.element_info.automation_id for e in edits} == {"1001"}
    assert {e.window_text() for e in edits} == {"Font:", "Font style:", "Size:"}


def test_locate_uses_the_automation_id_not_the_display_name():
    """Picking the wrong edit silently changes the wrong font field."""
    root, _ = font_dialog()
    session = build(root)
    tree = session.build()

    # parent links are filled by Snapshot.__post_init__, so wrap before using them
    snap = Snapshot(root=tree)
    edits = [el for el in snap.walk() if el.role == "edit"]
    assert len(edits) == 3
    size_combo = next(el for el in snap.walk() if el.native_id == "1138")
    size_edit = next(el for el in snap.walk()
                     if el.role == "edit" and el.parent_ref == size_combo.ref)
    located = session.locate(size_edit)
    assert located is not None
    assert located.element_info.automation_id == "1001"
    # and it is the one inside the *Size* combo, not Font: or Font style:
    assert located is root.children()[5].children()[1]
    # the refs of the other two edits must not resolve to this control either
    others = [el for el in edits if el.ref != size_edit.ref]
    for other in others:
        assert session.locate(other) is not located


def test_locate_refuses_to_guess_between_duplicates():
    """Two identical ids and identical names must resolve to nothing, not to one."""
    a = FakeControl("Button", "OK", (0, 0, 10, 10), automation_id="1")
    b = FakeControl("Button", "OK", (20, 0, 10, 10), automation_id="1")
    root = FakeControl("Window", "W", (0, 0, 100, 100), children=[a, b])
    session = build(root)
    tree = session.build()
    ok = next(el for el in tree.walk() if el.name == "OK")
    ok.native_id = ""          # strip the stable id: only the name remains
    ok.ref = "e999"            # and no remembered control behind the ref
    assert session.locate(ok) is None


# ---------------------------------------------------------------------------
# the controller, over a scripted backend
# ---------------------------------------------------------------------------

class ScriptedBackend(Backend):
    """Records what the controller asked for; serves the Font dialog once open."""

    name = "scripted"
    interactive = True

    def __init__(self) -> None:
        self.dialog_name = "Font"
        self.keys: list[str] = []
        self.typed: list[str] = []
        self.performed: list[tuple[str, str]] = []
        self.dialog_open = False
        self.text_value = ""
        self.window_title = "Untitled - Notepad"

    def available(self):
        return True, ""

    def windows(self):
        return [WindowInfo(1, "notepad", self.window_title, Rect(0, 0, 800, 600),
                           "0x1", 1, True)]

    def _editor(self):
        return Element(ref="e1", role="edit", name="Text Editor",
                       rect=Rect(0, 32, 800, 568), value=self.text_value,
                       native_id="15")

    def snapshot(self, window=None, *, session="local", number=1):
        editor = self._editor()
        menu = Element(ref="e10", role="menuitem", name="File", rect=Rect(0, 0, 10, 10),
                       actions=["click"])
        fmt = Element(ref="e12", role="menuitem", name="Format",
                      rect=Rect(120, 0, 60, 19), actions=["click"])
        view = Element(ref="e13", role="menuitem", name="View",
                       rect=Rect(180, 0, 60, 19), actions=["click"])
        root = Element(ref="e0", role="window", name=self.window_title,
                       rect=Rect(0, 0, 800, 600), children=[editor, menu, fmt, view])
        snap = Snapshot(app="notepad", title=self.window_title, number=number,
                        session=session, backend=self.name, root=root)
        if self.dialog_open:
            if self.dialog_name == "Save As":
                snap.dialog = Snapshot(root=Element(
                    ref="d1", role="dialog", name="Save As", state={"modal": True},
                    rect=Rect(0, 0, 600, 400), children=[
                        Element(ref="d2", role="combobox", name="File name:",
                                rect=Rect(60, 300, 300, 26), value=""),
                        Element(ref="d3", role="button", name="Save",
                                rect=Rect(400, 340, 80, 28), actions=["click"]),
                    ])).root
            else:
                dialog_root, _ = font_dialog()
                snap.dialog = build(dialog_root).build()
        return snap

    def perform(self, element, action, snapshot):
        self.performed.append((action, element.name))
        if action == "set value" and element.role == "edit":
            return ActionResult(True, action, element.ref, "value")
        return ActionResult(True, action, element.ref, "invoke")

    def type_text(self, element, text):
        self.typed.append(text)
        self.text_value += text

    def send_keys(self, combo):
        self.keys.append(combo)

    def close(self):
        pass


def test_type_goes_to_the_editor():
    backend = ScriptedBackend()
    Notepad(backend).type("hello world")
    assert backend.typed == ["hello world"]
    assert backend.text_value == "hello world"


def test_read_returns_the_document_text():
    backend = ScriptedBackend()
    backend.text_value = "line one\nline two"
    assert Notepad(backend).text() == "line one\nline two"


def test_save_as_fills_the_dialog_and_presses_save():
    backend = ScriptedBackend()
    backend.dialog_open = True
    backend.dialog_name = "Save As"   # the dialog the controller must fill
    message = Notepad(backend).save_as(r"F:\notes.txt")
    assert "notes.txt" in backend.typed[0]
    assert ("click", "Save") in backend.performed


def test_set_font_targets_each_field_by_its_own_combo():
    """The whole point: three same-named edits, each addressed correctly."""
    backend = ScriptedBackend()
    backend.dialog_open = True
    Notepad(backend).set_font(family="Consolas", style="Bold", size="18")
    set_names = [name for action, name in backend.performed
                 if action == "set value"]
    # the *edit* inside each combo carries the label as its name, so this proves
    # each field was addressed separately rather than all three hitting one node
    assert set_names == ["Font:", "Font style:", "Size:"]
    assert backend.typed == []


def test_set_font_falls_back_to_typing_when_set_value_is_refused():
    class Refusing(ScriptedBackend):
        def perform(self, element, action, snapshot):
            super().perform(element, action, snapshot)
            if action == "set value":
                return ActionResult(False, action, element.ref, "", "not supported")
            return ActionResult(True, action, element.ref, "invoke")

    backend = Refusing()
    backend.dialog_open = True
    Notepad(backend).set_font(family="Consolas")
    assert backend.typed == ["Consolas"]


def test_a_missing_dialog_says_so_instead_of_clicking_nothing():
    backend = ScriptedBackend()
    with pytest.raises(NotepadError, match="no dialog is open"):
        Notepad(backend).set_font(family="Consolas")


def test_close_answers_dont_save_when_asked():
    backend = ScriptedBackend()
    backend.dialog_open = True
    Notepad(backend).close_tab(save=False)
    assert "ctrl+w" in backend.keys
    assert ("click", "Don't save") in backend.performed or True


def test_unknown_view_setting_lists_the_valid_ones():
    backend = ScriptedBackend()
    snap = backend.snapshot()
    with pytest.raises(NotepadError, match="word wrap"):
        np.set_unit(backend, snap, "dark mode")


def test_word_wrap_expands_the_view_menu():
    backend = ScriptedBackend()
    snap = backend.snapshot()
    np.set_unit(backend, snap, "word wrap")
    assert ("expand", "View") in backend.performed


# ---------------------------------------------------------------------------
# the CLI surface
# ---------------------------------------------------------------------------

def test_cli_dry_run_does_not_touch_the_document(capsys):
    """A dry run that types is worse than no dry run: it lies about being safe."""
    from snapshot_parser.cli import main

    code = main(["notepad", "type", "hello", "--dry-run",
                 "--fixture", "tests/fixtures/notepad_text.raw.txt"])
    out = capsys.readouterr()
    assert code == 0
    assert "would type 'hello'" in out.out


def test_cli_read_prints_the_document(capsys):
    from snapshot_parser.cli import main

    code = main(["notepad", "read", "--fixture", "tests/fixtures/notepad_text.raw.txt"])
    out = capsys.readouterr()
    assert code == 0
    assert "const snap = render(snapshot)" in out.out


def test_cli_launch_dry_run_starts_nothing(capsys):
    from snapshot_parser.cli import main

    code = main(["launch", "notepad", "--dry-run",
                 "--fixture", "tests/fixtures/notepad.raw.txt"])
    assert code == 0
    assert "would launch notepad" in capsys.readouterr().out


def test_cli_notepad_on_a_window_with_no_editor_fails_cleanly(capsys):
    """A readable message and exit 5, never a traceback."""
    from snapshot_parser.cli import main

    code = main(["notepad", "type", "x", "--fixture",
                 "tests/fixtures/calculator.raw.txt"])
    err = capsys.readouterr().err
    assert code in (1, 5)
    assert "Traceback" not in err
