"""Step 5: the model - construct, walk, find by ref, parent links."""

from __future__ import annotations

import pytest

from snapshot_parser.model import DEFAULT_ACTIONS, Element, Rect, Snapshot, link_parents


def build():
    root = Element(ref="e1", role="window", name="Notepad", rect=Rect(0, 0, 800, 600))
    pane = root.add(Element(ref="e2", role="pane", name=""))
    close = pane.add(Element(ref="e3", role="button", name="Close",
                             actions=["click"], rect=Rect(760, 8, 32, 24)))
    editor = root.add(Element(ref="e4", role="edit", name="Text Editor",
                              actions=["set value", "focus"], value="hello"))
    return root, pane, close, editor


def test_element_defaults():
    el = Element()
    assert el.ref == ""
    assert el.role == "unknown"
    assert el.children == []
    assert el.visible is True
    assert el.rect == Rect()
    assert el.is_leaf


def test_add_and_is_leaf():
    root, pane, close, editor = build()
    assert not root.is_leaf
    assert close.is_leaf
    assert root.children[0] is pane
    assert pane.children[0] is close


def test_walk_is_depth_first_pre_order():
    root, pane, close, editor = build()
    assert [el.ref for el in root.walk()] == ["e1", "e2", "e3", "e4"]


def test_find_by_ref():
    root, pane, close, editor = build()
    assert root.find("e3") is close
    assert root.find("e4") is editor
    assert root.find("e99") is None


def test_descendants_excludes_self():
    root, *_ = build()
    assert [el.ref for el in root.descendants()] == ["e2", "e3", "e4"]


def test_link_parents():
    root, pane, close, editor = build()
    link_parents(root)
    assert close.parent_ref == "e2"
    assert pane.parent_ref == "e1"
    assert root.parent_ref == ""


def test_default_actions_are_not_printed_reasons():
    el = Element(ref="e1", role="button", name="OK", actions=["click", "focus"])
    assert "focus" in DEFAULT_ACTIONS
    assert el.non_default_actions() == ["click"]


def test_has_action():
    _, _, close, _ = build()
    assert close.has_action("click")
    assert not close.has_action("scroll")


# -- Rect ---------------------------------------------------------------
def test_rect_center_and_edges():
    rect = Rect(100, 200, 50, 20)
    assert rect.center == (125, 210)
    assert (rect.right, rect.bottom) == (150, 220)
    assert not rect.empty
    assert Rect(10, 10, 0, 5).empty


def test_rect_contains_and_offset():
    rect = Rect(100, 200, 50, 20)
    assert rect.contains(100, 200)
    assert rect.contains(149, 219)
    assert not rect.contains(150, 220)
    assert rect.offset(100, 200) == Rect(0, 0, 50, 20)


def test_rect_str_is_the_bounds_form():
    assert str(Rect(1842, 12, 46, 32)) == "1842,12 46x32"


# -- Snapshot -----------------------------------------------------------
def test_snapshot_links_parents_on_construction():
    root, pane, close, _ = build()
    snap = Snapshot(app="Notepad", title="Untitled", number=3, session="abc", root=root)
    assert close.parent_ref == "e2"
    assert snap.count() == 4
    assert snap.find("e3") is close
    assert snap.refs() == ["e1", "e2", "e3", "e4"]
    assert snap.window_rect() == Rect(0, 0, 800, 600)


def test_snapshot_full_ref():
    snap = Snapshot(session="syzqsppsg5kob", root=Element(ref="e10"))
    assert snap.full_ref("e10") == "@syzqsppsg5kob:e10"


def test_snapshot_error_state():
    snap = Snapshot(error="access denied")
    assert not snap.ok
    assert snap.count() == 0
    assert snap.find("e1") is None


def test_bounds_snapshot_defaults_to_number():
    snap = Snapshot(number=7, session="s", root=Element(ref="e1"))
    assert snap.bounds_snapshot == 7
