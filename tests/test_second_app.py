"""Step 25: a second app must render within the cap, without exceptions.

The parser should not be a Notepad parser.  This test builds a *different*
kind of window - a Calculator - from a capture shaped like the real one (a grid
of digit buttons, a display, a memory row) and a file manager with a huge tree,
and checks the output stays inside the contract.
"""

from __future__ import annotations

import os
import re

import pytest

from snapshot_parser.model import Rect, Snapshot
from snapshot_parser.reader import parse_raw_text, render_raw_text, read_snapshot
from snapshot_parser.render import RenderOptions, render_snapshot

from conftest import ROOT

CALC = os.path.join(ROOT, "tests", "fixtures", "calculator.raw.txt")
EXPLORER = os.path.join(ROOT, "tests", "fixtures", "explorer_big.raw.txt")


def test_calculator_renders_without_surprises():
    snap = read_snapshot(CALC)
    text = render_snapshot(snap)
    lines = text.splitlines()
    assert lines[0] == ('Calculator — "Calculator"  (snapshot 1, session local)')
    body = [line for line in lines[2:] if line.strip()]
    assert len(body) <= 81
    # digits group on one line, the display keeps its own
    assert any("7" in line and "8" in line and "9" in line for line in body)
    assert any(line.startswith("[text] Display") or "Display" in line
               for line in body)
    assert "0x" not in text


def test_calculator_keeps_every_interactive_ref():
    snap = read_snapshot(CALC)
    text = render_snapshot(snap, RenderOptions(full=True))
    for ref in re.findall(r"\b(e\d+)\b", text):
        assert snap.find(ref) is not None


def test_big_explorer_tree_respects_the_cap_and_collapses():
    snap = read_snapshot(EXPLORER)
    total = snap.count()
    assert total > 400
    # 80 lines is not enough for 500+ rows, so the overflow line must appear
    text = render_snapshot(snap, RenderOptions(max_lines=20))
    body = text.split("\n\n", 1)[1].splitlines()
    assert len(body) <= 21
    assert body[-1].startswith("... ")
    assert "inspect e" in body[-1]
    # and the same tree, at the default cap, must still be within it
    default_text = render_snapshot(snap)
    assert len(default_text.splitlines()) <= 82


def test_big_explorer_tree_survives_full_rendering():
    snap = read_snapshot(EXPLORER)
    text = render_snapshot(snap, RenderOptions(full=True))
    body = text.split("\n\n", 1)[1]
    assert "more" not in body
    # every one of the 500 rows is reachable
    refs = re.findall(r"\b(e\d+)\b", body)
    assert len(refs) >= 500


def test_big_tree_round_trips_through_the_raw_format():
    snap = read_snapshot(EXPLORER)
    again = parse_raw_text(render_raw_text(snap))
    assert again.count() == snap.count()


def test_large_tree_output_is_smaller_than_the_input():
    with open(EXPLORER, "r", encoding="utf-8") as handle:
        raw = handle.read()
    compact = render_snapshot(read_snapshot(EXPLORER))
    assert len(compact) < len(raw) / 2


def test_no_exception_on_a_window_with_only_a_root():
    snap = Snapshot(app="Shell", title="Desktop", number=1, session="local")
    snap.root = parse_raw_text(
        "agdesk-raw 1\n---\ne1|Window|Desktop|0,0,1920,1080\n"
    ).root
    text = render_snapshot(snap)
    assert text.startswith("Shell")


def test_window_with_no_accessible_children_is_honest():
    snap = parse_raw_text(
        "agdesk-raw 1\napp: Game\ntitle: Game\n---\ne1|Window|Game|0,0,800,600|SetFocus\n"
    )
    text = render_snapshot(snap)
    assert "Game" in text
    # a window with no children and no state prints its header and nothing
    # else: the header already names it, and nothing may be invented
    assert len(text.splitlines()) == 1
    assert "0x" not in text
