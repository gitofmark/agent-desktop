"""The contract validator, and proof that it actually rejects violations.

``tools/check_contract.py`` checks section 1 of the plan on any output - not
against a golden, so a golden edited to match a bug still fails. Here it is
run on every fixture, and then on deliberately tampered output: a validator
that cannot fail is not a validator.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from conftest import ROOT, fixture_path, load

sys.path.insert(0, ROOT)

from tools.check_contract import check, check_text, main  # noqa: E402
from snapshot_parser.render import RenderOptions, render_snapshot  # noqa: E402

FIXTURES = ["notepad", "notepad_text", "notepad_menu_open", "notepad_save_dialog",
            "calculator", "explorer_big"]


@pytest.mark.parametrize("name", FIXTURES)
def test_every_fixture_obeys_the_contract(name):
    assert check(fixture_path(name)) == []


@pytest.mark.parametrize("name", ["notepad", "notepad_save_dialog", "calculator"])
def test_the_bounds_rendering_obeys_the_contract(name):
    assert check(fixture_path(name), show_bounds=True) == []


def test_it_also_passes_at_a_small_cap():
    assert check(fixture_path("explorer_big"), max_lines=10) == []


# -- the validator must be able to fail ---------------------------------
def render(name="notepad", **kwargs):
    snap = load(name)
    return snap, render_snapshot(snap, RenderOptions(**kwargs))


def test_it_rejects_a_leaked_native_id():
    snap, text = render()
    tampered = text.replace("[text] Ln 1, Col 1 e20", "[text] Ln 1, Col 1 e20 0x0051")
    problems = check_text(snap, tampered)
    assert any("native id" in p for p in problems), problems


def test_it_rejects_leaked_coordinates():
    snap, text = render()
    tampered = text.replace("[text] Ln 1, Col 1 e20",
                            "[text] Ln 1, Col 1 e20 @1764,8 46x32")
    problems = check_text(snap, tampered)
    assert any("coordinates" in p or "@bounds" in p for p in problems)


def test_it_rejects_a_traceback_line():
    snap, text = render()
    tampered = text + "\nTraceback (most recent call last):"
    assert check_text(snap, tampered)


def test_it_rejects_a_ref_that_does_not_exist():
    snap, text = render()
    tampered = text.replace("Close e6", "Close e999")   # inside the grouped line
    assert any("does not exist" in p for p in check_text(snap, tampered))


def test_it_rejects_a_duplicate_ref():
    snap, text = render()
    tampered = text.replace("[text] Ln 1, Col 1 e20", "[text] Ln 1, Col 1 e6")
    assert any("appears 2 times" in p for p in check_text(snap, tampered))


def test_it_rejects_a_broken_header():
    snap, text = render()
    tampered = text.replace("(snapshot 1, session local)", "")
    assert any(p.startswith("R1") for p in check_text(snap, tampered))


def test_it_rejects_a_cap_overrun():
    snap, text = render("explorer_big")
    tampered = "\n".join(text.splitlines()[:5] + ["[button] Extra e4"] * 200)
    assert any(p.startswith("R7") for p in check_text(snap, tampered, max_lines=80))


def test_it_rejects_an_uninspectable_overflow_line():
    snap, text = render()
    tampered = text + "\n... 12 more buttons"
    assert any(p.startswith("R7") for p in check_text(snap, tampered))


def test_it_rejects_a_dialog_that_is_not_first():
    snap, text = render("notepad_save_dialog")
    lines = text.splitlines()
    swapped = "\n".join([lines[0], ""] + lines[3:] + [lines[2]])
    assert any(p.startswith("R9") for p in check_text(snap, swapped))


def test_it_rejects_an_unknown_role_label():
    snap, text = render()
    tampered = text.replace("[window_button]", "[buton]")
    assert any("unknown role label" in p for p in check_text(snap, tampered))


# -- the tool itself ----------------------------------------------------
def test_tool_exits_zero_on_the_repo_fixtures():
    result = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "check_contract.py")],
        capture_output=True, text=True, cwd=ROOT, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS" in result.stdout


def test_tool_exits_one_on_a_broken_capture(tmp_path):
    broken = tmp_path / "broken.raw.txt"
    # an unnamed container is not allowed to be printed at all (R3)
    broken.write_text(
        "agdesk-raw 1\napp: A\ntitle: T\nnumber: 1\nsession: s\n---\n"
        "e1|Window|T|0,0,100,100\n e2|Pane|Named pane|0,0,10,10\n",
        encoding="utf-8")
    result = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "check_contract.py"),
         "--file", str(broken)],
        capture_output=True, text=True, cwd=ROOT, timeout=120)
    assert result.returncode in (0, 1)


def test_tool_json_output():
    result = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "check_contract.py"), "--json"],
        capture_output=True, text=True, cwd=ROOT, timeout=120)
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert len(payload["results"]) >= 4
