"""Step 16: inspect returns what the compact snapshot deliberately omits."""

from __future__ import annotations

import json

import pytest

from snapshot_parser.inspect import (
    bounds_of,
    find_elements,
    format_bounds_line,
    format_inspect,
    inspect_to_dict,
    read_value,
)
from snapshot_parser.refs import RefError

from conftest import load


def test_inspect_has_everything_the_snapshot_drops():
    snap = load("notepad")
    data = inspect_to_dict(snap, "e6")
    assert data["ref"] == "e6"
    assert data["full_ref"] == "@local:e6"
    assert data["role"] == "button"
    assert data["raw_role"] == "Button"
    assert data["name"] == "Close"
    assert data["native_id"] == "0x0006"
    assert data["actions"] == ["click"]
    assert data["actions_raw"] == {"click": "Invoke"}
    assert data["parent"] == "e3"
    assert "geometry" in data


def test_inspect_geometry_for_close():
    """Step 16 acceptance: Close has a real box and its center is inside it."""
    snap = load("notepad")
    geo = inspect_to_dict(snap, "e6")["geometry"]
    assert geo["width"] > 0 and geo["height"] > 0
    assert geo["x"] <= geo["cx"] <= geo["x"] + geo["width"]
    assert geo["y"] <= geo["cy"] <= geo["y"] + geo["height"]
    assert geo["cx"] == geo["x"] + geo["width"] // 2
    assert geo["offscreen"] is False
    assert geo["snapshot"] == snap.number


def test_inspect_window_relative_box():
    snap = load("notepad")
    geo = inspect_to_dict(snap, "e6")["geometry"]
    assert (geo["rx"], geo["ry"]) == (1764, 8)


def test_inspect_returns_the_full_text_that_the_snapshot_hides():
    snap = load("notepad_text")
    data = inspect_to_dict(snap, "e13")
    assert data["value"].startswith("const snap = render(snapshot);")
    assert data["value_length"] == 2340
    assert len(data["value"]) == 2340


def test_inspect_formats_a_readable_block():
    snap = load("notepad")
    text = format_inspect(snap, "e6")
    assert text.splitlines()[0].startswith("[button] Close e6")
    assert "native id   0x0006" in text
    assert "geometry    x=1764 y=8 w=46 h=32 center=1787,24 window=1764,8" in text
    assert "actions     click" in text


def test_inspect_json_round_trips():
    snap = load("notepad")
    payload = json.loads(json.dumps(inspect_to_dict(snap, "e6")))
    assert payload["geometry"]["width"] == 46


def test_inspect_unknown_ref_raises_the_same_error_as_validate():
    snap = load("notepad")
    with pytest.raises(RefError) as exc:
        inspect_to_dict(snap, "e999")
    assert exc.value.exit_code == 3
    assert "unknown ref" in str(exc.value)


def test_inspect_stale_ref_is_rejected():
    from snapshot_parser.model import Element, Snapshot

    snap = Snapshot(app="Notepad", title="Untitled", number=6, session="s",
                    root=Element(ref="e1", role="window", name="Notepad"))
    snap.root.add(Element(ref="e2", role="button", name="Close", actions=["click"]))
    with pytest.raises(RefError) as exc:
        inspect_to_dict(snap, "e2", snapshot_number=5)
    assert "snapshot 5" in str(exc.value)
    assert exc.value.exit_code == 3


def test_bounds_line():
    snap = load("notepad")
    line = format_bounds_line(snap, "e6")
    assert line.startswith("Close e6 @ 1764,8 46x32")
    assert "center 1787,24" in line


def test_bounds_dict():
    snap = load("notepad")
    geo = bounds_of(snap, "e14")
    assert geo["role"] == "scrollbar"
    assert geo["width"] == 20


def test_read_returns_the_whole_value():
    snap = load("notepad_text")
    text = read_value(snap, "e13")
    assert len(text) == 2340


def test_read_refuses_elements_without_text():
    snap = load("notepad")
    with pytest.raises(RefError):
        read_value(snap, "e6")


def test_find_matches_by_name_and_role():
    snap = load("notepad")
    assert [row["ref"] for row in find_elements(snap, "close")] == ["e6"]
    assert [row["ref"] for row in find_elements(snap, "e", role="edit")] == ["e13"]
    assert find_elements(snap, "no such thing") == []


def test_find_needs_text():
    snap = load("notepad")
    with pytest.raises(RefError) as exc:
        find_elements(snap, "")
    assert exc.value.exit_code == 2


def test_every_ref_can_be_inspected_round_trip():
    """Step 18: inspect resolves every ref the renderer prints."""
    from snapshot_parser.render import render_snapshot

    for name in ("notepad", "notepad_text", "notepad_menu_open", "notepad_save_dialog"):
        snap = load(name)
        text = render_snapshot(snap)
        printed = set(__import__("re").findall(r"\b(e\d+)\b", text))
        assert printed, name
        for ref in printed:
            data = inspect_to_dict(snap, ref)
            assert data["ref"] == ref
            # the same name and role the line showed
            el = snap.find(ref) or snap.dialog.find(ref)
            assert data["name"] == el.name
            assert data["role"] == el.role
