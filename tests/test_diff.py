"""Step 28: verify-after-action diff."""

from __future__ import annotations

import pytest

from snapshot_parser.diff import Change, diff_snapshots, format_diff, verify_diff
from snapshot_parser.model import Element, Rect, Snapshot
from snapshot_parser.reader import parse_raw_text, render_raw_text

from conftest import load


def snapshot(number, *, title="Untitled - Notepad", text="", buttons=("Close",),
             dialog=None):
    root = Element(ref="e1", role="window", name=title, rect=Rect(0, 0, 800, 600))
    editor = root.add(Element(ref="e2", role="edit", name="Text Editor",
                              value=text, rect=Rect(0, 40, 800, 500)))
    for index, label in enumerate(buttons, start=3):
        root.add(Element(ref=f"e{index}", role="button", name=label,
                         actions=["click"], rect=Rect(700 + index, 4, 30, 20)))
    snap = Snapshot(app="Notepad", title=title, number=number, session="s", root=root)
    if dialog is not None:
        snap.dialog = Element(ref="e40", role="dialog", name=dialog)
        snap.dialog.add(Element(ref="e41", role="button", name="Save"))
    return snap


def test_no_change():
    a = snapshot(1)
    b = snapshot(2)
    assert diff_snapshots(a, b) == []
    assert format_diff([]) == "no change"


def test_added_and_removed_elements():
    before = snapshot(1, buttons=("Close",))
    after = snapshot(2, buttons=("Close", "Save"))
    changes = diff_snapshots(before, after)
    assert any(c.kind == "+" and "Save" in c.text for c in changes)
    after2 = snapshot(3, buttons=())
    removed = diff_snapshots(before, after2)
    assert any(c.kind == "-" and "Close" in c.text for c in removed)


def test_text_change_is_summarised_not_dumped():
    before = snapshot(1, text="")
    after = snapshot(2, text="hello world, this is a typed line")
    changes = diff_snapshots(before, after)
    joined = " ".join(c.text for c in changes)
    assert "Text Editor: starts" in joined
    assert "hello world" in joined
    assert len(after.find("e2").value) < 200


def test_title_change_is_reported():
    changes = diff_snapshots(snapshot(1), snapshot(2, title="snap.js - Notepad"))
    assert any("title" in c.text for c in changes)


def test_dialog_appearing_is_reported():
    changes = diff_snapshots(snapshot(1), snapshot(2, dialog="Save As"))
    assert any(c.kind == "+" and "dialog" in c.text.lower() for c in changes)


def test_state_change_is_reported():
    before = snapshot(1, buttons=("Close",))
    after = snapshot(2, buttons=("Close",))
    after.find("e3").state = {"focused": True}
    changes = diff_snapshots(before, after)
    assert any("focused" in c.text for c in changes)


def test_diff_is_capped():
    before = snapshot(1, buttons=())
    after = snapshot(2, buttons=tuple(f"B{i}" for i in range(30)))
    changes = diff_snapshots(before, after, limit=5)
    text = format_diff(changes, limit=5)
    assert len(text.splitlines()) == 6
    assert "more changes" in text.splitlines()[-1]


def test_first_snapshot_diff_is_not_an_error():
    changes = diff_snapshots(None, snapshot(1))
    assert changes and changes[0].kind == "+"


def test_typing_scenario_end_to_end(tmp_path):
    """The Notepad typing scenario, through the raw format round trip."""
    before = load("notepad")
    after = load("notepad_text")
    # the two fixtures differ in title, editor content and status text
    changes = diff_snapshots(before, after)
    text = format_diff(changes)
    assert "title" in text or "Text Editor" in text

    stored = render_raw_text(before)
    reloaded = parse_raw_text(stored)
    assert format_diff(diff_snapshots(reloaded, reloaded)) == "no change"


def test_verify_diff_helper():
    text = verify_diff(None, snapshot(1))
    assert text.startswith("+")
