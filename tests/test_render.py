"""Steps 10-13: rendering, grouping, preview, modal order, goldens.

The golden files are the contract.  They were written by reading section 1 of
the plan and then reviewed by hand; a change to them needs a note in
``walkthrough.md``.
"""

from __future__ import annotations

import os
import re

import pytest

from snapshot_parser.model import Element, Rect, Snapshot
from snapshot_parser.render import (
    MAX_PREVIEW,
    RenderOptions,
    preview_text,
    render_error,
    render_header,
    render_line,
    render_snapshot,
)

from conftest import ALL_FIXTURES, golden_path, load, read_golden

GOLDENS = {
    "notepad.raw.txt": "notepad.txt",
    "notepad_text.raw.txt": "notepad_text.txt",
    "notepad_menu_open.raw.txt": "notepad_menu_open.txt",
    "notepad_save_dialog.raw.txt": "notepad_save_dialog.txt",
}

BOUNDS_GOLDEN = "notepad_bounds.txt"


# -- header -------------------------------------------------------------
def test_header_states_app_title_snapshot_and_session():
    snap = Snapshot(app="Notepad", title="snap.js - Notepad", number=14,
                    session="syzqsppsg5kob")
    header = render_header(snap)
    assert header == 'Notepad — "snap.js - Notepad"  (snapshot 14, session syzqsppsg5kob)'
    assert header.count("snapshot") == 1


def test_header_without_title_or_app():
    assert render_header(Snapshot(app="Explorer", number=2, session="s")) == \
        "Explorer  (snapshot 2, session s)"
    assert "?unknown" in render_header(Snapshot())


def test_error_renders_one_line_not_a_traceback():
    text = render_error("access denied: that window runs elevated")
    assert text.startswith("!")
    assert "Traceback" not in text
    assert "\n" not in text


def test_error_keeps_the_header_when_known():
    snap = Snapshot(app="Notepad", title="x", number=3, session="s",
                    error="the window closed while it was being read")
    text = render_snapshot(snap)
    assert text.startswith("Notepad —")
    assert "! the window closed" in text
    assert "Traceback" not in text


# -- one line -----------------------------------------------------------
def test_render_line_shape():
    el = Element(ref="e10", role="button", name="Close", actions=["click"],
                 rect=Rect(1842, 12, 46, 32))
    assert render_line(el) == "[button] Close e10"


def test_render_line_shows_non_default_action():
    el = Element(ref="e1", role="button", name="Options", actions=["click", "expand"],
                 state={"disabled": True})
    assert render_line(el) == "[button] Options e1 — disabled"


def test_render_line_hides_implied_action():
    el = Element(ref="e1", role="button", name="OK", actions=["click"])
    assert render_line(el) == "[button] OK e1"


# -- step 12: editor preview -------------------------------------------
def test_long_text_is_cut_at_80_chars_and_never_printed_in_full():
    long_text = "x" * 500
    el = Element(ref="e1", role="edit", name="Text Editor", value=long_text)
    line = render_line(el)
    assert "500 chars" in line
    assert "xxx" in line
    assert long_text not in line
    preview = re.search(r'starts "(.*)"', line).group(1)
    assert len(preview) <= MAX_PREVIEW + 3
    assert preview.endswith("...")


def test_preview_text_strips_newlines_and_quotes():
    assert preview_text('a\nb\tc "quoted"') == "a b c 'quoted'"
    assert preview_text("short") == "short"
    assert len(preview_text("y" * 200)) == MAX_PREVIEW + 3


def test_full_text_never_appears_in_a_rendered_snapshot():
    snap = load("notepad_text")
    text = render_snapshot(snap)
    editor = snap.find("e13")
    assert editor.value and len(editor.value) > 200
    # the first 80 characters may show; the rest must not
    assert editor.value[81:160] not in text


def test_multiline_text_reports_lines():
    el = Element(ref="e1", role="edit", name="Text Editor", value="a\nb\nc")
    assert "5 chars, 3 lines" in render_line(el)


def test_empty_editor_reports_zero_chars():
    el = Element(ref="e1", role="edit", name="Text Editor", value="")
    assert render_line(el) == "[edit] Text Editor e1 — 0 chars"


# -- step 11: grouping --------------------------------------------------
def test_siblings_of_the_same_role_group_on_one_line():
    snap = load("notepad")
    text = render_snapshot(snap)
    assert "[window_button] Minimize e4 · Maximize e5 · Close e6" in text
    assert "[menu] File e8 · Edit e9 · Format e10 · View e11 · Help e12" in text


def test_sibling_with_state_gets_its_own_line():
    root = Element(ref="e1", role="window", name="W", rect=Rect(0, 0, 100, 100))
    root.add(Element(ref="e2", role="button", name="A"))
    root.add(Element(ref="e3", role="button", name="B", state={"disabled": True}))
    root.add(Element(ref="e4", role="button", name="C"))
    lines = render_snapshot(Snapshot(root=root, session="s")).splitlines()
    body = [line for line in lines if line.startswith("[")]
    # a run is not interrupted by an element that needs its own line, but a
    # singleton run keeps element order and gets its own line (no reordering)
    assert body == ["[button] A e2", "[button] B e3 — disabled", "[button] C e4"]


def test_children_stop_grouping():
    root = Element(ref="e1", role="window", name="W")
    a = root.add(Element(ref="e2", role="listitem", name="A"))
    a.add(Element(ref="e3", role="button", name="Open"))
    root.add(Element(ref="e4", role="listitem", name="B"))
    text = render_snapshot(Snapshot(root=root, session="s"))
    assert "[button] Open e3" in text


def test_grouping_can_be_disabled():
    snap = load("notepad")
    text = render_snapshot(snap, RenderOptions(group=False))
    assert "[window_button]" not in text
    assert "[button] Minimize e4" in text


# -- step 13: modal first ----------------------------------------------
def test_disabled_action_is_not_printed():
    el = Element(ref="e1", role="link", name="Docs", actions=["click", "expand"],
                 state={"disabled": True})
    assert render_line(el) == "[link] Docs e1 — disabled"


def test_modal_dialog_comes_first_and_marks_the_background():
    snap = load("notepad_save_dialog")
    lines = [line for line in render_snapshot(snap).splitlines() if line.strip()]
    assert lines[0].startswith("Notepad")
    body = lines[1:]
    assert body[0].startswith("[dialog] Save As e40")
    background = [i for i, line in enumerate(body) if "(background)" in line]
    assert background, "the window behind a modal dialog must be marked (background)"
    dialog_lines = [i for i, line in enumerate(body) if line.startswith("[dialog]")]
    assert dialog_lines[0] < background[0]


def test_dialog_actions_come_before_background_actions():
    snap = load("notepad_save_dialog")
    text = render_snapshot(snap)
    assert text.index("Save e45") < text.index("Close e6")


# -- R5/R10: what must never leak --------------------------------------
def test_default_snapshot_has_no_bounds_and_no_native_ids():
    for name in ALL_FIXTURES:
        text = render_snapshot(load(name))
        assert "0x000" not in text, name
        assert not re.search(r"\d+,\d+ \d+x\d+", text), name


def test_bounds_option_prints_geometry_inline():
    snap = load("notepad")
    text = render_snapshot(snap, RenderOptions(show_bounds=True))
    assert "Close e6 @1764,8 46x32" in text
    assert "Minimize e4 @1660,8 46x32" in text


# -- goldens ------------------------------------------------------------
@pytest.mark.parametrize("fixture,expected_file", GOLDENS.items())
def test_golden(fixture, expected_file):
    actual = render_snapshot(load(fixture))
    path = golden_path(expected_file)
    assert os.path.exists(path), f"missing golden {path}"
    assert actual == read_golden(path)


def test_bounds_golden():
    actual = render_snapshot(load("notepad"), RenderOptions(show_bounds=True))
    assert actual == read_golden(golden_path(BOUNDS_GOLDEN))


def test_ascii_mode_is_plain():
    snap = load("notepad")
    text = render_snapshot(snap, RenderOptions(ascii=True))
    assert "·" not in text and "—" not in text
    assert " | " in text


def test_rendering_is_deterministic():
    snap = load("notepad_text")
    assert render_snapshot(snap) == render_snapshot(snap)
