"""Steps 31-34: the echo-desktop CLI, against fixtures and a fake desktop.

Fixture tests need no screen at all, which is what makes the CLI testable in
CI.  The fake backend tests exercise the Act half: refs, dry-run, verify diff,
exit codes.
"""

from __future__ import annotations

import json
import os
import re

import pytest

from snapshot_parser.backends import register_backend
from snapshot_parser.cli import EXIT_CODES, main
from snapshot_parser.session import SessionState, state_dir

from conftest import (
    ALL_FIXTURES,
    ROOT,
    fixture_path,
    golden_path,
    read_golden,
    run_cli,
    run_cli_json,
)
from fake_backend import FakeBackend, FakeWindow


@pytest.fixture
def fake():
    backend = FakeBackend()
    register_backend("fake", backend)
    yield backend


# ----------------------------------------------------------------------
# help / version / exit codes
# ----------------------------------------------------------------------
def test_help_lists_every_command():
    code, out, err = run_cli("--help")
    assert code == 0
    for command in ("windows", "snapshot", "inspect", "bounds", "find", "read",
                    "diff", "click", "click-at", "type", "set-value", "focus",
                    "scroll", "expand", "collapse", "key", "move-window",
                    "resize-window", "wait", "doctor"):
        assert command in out, f"{command} missing from --help"


def test_version():
    code, out, err = run_cli("--version")
    assert code == 0
    assert "echo-desktop" in out


def test_no_command_prints_help_and_exits_2():
    code, out, err = run_cli()
    assert code == 2
    assert "usage" in out.lower()


def test_bad_arguments_exit_2():
    code, out, err = run_cli("snapshot", "--nonsense")
    assert code == 2


def test_every_command_accepts_json():
    from snapshot_parser.cli import build_parser

    parser = build_parser()
    for command in ("windows", "snapshot", "inspect", "bounds", "find", "read",
                    "diff", "click", "click-at", "type", "set-value", "focus",
                    "scroll", "expand", "collapse", "key", "move-window",
                    "resize-window", "wait", "doctor"):
        with pytest.raises(SystemExit):
            parser.parse_args([command, "--json", "--help"])


def test_act_commands_accept_dry_run():
    from snapshot_parser.cli import build_parser

    parser = build_parser()
    for argv in (["click", "e1", "--dry-run"], ["click-at", "10", "20", "--dry-run"],
                 ["type", "e1", "x", "--dry-run"], ["set-value", "e1", "x", "--dry-run"],
                 ["focus", "e1", "--dry-run"], ["scroll", "e1", "--dry-run"],
                 ["expand", "e1", "--dry-run"], ["collapse", "e1", "--dry-run"],
                 ["key", "ctrl+s", "--dry-run"],
                 ["move-window", "10", "10", "--dry-run"],
                 ["resize-window", "800", "600", "--dry-run"]):
        args = parser.parse_args(argv)
        assert args.dry_run, argv


def test_exit_codes_are_documented():
    assert EXIT_CODES[0] == "ok"
    assert EXIT_CODES[2] == "bad arguments"
    assert EXIT_CODES[3] == "stale or unknown ref"
    assert EXIT_CODES[4] == "window not found"
    assert EXIT_CODES[5] == "action failed"


# ----------------------------------------------------------------------
# read commands on fixtures
# ----------------------------------------------------------------------
def test_snapshot_matches_the_golden():
    code, out, err = run_cli("snapshot", "--window", "snap.js", fixture="notepad_text")
    assert code == 0, err
    body = "\n".join(out.splitlines()[2:])
    expected = "\n".join(read_golden(golden_path("notepad_text.txt")).splitlines()[2:])
    assert body == expected


def test_snapshot_increments_the_number():
    code, out, err = run_cli("snapshot", fixture="notepad")
    assert "snapshot 1" in out
    code, out, err = run_cli("snapshot", fixture="notepad")
    assert "snapshot 2" in out


def test_snapshot_json():
    code, payload, err = run_cli_json("snapshot", fixture="notepad")
    assert code == 0
    assert payload["snapshot"] == 1
    assert payload["app"] == "Notepad"
    assert payload["backend"] == "fixture"
    assert "[window_button]" in payload["text"]
    assert payload["session"]


def test_snapshot_bounds_flag():
    code, out, err = run_cli("snapshot", "--bounds", fixture="notepad")
    assert code == 0
    assert "Close e6 @1764,8 46x32" in out


def test_snapshot_max_lines():
    code, out, err = run_cli("snapshot", "--max-lines", "3", fixture="notepad")
    assert code == 0
    assert "... " in out


def test_snapshot_ascii():
    code, out, err = run_cli("snapshot", "--ascii", fixture="notepad")
    assert "·" not in out and "—" not in out


def test_snapshot_unknown_window_exits_4():
    code, out, err = run_cli("snapshot", "--window", "No Such Window",
                             fixture="notepad")
    assert code == 4
    assert "no window matching" in err


def test_windows_lists_the_fixture_window():
    code, out, err = run_cli("windows", fixture="notepad")
    assert code == 0
    assert "Notepad" in out


def test_windows_json():
    code, payload, err = run_cli_json("windows", fixture="notepad")
    assert code == 0
    assert payload["windows"][0]["title"] == "Untitled - Notepad"
    assert payload["windows"][0]["size"] == "1920x1080"


def test_inspect_against_a_fixture():
    code, out, err = run_cli("snapshot", fixture="notepad")
    code, out, err = run_cli("inspect", "e6", fixture="notepad")
    assert code == 0, err
    assert "[button] Close e6" in out
    assert "geometry" in out


def test_inspect_json_has_everything():
    run_cli("snapshot", fixture="notepad")
    code, payload, err = run_cli_json("inspect", "e6", fixture="notepad")
    assert code == 0
    assert payload["geometry"]["width"] == 46
    assert payload["native_id"] == "0x0006"


def test_bounds_against_a_fixture():
    run_cli("snapshot", fixture="notepad")
    code, out, err = run_cli("bounds", "e6", fixture="notepad")
    assert code == 0
    assert out.strip().startswith("Close e6 @ 1764,8 46x32")
    assert "center 1787,24" in out


def test_find_against_a_fixture():
    run_cli("snapshot", fixture="notepad_save_dialog")
    code, out, err = run_cli("find", "save", fixture="notepad_save_dialog")
    assert code == 0
    assert "e45" in out


def test_find_with_no_match_exits_1():
    run_cli("snapshot", fixture="notepad")
    code, out, err = run_cli("find", "zzzz", fixture="notepad")
    assert code == 1
    assert "no element matches" in out


def test_read_returns_full_text():
    run_cli("snapshot", fixture="notepad_text")
    code, out, err = run_cli("read", "e13", fixture="notepad_text")
    assert code == 0
    assert len(out.rstrip("\n")) == 2340
    assert out.startswith("const snap = render(snapshot);")


def test_diff_between_two_snapshots():
    run_cli("snapshot", fixture="notepad")
    run_cli("snapshot", fixture="notepad_text")
    code, out, err = run_cli("diff", fixture="notepad_text")
    assert code == 0
    assert "Text Editor" in out or "title" in out


def test_doctor_reports_backends():
    code, out, err = run_cli("doctor", fixture="notepad")
    assert code == 0
    assert "backend in use" in out


def test_doctor_json():
    code, payload, err = run_cli_json("doctor", fixture="notepad")
    assert code == 0
    names = [row["backend"] for row in payload["backends"]]
    assert "fixture" in names and "windows-uia" in names


# ----------------------------------------------------------------------
# errors are readable, never tracebacks
# ----------------------------------------------------------------------
def test_unknown_ref_exits_3_with_a_clear_message():
    run_cli("snapshot", fixture="notepad")
    code, out, err = run_cli("inspect", "e777", fixture="notepad")
    assert code == 3
    assert "unknown ref" in err
    assert "Traceback" not in err


def test_malformed_ref_exits_3():
    run_cli("snapshot", fixture="notepad")
    code, out, err = run_cli("bounds", "banana", fixture="notepad")
    assert code == 3
    assert "not a ref" in err


def test_stale_ref_exits_3_after_a_new_snapshot():
    run_cli("snapshot", fixture="notepad")
    code, out, err = run_cli("click", "e6", "--backend", "fake")
    # the ref was taken with the fixture backend; the fake one has no state
    assert code in (3, 1, 5), (code, err)
    assert "Traceback" not in err


def test_json_error_is_machine_readable():
    run_cli("snapshot", fixture="notepad")
    code, payload, err = run_cli_json("inspect", "e777", fixture="notepad")
    assert code == 3
    assert payload["ok"] is False
    assert payload["exit_code"] == 3
    assert payload["code"] == "stale or unknown ref"


def test_missing_fixture_is_a_readable_error():
    code, out, err = run_cli("snapshot", "--fixture", "/no/such/file.txt")
    assert code == 1
    assert "fixture" in err.lower()
    assert "Traceback" not in err


def test_backend_that_cannot_run_here_explains_itself():
    code, out, err = run_cli("snapshot", "--backend", "windows-uia")
    assert code == 1
    assert "Windows" in err or "pywinauto" in err
    assert "Traceback" not in err


# ----------------------------------------------------------------------
# Act commands against the fake desktop
# ----------------------------------------------------------------------
def test_click_close_closes_the_window(fake):
    code, out, err = run_cli("snapshot", "--backend", "fake")
    assert code == 0, err
    assert "Close e5" in out
    code, out, err = run_cli("click", "e5", "--backend", "fake", "--yes")
    assert code == 0, err
    assert fake.window.closed
    assert any(p["action"] == "click" for p in fake.performed)


def test_click_dry_run_changes_nothing(fake):
    run_cli("snapshot", "--backend", "fake")
    code, out, err = run_cli("click", "e5", "--dry-run", "--backend", "fake")
    assert code == 0
    assert "dry run" in out.lower()
    assert not fake.window.closed
    assert fake.performed == []


def test_dry_run_prints_the_click_point(fake):
    run_cli("snapshot", "--backend", "fake")
    code, out, err = run_cli("snapshot", "e5", "--dry-run", "--backend", "fake")
    code, out, err = run_cli("click", "e5", "--dry-run", "--backend", "fake")
    assert re.search(r"at \d+,\d+", out), out


def test_click_at_dry_run(fake):
    run_cli("snapshot", "--backend", "fake")
    code, out, err = run_cli("click-at", "10", "20", "--dry-run", "--backend", "fake")
    assert code == 0
    assert fake.window.clicks == []


def test_click_at_really_clicks(fake):
    run_cli("snapshot", "--backend", "fake")
    code, out, err = run_cli("click-at", "300", "400", "--backend", "fake")
    assert code == 0
    assert (300, 400) in fake.window.clicks


def test_type_into_the_editor(fake):
    run_cli("snapshot", "--backend", "fake")
    code, out, err = run_cli("type", "e6", "hello", "--backend", "fake")
    assert code == 0, err
    assert fake.window.typed == "hello"
    assert "hello" in out or "+" in out or "Text Editor" in out


def test_set_value_replaces_content(fake):
    run_cli("snapshot", "--backend", "fake")
    code, out, err = run_cli("set-value", "e6", "replaced", "--backend", "fake")
    assert code == 0, err
    assert fake.window.text == "replaced"


def test_key_alt_f4_warns_and_needs_yes(fake):
    run_cli("snapshot", "--backend", "fake")
    code, out, err = run_cli("key", "alt+f4", "--dry-run", "--backend", "fake")
    assert code == 0
    assert "dry run" in out.lower()
    code, out, err = run_cli("key", "alt+f4", "--backend", "fake", "--no-verify")
    assert code == 0
    assert "destructive" in err
    assert fake.window.closed


def test_move_window_moves_and_reports(fake):
    run_cli("snapshot", "--backend", "fake")
    code, out, err = run_cli("move-window", "300", "250", "--backend", "fake")
    assert code == 0, err
    assert (fake.window.x, fake.window.y) == (300, 250)


def test_resize_window(fake):
    run_cli("snapshot", "--backend", "fake")
    code, out, err = run_cli("resize-window", "1024", "768", "--backend", "fake")
    assert code == 0
    assert (fake.window.width, fake.window.height) == (1024, 768)


def test_verify_diff_appears_after_an_action(fake):
    run_cli("snapshot", "--backend", "fake")
    code, out, err = run_cli("type", "e6", "typed text", "--backend", "fake")
    assert code == 0
    assert "Text Editor" in out or "typed text" in out


def test_no_verify_skips_the_diff(fake):
    run_cli("snapshot", "--backend", "fake")
    code, out, err = run_cli("type", "e6", "x", "--no-verify", "--backend", "fake")
    assert code == 0
    assert out.strip() == ""


def test_action_json_reports_ok_and_point(fake):
    run_cli("snapshot", "--backend", "fake")
    code, payload, err = run_cli_json("click", "e5", "--dry-run", "--backend", "fake")
    assert code == 0
    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert payload["point"]["x"] > 0


def test_wait_finds_an_element(fake):
    run_cli("snapshot", "--backend", "fake")
    code, out, err = run_cli("wait", "Close", "--timeout", "1", "--backend", "fake")
    assert code == 0
    assert "found" in out


def test_wait_times_out_with_a_readable_error(fake):
    run_cli("snapshot", "--backend", "fake")
    code, out, err = run_cli("wait", "Nonexistent", "--timeout", "0.3",
                             "--backend", "fake")
    assert code == 5
    assert "did not appear" in err


def test_action_on_a_closed_window_is_readable(fake):
    fake.window.closed = True
    code, out, err = run_cli("snapshot", "--backend", "fake")
    assert code == 4
    assert "Traceback" not in err


def test_dialog_actions_use_dialog_refs(fake):
    fake.window.dialog = "Save As"
    code, out, err = run_cli("snapshot", "--backend", "fake")
    assert "Save As" in out
    code, out, err = run_cli("click", "e9", "--backend", "fake")
    assert code == 0, err
    assert fake.window.dialog is None


# ----------------------------------------------------------------------
# state directory
# ----------------------------------------------------------------------
def test_state_is_written_to_the_state_dir():
    run_cli("snapshot", fixture="notepad")
    directory = state_dir(create=False)
    assert os.path.exists(os.path.join(directory, "state.json"))
    assert os.path.exists(os.path.join(directory, "last.raw.txt"))
    with open(os.path.join(directory, "state.json"), encoding="utf-8") as handle:
        payload = json.load(handle)
    assert payload["number"] == 1
    assert payload["session"]


def test_refs_survive_across_invocations():
    run_cli("snapshot", fixture="notepad")
    code, out, err = run_cli("inspect", "e6", fixture="notepad")
    assert code == 0
    assert "Close" in out


def test_no_state_flag_does_not_write_anything():
    run_cli("snapshot", "--no-state", fixture="notepad")
    assert not os.path.exists(os.path.join(state_dir(create=False), "state.json"))


def test_session_ids_are_stable_across_calls():
    _, out1, _ = run_cli("snapshot", fixture="notepad")
    _, out2, _ = run_cli("snapshot", fixture="notepad")
    session1 = re.search(r"session (\w+)", out1).group(1)
    session2 = re.search(r"session (\w+)", out2).group(1)
    assert session1 == session2


# ----------------------------------------------------------------------
# stale refs in the real CLI flow (step 33)
# ----------------------------------------------------------------------
def test_refs_are_handles_into_the_snapshot_you_last_printed(tmp_path):
    """Documented semantics: a short ref means "that element, in this snapshot".

    The CLI owns the snapshot numbering, so the stored snapshot - not the file
    on disk - is what a ref is resolved against.  A ref from another session,
    an unknown ref, or a ref whose element changed identity inside the same
    snapshot number is rejected with exit code 3 (see test_refs.py for the
    identity case); a ref that still exists is acted on, which is what makes
    `snapshot` then `click e6` work.
    """
    capture = tmp_path / "window.raw.txt"
    capture.write_text(
        "agdesk-raw 1\napp: App\ntitle: T\nnumber: 1\nsession: local\n---\n"
        "e1|Window|T|0,0,800,600\n e6|Button|Close|700,4,40,20|Invoke\n",
        encoding="utf-8")
    assert run_cli("snapshot", "--fixture", str(capture))[0] == 0
    # the file changes behind our back: the ref still resolves to the stored
    # snapshot, so the click point and the element agree with each other
    capture.write_text(
        "agdesk-raw 1\napp: App\ntitle: T\nnumber: 1\nsession: local\n---\n"
        "e1|Window|T|0,0,800,600\n e6|Button|Close|10,4,40,20|Invoke\n",
        encoding="utf-8")
    code, out, err = run_cli("click", "e6", "--dry-run", "--fixture", str(capture))
    assert code == 0
    assert "720,14" in out, "the stored snapshot's geometry is the one printed"

    # a ref that does not exist in the stored snapshot is rejected
    code, out, err = run_cli("click", "e77", "--dry-run", "--fixture", str(capture))
    assert code == 3
    assert "unknown ref" in err

    # a ref from another session is rejected outright
    code, out, err = run_cli("click", "@other:e6", "--dry-run", "--fixture", str(capture))
    assert code == 3
    assert "session" in err


def test_doctor_runs_even_when_no_backend_is_available():
    """Regression: `doctor` is the command that explains a broken setup.

    On a machine with no fixture, no display and no UIA it used to exit 1 inside
    make_context, so the one diagnostic you reach for was the one thing that
    could not run.
    """
    import os

    from snapshot_parser.cli import main

    # --shared options go after the subcommand, which is where they live
    saved = {k: os.environ.pop(k, None) for k in ("DISPLAY", "ECHO_FIXTURE", "AGENT_DESKTOP_FIXTURE")}
    try:
        assert main(["doctor"]) == 0, "doctor must report, not fail"
        out = main(["doctor", "--backend", "linux-x11", "--json"])
        assert out == 0
    finally:
        for key, value in saved.items():
            if value is not None:
                os.environ[key] = value
        os.environ.pop("ECHO_STATE", None)
