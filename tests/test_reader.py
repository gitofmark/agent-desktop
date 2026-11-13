"""Step 6: the reader - fixtures load into the expected element tree."""

from __future__ import annotations

import json
import os

import pytest

from snapshot_parser.model import Rect
from snapshot_parser.reader import (
    MAGIC,
    ReaderError,
    parse_raw_json,
    parse_raw_text,
    read_snapshot,
    render_raw_text,
)

from conftest import ALL_FIXTURES, fixture_path

#: Exact element counts, as required by Step 6.  If a fixture changes, the
#: number here changes with it - and FORMAT_NOTES.md records why.
EXPECTED_COUNTS = {
    "notepad.raw.txt": 20,
    "notepad_text.raw.txt": 20,
    "notepad_menu_open.raw.txt": 26,
    "notepad_save_dialog.raw.txt": 27,   # 20 background + 7 dialog
}

EXPECTED_REFS = {
    "notepad.raw.txt": ["e1", "e2", "e3", "e4", "e5", "e6", "e7", "e8", "e9",
                        "e10", "e11", "e12", "e13", "e14", "e15", "e16", "e17",
                        "e18", "e19", "e20"],
}


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_fixture_loads(name):
    snap = read_snapshot(fixture_path(name))
    assert snap.root is not None
    assert snap.app
    assert snap.title
    assert snap.count() > 5


@pytest.mark.parametrize("name,expected", EXPECTED_COUNTS.items())
def test_fixture_element_count(name, expected):
    snap = read_snapshot(fixture_path(name))
    total = sum(1 for _ in snap.walk())
    if snap.dialog is not None:
        total += sum(1 for _ in snap.dialog.walk())
    assert total == expected, f"{name}: {total} elements, expected {expected}"


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_refs_are_unique(name):
    snap = read_snapshot(fixture_path(name))
    refs = [el.ref for el in snap.walk()]
    if snap.dialog is not None:
        refs += [el.ref for el in snap.dialog.walk()]
    assert len(refs) == len(set(refs)), name


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_fixture_is_valid_json_parseable_text(name):
    """The fixture is text; it must still be plain UTF-8 with a magic header."""
    with open(fixture_path(name), "r", encoding="utf-8") as handle:
        text = handle.read()
    assert text.startswith(MAGIC)
    assert text.count("---") >= 1


def test_geometry_is_parsed_in_screen_pixels():
    snap = read_snapshot(fixture_path("notepad.raw.txt"))
    close = snap.find("e6")
    assert close.rect == Rect(1764, 8, 46, 32)
    assert close.rect.center == (1787, 24)


def test_names_and_roles_are_normalised():
    snap = read_snapshot(fixture_path("notepad.raw.txt"))
    close = snap.find("e6")
    assert close.role == "button"
    assert close.name == "Close"
    assert close.raw_role == "Button"
    assert close.actions == ["click"]
    assert close.action_ids == {"click": "Invoke"}


def test_state_is_normalised():
    snap = read_snapshot(fixture_path("notepad.raw.txt"))
    assert snap.find("e1").state["maximized"] is True
    assert snap.find("e13").state["focused"] is True


def test_text_value_and_length_property():
    snap = read_snapshot(fixture_path("notepad_text.raw.txt"))
    editor = snap.find("e13")
    assert editor.value and len(editor.value) == 2340
    assert editor.properties["chars"] == "2340"


def test_dialog_block_is_separate_from_the_window():
    snap = read_snapshot(fixture_path("notepad_save_dialog.raw.txt"))
    assert snap.dialog is not None
    assert snap.find("e40") is None, "dialog refs must not leak into the window tree"
    assert snap.dialog.find("e40").role == "dialog"
    assert snap.dialog.find("e40").name == "Save As"


def test_parent_links_are_set():
    snap = read_snapshot(fixture_path("notepad.raw.txt"))
    assert snap.find("e6").parent_ref == "e3"
    assert snap.find("e3").parent_ref == "e2"


# -- errors -------------------------------------------------------------
def test_empty_input_raises_readable_error():
    with pytest.raises(ReaderError) as exc:
        parse_raw_text("")
    assert "empty" in str(exc.value)


def test_wrong_magic_raises_readable_error():
    with pytest.raises(ReaderError) as exc:
        parse_raw_text("hello\nworld\n")
    assert "accessibility dump" in str(exc.value)


def test_unsupported_version():
    with pytest.raises(ReaderError) as exc:
        parse_raw_text("agdesk-raw 9\n---\ne1|Button|OK|0,0,10,10|Invoke\n")
    assert "version" in str(exc.value)


def test_bad_rect_is_reported_with_the_line():
    with pytest.raises(ReaderError) as exc:
        parse_raw_text(f"{MAGIC}\n---\ne1|Button|OK|nonsense|Invoke\n")
    assert "bad rect" in str(exc.value)


def test_missing_file_is_readable():
    with pytest.raises(ReaderError) as exc:
        read_snapshot("/no/such/file.raw.txt")
    assert "no such snapshot file" in str(exc.value)


def test_bad_json_is_readable(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ReaderError) as exc:
        read_snapshot(str(path))
    assert "valid JSON" in str(exc.value)


def test_root_must_be_at_depth_zero():
    with pytest.raises(ReaderError) as exc:
        parse_raw_text(f"{MAGIC}\n---\n e1|Button|OK|0,0,10,10|Invoke\n")
    assert "depth 0" in str(exc.value)


# -- escaping and round trip -------------------------------------------
def test_pipes_and_newlines_are_escaped():
    snap = parse_raw_text(
        f"{MAGIC}\n---\ne1|Text|a \\| b|0,0,10,10|||||visible|note=line1\\nline2\n"
    )
    assert snap.find("e1").name == "a | b"
    assert snap.find("e1").properties["note"] == "line1\nline2"


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_text_round_trip(name):
    original = read_snapshot(fixture_path(name))
    text = render_raw_text(original)
    again = parse_raw_text(text)
    assert again.app == original.app
    assert again.title == original.title
    assert again.count() == original.count()
    assert [el.ref for el in again.walk()] == [el.ref for el in original.walk()]
    assert [el.rect for el in again.walk()] == [el.rect for el in original.walk()]


# -- legacy JSON --------------------------------------------------------
def test_json_snapshot_shape():
    data = {
        "app": "Calculator",
        "title": "Calculator",
        "root": {
            "ref": "e1",
            "role": "Window",
            "name": "Calculator",
            "rect": {"x": 0, "y": 0, "width": 400, "height": 600},
            "state": {"focused": True},
            "children": [
                {"ref": "e2", "role": "Button", "name": "7", "actions": ["Invoke"],
                 "rect": [10, 20, 40, 40]},
                {"ref": "e3", "role": "Button", "name": "8", "actions": ["Invoke"],
                 "rect": [50, 20, 40, 40]},
            ],
        },
    }
    snap = parse_raw_json(data)
    assert snap.app == "Calculator"
    assert snap.count() == 3
    assert snap.find("e2").role == "button"
    assert snap.find("e2").rect == Rect(10, 20, 40, 40)
    assert snap.find("e1").state["focused"] is True
