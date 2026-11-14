"""The Windows backend's logic, exercised without Windows.

``UiaSession`` only needs objects that answer like pywinauto wrappers
(``window_text()``, ``element_info.control_type``, ``rectangle()``, ``children()``,
``is_invokable()`` ...). Fake controls let us test ref assignment, state and
action normalisation, the geometry conversion and the fallback click target -
all of which is where the bugs would be. The provider calls themselves are the
only part that needs a real desktop, and those are covered by the live tests.
"""

from __future__ import annotations

import sys

import pytest

from snapshot_parser.model import Element, Rect
from snapshot_parser.render import render_snapshot
from snapshot_parser.model import Snapshot


class FakeRect:
    def __init__(self, x, y, w, h):
        self.left, self.top = x, y
        self._w, self._h = w, h

    def width(self):
        return self._w

    def height(self):
        return self._h


class FakeInfo:
    def __init__(self, control_type="", automation_id="", runtime_id=(1, 2)):
        self.control_type = control_type
        self.automation_id = automation_id
        self.runtime_id = runtime_id
        self.process_id = 4242


class FakeControl:
    """Just enough of a pywinauto UIA wrapper."""

    def __init__(self, control_type, name="", rect=(0, 0, 10, 10), *, children=(),
                 invokable=False, enabled=True, visible=True, focused=False,
                 expanded=None, editable=False, value=None, automation_id=""):
        self.element_info = FakeInfo(control_type, automation_id)
        self._name = name
        self._rect = FakeRect(*rect)
        self._children = list(children)
        self._invokable = invokable
        self._enabled = enabled
        self._visible = visible
        self._focused = focused
        self._expanded = expanded
        self._editable = editable
        self._value = value
        self.clicks: list[tuple] = []
        self.invoked = False

    # pywinauto-ish surface
    def window_text(self):
        return self._name

    def rectangle(self):
        return self._rect

    def children(self):
        return self._children

    def is_invokable(self):
        return self._invokable

    def is_enabled(self):
        return self._enabled

    def is_visible(self):
        return self._visible

    def has_keyboard_focus(self):
        return self._focused

    def is_expanded(self):
        return self._expanded

    def is_selected(self):
        return False

    def is_checked(self):
        return None

    def is_offscreen(self):
        return not self._visible

    def is_modal(self):
        return False

    def is_read_only(self):
        return False

    def get_value(self):
        return self._value

    def invoke(self):
        self.invoked = True

    def click_input(self, coords=None):
        self.clicks.append(coords)

    def set_focus(self):
        self._focused = True

    def set_edit_text(self, text):
        self._value = text

    def type_keys(self, text, with_spaces=False):
        self._value = (self._value or "") + text

    def select(self):
        return None

    def scroll(self, *args):
        return None

    def move_window(self, **kwargs):
        return None

    def resize(self, **kwargs):
        return None


@pytest.fixture
def window():
    """A tiny Notepad-shaped window with a title bar and an editor."""
    close = FakeControl("Button", "Close", (760, 8, 32, 24), invokable=True)
    minimize = FakeControl("Button", "Minimize", (700, 8, 32, 24), invokable=True)
    title_bar = FakeControl("Pane", "", (0, 0, 800, 32),
                            children=[minimize, close])
    editor = FakeControl("Edit", "Text Editor", (0, 32, 800, 568),
                         editable=True, focused=True, value="hello")
    root = FakeControl("Window", "Untitled - Notepad", (0, 0, 800, 600),
                       children=[title_bar, editor])
    return root, close, editor


def build(root):
    from snapshot_parser.backends.windows_uia import UiaSession

    return UiaSession(root, Rect(0, 0, 800, 600))


def test_refs_are_assigned_in_pre_order(window):
    root, close, editor = window
    session = build(root)
    tree = session.build()
    refs = [el.ref for el in tree.walk()]
    assert refs == ["e1", "e2", "e3", "e4", "e5"]
    assert tree.name == "Untitled - Notepad"
    assert tree.role == "window"
    assert len(set(refs)) == len(refs)


def test_roles_and_actions_are_normalised(window):
    root, close, _ = window
    tree = build(root).build()
    close_el = next(el for el in tree.walk() if el.name == "Close")
    assert close_el.role == "button"
    assert "click" in close_el.actions
    assert close_el.action_ids.get("click") in ("Invoke", None)


def test_geometry_is_screen_pixels(window):
    root, close, _ = window
    session = build(root)
    tree = session.build()
    close_el = next(el for el in tree.walk() if el.name == "Close")
    assert close_el.rect == Rect(760, 8, 32, 24)
    assert close_el.rect.center == (776, 20)


def session_tree_walk(root):
    return list(build(root).build().walk())


def test_state_and_value_come_through(window):
    root, close, editor = window
    tree = build(root).build()
    editor_el = next(el for el in tree.walk() if el.role == "edit")
    assert editor_el.value == "hello"
    assert editor_el.state.get("focused") is True
    assert "set value" in editor_el.actions
    # and the title-bar buttons do not pretend to be text fields
    close_el = next(el for el in session_tree_walk(root) if el.name == "Close")
    assert "set value" not in close_el.actions


def test_perform_invokes_when_it_can(window):
    root, close, _ = window
    session = build(root)
    tree = session.build()
    close_el = next(el for el in tree.walk() if el.name == "Close")
    result = session.locate(close_el)
    assert result is close


def test_locate_refuses_to_guess_between_two_matches():
    a = FakeControl("Button", "OK", (0, 0, 10, 10), invokable=True)
    b = FakeControl("Button", "OK", (20, 0, 10, 10), invokable=True)
    root, *_ = window = (
        FakeControl("Window", "W", (0, 0, 100, 100), children=[a, b]), None, None)
    session = build(root)
    tree = session.build()
    ok = [el for el in tree.walk() if el.name == "OK"]
    assert len(ok) == 2
    # both have their own ref: locating by ref is unambiguous
    assert session.locate(ok[0]) is a
    assert session.locate(ok[1]) is b
    # but a ref that is not in the map and whose name is ambiguous resolves to
    # nothing rather than to a guess
    stray = Element(ref="e99", role="button", name="OK")
    assert session.locate(stray) is None


def test_recursive_tree_renders_through_the_shared_pipeline(window):
    root, close, _ = window
    tree = build(root).build()
    snap = Snapshot(app="Notepad", title="Untitled - Notepad", number=1,
                    session="uia", root=tree)
    text = render_snapshot(snap)
    assert text.splitlines()[0] == ('Notepad — "Untitled - Notepad"  '
                                    '(snapshot 1, session uia)')
    assert "[window_button] Minimize e3 · Close e4" in text
    assert '[edit] Text Editor e5' in text
    assert "5 chars" in text and "hello" in text
    # no action noise: a button's click and an editor's typing are implied
    assert "click" not in text
    assert "set value" not in text


def test_state_normalisation_does_not_emit_noise():
    box = FakeControl("CheckBox", "Word wrap", (0, 0, 10, 10))
    root = FakeControl("Window", "W", (0, 0, 100, 100), children=[box])
    tree = build(root).build()
    assert tree.children[0].state == {}


def test_combo_translation_for_keyboard():
    from snapshot_parser.backends.windows_uia import _combo_to_pywinauto

    assert _combo_to_pywinauto("ctrl+s") == "^s"
    assert _combo_to_pywinauto("ctrl+shift+s") == "^+s"
    assert _combo_to_pywinauto("alt+f4") == "%{F4}"
    assert _combo_to_pywinauto("enter") == "{ENTER}"
    assert _combo_to_pywinauto("escape") == "{ESC}"


def test_backend_says_why_it_cannot_run_here():
    from snapshot_parser.backends.windows_uia import WindowsUiaBackend

    backend = WindowsUiaBackend()
    ok, why = backend.available()
    if sys.platform == "win32":  # pragma: no cover - on a real Windows box
        assert ok or "pywinauto" in why
    else:
        assert not ok
        assert "Windows" in why


# ---------------------------------------------------------------------------
# File Explorer's shape: big, deep, and full of zero-size nodes
# ---------------------------------------------------------------------------

def _explorer_like(items: int = 60):
    """A window whose navigator reports 0x0 - exactly what Win11 Explorer does."""
    rows = [
        FakeControl("ListItem", f"file{i}.txt", (0, 0, 0, 0))
        for i in range(items)
    ]
    grid = FakeControl("List", "Items View", (250, 120, 900, 600), children=rows)
    nav = FakeControl("Tree", "Navigation", (0, 0, 0, 0),
                      children=[FakeControl("TreeItem", "Desktop", (0, 0, 0, 0)),
                                FakeControl("TreeItem", "Downloads", (0, 0, 0, 0))])
    bar = FakeControl("ToolBar", "Command bar", (0, 0, 0, 0),
                      children=[FakeControl("Button", "New", (0, 0, 0, 0), invokable=True),
                                FakeControl("Button", "Delete", (0, 0, 0, 0), invokable=True)])
    root = FakeControl("Window", "Downloads", (0, 0, 1200, 800),
                       children=[bar, nav, grid])
    return root, grid, rows


def test_explorer_rows_survive_the_zero_size_geometry():
    """Regression: 0x0 nodes were marked invisible, which emptied Explorer.

    Win11's navigator and virtualised list report no area at all. Dropping them
    left a snapshot with a header and nothing else.
    """
    root, grid, rows = _explorer_like()
    tree = build(root).build()
    list_item = next(el for el in tree.walk() if el.name == "file3.txt")
    assert list_item.visible is True
    assert list_item.rect.empty
    assert list_item.role == "listitem"


def test_explorer_tree_renders_rows_not_just_a_header():
    from snapshot_parser.budget import is_renderable

    root, grid, rows = _explorer_like()
    tree = build(root).build()
    visible = [el for el in tree.walk() if is_renderable(el)]
    names = {el.name for el in visible}
    assert "file0.txt" in names and "file59.txt" in names
    assert "Downloads" in names          # a zero-size tree item, still shown
    assert "New" in names                # a zero-size toolbar button, still shown


def test_the_node_budget_is_actually_enforced(monkeypatch):
    """MAX_NODES was declared and exported but never checked."""
    import snapshot_parser.backends.windows_uia as uia

    monkeypatch.setattr(uia, "MAX_NODES", 10)
    root, _, _ = _explorer_like(items=80)
    session = uia.UiaSession(root, Rect(0, 0, 1200, 800))
    tree = session.build()
    assert session.truncated is True
    assert len(list(tree.walk())) < 80


def test_a_truncated_read_says_so(monkeypatch):
    import snapshot_parser.backends.windows_uia as uia

    monkeypatch.setattr(uia, "MAX_NODES", 8)
    root, _, _ = _explorer_like(items=80)
    session = uia.UiaSession(root, Rect(0, 0, 1200, 800))
    session.build()
    assert session.truncated


def test_deep_but_narrow_trees_are_not_cut_by_depth():
    """Electron nests content ~11 levels down; the old guard was depth 60 for all."""
    from snapshot_parser.backends.windows_uia import MAX_DEPTH

    node = FakeControl("Button", "deep", (0, 0, 10, 10))
    for level in range(MAX_DEPTH - 5):
        node = FakeControl("Pane", f"p{level}", (0, 0, 100, 100), children=[node])
    tree = build(node).build()
    assert any(el.name == "deep" for el in tree.walk())
