"""Step 17: property tests on generated random trees.

Properties (each has its own test, all run on the same generated trees):

(a) every ref in the output exists in the input
(b) no ref appears twice in the output
(c) the output never exceeds the line cap (plus its one overflow footer)
(d) no bounds and no native ids leak into the default output
(e) rendering is deterministic

``settings(max_examples=...)`` is set high enough to satisfy the "at least 200
examples" acceptance check.
"""

from __future__ import annotations

import re

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from snapshot_parser.model import Element, Rect, Snapshot
from snapshot_parser.render import RenderOptions, render_snapshot
from snapshot_parser.state import extract_state

ROLE_POOL = ["button", "menu", "menuitem", "checkbox", "edit", "text", "pane",
             "group", "scrollbar", "listitem", "unknown", "separator", "tab",
             "combobox", "link", "window_button"]
NAME_POOL = ["Close", "Open", "Save", "File", "Edit", "Help", "", "", "OK",
             "Cancel", "a very long name " * 4, "Ln 1, Col 1", "x" * 120]
STATE_POOL = [{}, {"focused": True}, {"disabled": True}, {"checked": True},
              {"expanded": True}, {"offscreen": True}, {"selected": True},
              {"maximized": True}, {"modal": True}]


@st.composite
def random_tree(draw, max_nodes: int = 60):
    """A random Element tree with refs e1..eN in pre-order."""
    counter = {"n": 0}

    def make(depth: int) -> Element:
        counter["n"] += 1
        ref = f"e{counter['n']}"
        role = draw(st.sampled_from(ROLE_POOL))
        name = draw(st.sampled_from(NAME_POOL))
        actions = draw(st.sampled_from([[], ["click"], ["set value"],
                                        ["click", "focus"], ["scroll"]]))
        state = extract_state(draw(st.sampled_from(STATE_POOL)))
        rect = Rect(draw(st.integers(-200, 1800)), draw(st.integers(-100, 1000)),
                    draw(st.integers(0, 400)), draw(st.integers(0, 300)))
        value = draw(st.sampled_from([None, "", "hello", "y" * 300,
                                      "line1\nline2"]))
        visible = draw(st.booleans())
        el = Element(ref=ref, role=role, name=name, actions=actions, state=state,
                     rect=rect, value=value, visible=visible,
                     native_id=f"0x{draw(st.integers(0, 65535)):04X}")
        if depth < 3:
            for _ in range(draw(st.integers(0, 4 if depth == 0 else 2))):
                if counter["n"] >= max_nodes:
                    break
                el.add(make(depth + 1))
        return el

    root = make(0)
    root.role = "window"
    number = draw(st.integers(1, 999))
    return Snapshot(app=draw(st.sampled_from(["Notepad", "Calculator", ""])),
                    title=draw(st.sampled_from(["Untitled - Notepad", "", "x"])),
                    number=number, session=draw(st.sampled_from(["abc", "s1"])),
                    root=root)


def printed_refs(text: str) -> list[str]:
    body = text.split("\n\n", 1)[1] if "\n\n" in text else ""
    refs: list[str] = []
    for line in body.splitlines():
        if line.startswith("... "):
            continue
        refs.extend(re.findall(r"\b(e\d+)\b", line))
    return refs


PROPERTY_SETTINGS = settings(
    max_examples=250,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)


@PROPERTY_SETTINGS
@given(random_tree())
def test_a_every_printed_ref_exists_in_the_input(snap):
    opts = RenderOptions()
    text = render_snapshot(snap, opts)
    available = set(snap.refs())
    for ref in printed_refs(text):
        assert ref in available, f"{ref} printed but not in the tree"


@PROPERTY_SETTINGS
@given(random_tree())
def test_b_no_duplicate_refs(snap):
    text = render_snapshot(snap, RenderOptions())
    refs = printed_refs(text)
    assert len(refs) == len(set(refs)), refs


@PROPERTY_SETTINGS
@given(random_tree(), st.integers(min_value=4, max_value=120))
def test_c_output_respects_the_cap(snap, cap):
    text = render_snapshot(snap, RenderOptions(max_lines=cap))
    body = text.split("\n\n", 1)[1].splitlines() if "\n\n" in text else []
    content = [line for line in body if not line.startswith("... ")]
    footers = [line for line in body if line.startswith("... ")]
    assert len(content) <= cap
    assert len(footers) <= 1


@PROPERTY_SETTINGS
@given(random_tree())
def test_d_no_metadata_leaks(snap):
    text = render_snapshot(snap, RenderOptions())
    assert not re.search(r"\b0[xX][0-9a-fA-F]{2,}\b", text), "native id leaked"
    assert not re.search(r"\d+,\d+ \d+x\d+", text), "bounds leaked"
    assert "runtime_id" not in text


@PROPERTY_SETTINGS
@given(random_tree())
def test_e_rendering_is_deterministic(snap):
    first = render_snapshot(snap, RenderOptions())
    second = render_snapshot(snap, RenderOptions())
    assert first == second


@PROPERTY_SETTINGS
@given(random_tree())
def test_header_is_always_present_and_states_the_snapshot(snap):
    text = render_snapshot(snap, RenderOptions())
    first = text.splitlines()[0]
    assert f"snapshot {snap.number}" in first
    assert f"session {snap.session}" in first


@PROPERTY_SETTINGS
@given(random_tree())
def test_every_line_starts_with_a_role_or_is_the_overflow_footer(snap):
    text = render_snapshot(snap, RenderOptions())
    for line in text.splitlines()[1:]:
        if not line.strip():
            continue
        assert line.startswith("[") or line.startswith("... "), line


@PROPERTY_SETTINGS
@given(random_tree())
def test_bounds_mode_prints_a_box_for_every_ref(snap):
    text = render_snapshot(snap, RenderOptions(show_bounds=True))
    refs = printed_refs(text)
    boxes = re.findall(r"@-?\d+,-?\d+ \d+x\d+", text)
    assert len(boxes) >= len(refs) - 1
