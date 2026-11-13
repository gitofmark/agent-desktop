"""Step 15: visible-only, line cap, overflow collapse (R3, R7)."""

from __future__ import annotations

import re

import pytest

from snapshot_parser.budget import (
    DEFAULT_MAX_LINES,
    LineItem,
    cap_lines,
    is_interesting,
    is_renderable,
    overflow_line,
    renderable_elements,
)
from snapshot_parser.model import Element, Rect, Snapshot
from snapshot_parser.render import RenderOptions, render_snapshot

from conftest import load


# -- R3: the interesting filter ----------------------------------------
def test_named_element_is_interesting():
    assert is_interesting(Element(ref="e1", role="button", name="OK"))


def test_element_with_an_action_is_interesting():
    assert is_interesting(Element(ref="e1", role="button", name="", actions=["click"]))


def test_element_with_a_value_is_interesting():
    assert is_interesting(Element(ref="e1", role="edit", name="", value="text"))


def test_anonymous_container_is_not_interesting():
    assert not is_interesting(Element(ref="e1", role="pane", name=""))


def test_anonymous_leaf_without_actions_is_not_interesting():
    assert not is_interesting(Element(ref="e1", role="separator", name=""))


def test_nothing_with_actions_is_dropped():
    for role in ("pane", "group", "unknown", "separator", "image"):
        el = Element(ref="e1", role=role, name="", actions=["click"])
        assert is_renderable(el), role


def test_nothing_with_a_name_is_dropped():
    for role in ("pane", "group", "unknown"):
        assert is_renderable(Element(ref="e1", role=role, name="Named"))


def test_hidden_and_offscreen_are_dropped_by_default():
    hidden = Element(ref="e1", role="button", name="X", visible=False)
    assert not is_renderable(hidden)
    assert is_renderable(hidden, include_hidden=True)
    off = Element(ref="e2", role="button", name="Y", state={"offscreen": True})
    assert not is_renderable(off)
    assert is_renderable(off, include_offscreen=True)


def test_dropped_container_keeps_its_children_visible():
    root = Element(ref="e1", role="window", name="W")
    pane = root.add(Element(ref="e2", role="pane", name=""))
    pane.add(Element(ref="e3", role="button", name="Deep", actions=["click"]))
    text = render_snapshot(Snapshot(root=root, session="s"))
    assert "Deep e3" in text
    assert "e2" not in text


# -- the cap ------------------------------------------------------------
def big_tree(count=500, width=60):
    root = Element(ref="e1", role="window", name="Big", rect=Rect(0, 0, 1000, 800))
    for i in range(2, count + 2):
        root.add(Element(ref=f"e{i}", role="button", name=f"Item {i}",
                         actions=["click"], rect=Rect(10, i, 80, 20)))
    return Snapshot(app="Big", title="Big", number=7, session="s", root=root)


def test_cap_limits_the_number_of_lines():
    snap = big_tree()
    text = render_snapshot(snap, RenderOptions(max_lines=20))
    body = text.split("\n\n", 1)[1].splitlines()
    # 20 content lines plus the one overflow footer
    assert len(body) == 21
    assert body[-1].startswith("... ")
    assert body[-1].endswith(")")


def test_default_cap_is_eighty():
    assert DEFAULT_MAX_LINES == 80
    text = render_snapshot(big_tree(), RenderOptions())
    body = text.split("\n\n", 1)[1].splitlines()
    content = [line for line in body if line.startswith("[")]
    # 501 elements grouped 8 per line = 63 content lines: under the cap, so
    # nothing is dropped and no footer is needed
    assert len(content) == 63
    assert len(body) == 63
    assert not any(line.startswith("... ") for line in body)


def test_overflow_line_counts_and_names_an_inspectable_ref():
    text = render_snapshot(big_tree(), RenderOptions(max_lines=20))
    last = text.splitlines()[-1]
    assert last.startswith("... ")
    assert re.search(r"\d+ more buttons", last)
    match = re.search(r"\(inspect (e\d+)\)", last)
    assert match, last
    ref = match.group(1)
    assert snap_has(big_tree(), ref)


def snap_has(snap, ref):
    return snap.find(ref) is not None


def test_overflow_line_survives_the_cap():
    items = [LineItem(text=f"line {i}", role="button", ref=f"e{i}") for i in range(100)]
    lines = cap_lines(items, 10)
    assert len(lines) == 11
    assert lines[:10] == [f"line {i}" for i in range(10)]
    assert lines[-1].startswith("... 90 more buttons")
    assert "(inspect e10)" in lines[-1]


def test_full_disables_the_cap():
    snap = big_tree(200)
    text = render_snapshot(snap, RenderOptions(full=True))
    assert "more buttons (" not in text
    body = text.split("\n\n", 1)[1].splitlines()
    # 201 elements, 8 refs per grouped line, no cap and no overflow footer
    assert len(body) == 25
    assert body[0].startswith("[button] Item 2 e2")
    refs = [ref for line in body for ref in line.split(" · ")]
    assert len(refs) >= 200


def test_no_cap_when_max_lines_is_zero():
    items = [LineItem(text=f"l{i}", role="button") for i in range(10)]
    assert cap_lines(items, 0) == [f"l{i}" for i in range(10)]


def test_small_tree_is_not_capped():
    snap = load("notepad")
    text = render_snapshot(snap, RenderOptions(max_lines=80))
    assert "more" not in text


# -- overflow text ------------------------------------------------------
def test_overflow_line_groups_by_role():
    dropped = [
        LineItem(text="a", role="button", ref="e1"),
        LineItem(text="b", role="button", ref="e2"),
        LineItem(text="c", role="menu", ref="e3"),
    ]
    line = overflow_line(dropped)
    assert "2 more buttons" in line
    assert "1 more menus" in line
    assert line.endswith("(inspect e1)")


def test_overflow_line_of_nothing_is_empty():
    assert overflow_line([]) == ""


# -- visible-only across the fixtures ----------------------------------
@pytest.mark.parametrize("name", ["notepad", "notepad_text", "notepad_menu_open"])
def test_renderable_elements_are_all_interesting(name):
    snap = load(name)
    for el in renderable_elements(snap):
        assert is_interesting(el)
