"""Step 35: the rename to ``echo-desktop``, and the one-release ``agent-desktop-py`` alias.

The old name collided with an unrelated npm package that installs a binary called
``agent-desktop``; two package managers writing one bin path is a silent
last-writer-wins race, so the command was renamed.  These tests pin the parts
that have to keep working across the rename.
"""

from __future__ import annotations

import os
import re
import sys

import pytest

from snapshot_parser import naming, session


# ---------------------------------------------------------------------------
# names
# ---------------------------------------------------------------------------

def test_the_command_and_distribution_are_echo_desktop():
    assert naming.PROG == "echo-desktop"


def test_the_alias_is_not_the_contested_name():
    """The whole point: we never install a script called `agent-desktop`.

    npm's `agent-desktop` package owns that path.  Shipping our own would put us
    back in the overwrite race the rename exists to escape.
    """
    assert naming.ALIAS == "agent-desktop-py"
    assert naming.ALIAS != "agent-desktop"


def test_pyproject_installs_the_new_name_and_not_the_old_one():
    with open("pyproject.toml", encoding="utf-8") as handle:
        text = handle.read()
    assert 'name = "echo-desktop"' in text
    assert "echo-desktop = " in text
    assert "agent-desktop-py = " in text
    # a bare `agent-desktop = "..."` entry would re-create the collision
    assert "\nagent-desktop = " not in text


def test_hints_name_the_supported_command_not_the_alias():
    assert naming.command() == naming.PROG


# ---------------------------------------------------------------------------
# how we were invoked
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_executable(monkeypatch):
    yield
    naming.set_executable("")


@pytest.mark.parametrize("argv0", [
    "/usr/local/bin/agent-desktop-py",
    "agent-desktop-py",
    r"C:\Python311\Scripts\agent-desktop-py.exe",
])
def test_the_alias_is_detected_from_argv0(argv0):
    naming.set_executable(argv0)
    assert naming.invoked_as_alias() is True


@pytest.mark.parametrize("argv0", [
    "/usr/local/bin/echo-desktop",
    "echo-desktop",
    r"C:\Python311\Scripts\echo-desktop.exe",
])
def test_the_new_name_is_not_mistaken_for_the_alias(argv0):
    naming.set_executable(argv0)
    assert naming.invoked_as_alias() is False


def test_the_notice_names_both_commands_and_an_expiry(capsys):
    naming.set_executable("agent-desktop-py")
    naming.warn_alias()
    err = capsys.readouterr().err
    assert "echo-desktop" in err
    assert "agent-desktop-py" in err
    assert naming.ALIAS_REMOVED_IN in err


def test_the_notice_is_silent_on_the_new_name(capsys):
    naming.set_executable("echo-desktop")
    naming.warn_alias()
    assert capsys.readouterr().err == ""


def test_the_notice_goes_to_stderr_so_json_on_stdout_stays_parseable(capsys):
    naming.set_executable("agent-desktop-py")
    naming.warn_alias()
    captured = capsys.readouterr()
    assert captured.out == ""          # nothing pollutes stdout
    assert captured.err != ""


# ---------------------------------------------------------------------------
# environment variables: new name wins, old name still honoured
# ---------------------------------------------------------------------------

def test_new_env_var_is_used(monkeypatch):
    monkeypatch.setenv("ECHO_STATE", "/tmp/new")
    assert naming.env("ECHO_STATE", "AGENT_DESKTOP_STATE") == "/tmp/new"


def test_legacy_env_var_still_works(monkeypatch):
    """Someone's CI sets the old variable; the rename must not silently break it."""
    monkeypatch.delenv("ECHO_STATE", raising=False)
    monkeypatch.setenv("AGENT_DESKTOP_STATE", "/tmp/old")
    assert naming.env("ECHO_STATE", "AGENT_DESKTOP_STATE") == "/tmp/old"


def test_new_env_var_beats_the_legacy_one(monkeypatch):
    monkeypatch.setenv("ECHO_STATE", "/tmp/new")
    monkeypatch.setenv("AGENT_DESKTOP_STATE", "/tmp/old")
    assert naming.env("ECHO_STATE", "AGENT_DESKTOP_STATE") == "/tmp/new"


# ---------------------------------------------------------------------------
# state directory
# ---------------------------------------------------------------------------

def test_state_dir_is_dot_echo_desktop(tmp_path, monkeypatch):
    monkeypatch.delenv("ECHO_STATE", raising=False)
    monkeypatch.delenv("AGENT_DESKTOP_STATE", raising=False)
    monkeypatch.chdir(tmp_path)
    path = session.state_dir()
    assert path.endswith(".echo-desktop")
    assert os.path.isdir(path)


def test_an_existing_legacy_state_dir_is_migrated(tmp_path, monkeypatch):
    """Upgrading must not lose the user's ref history."""
    monkeypatch.delenv("ECHO_STATE", raising=False)
    monkeypatch.delenv("AGENT_DESKTOP_STATE", raising=False)
    monkeypatch.chdir(tmp_path)
    legacy = tmp_path / ".agent-desktop"
    legacy.mkdir()
    (legacy / "state.json").write_text('{"session": "abc"}', encoding="utf-8")

    path = session.state_dir()

    assert path.endswith(".echo-desktop")
    assert (tmp_path / ".echo-desktop" / "state.json").read_text(encoding="utf-8") == '{"session": "abc"}'
    assert not legacy.exists(), "the old directory should have been moved, not copied"


def test_migration_does_not_clobber_an_existing_new_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("ECHO_STATE", raising=False)
    monkeypatch.delenv("AGENT_DESKTOP_STATE", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agent-desktop").mkdir()
    (tmp_path / ".agent-desktop" / "state.json").write_text("old", encoding="utf-8")
    (tmp_path / ".echo-desktop").mkdir()
    (tmp_path / ".echo-desktop" / "state.json").write_text("new", encoding="utf-8")

    session.state_dir()

    assert (tmp_path / ".echo-desktop" / "state.json").read_text(encoding="utf-8") == "new"
    assert (tmp_path / ".agent-desktop" / "state.json").read_text(encoding="utf-8") == "old"


def test_an_explicit_state_override_is_left_alone(tmp_path, monkeypatch):
    """ECHO_STATE means 'put it here' - never migrate or create a dot-dir."""
    monkeypatch.setenv("ECHO_STATE", str(tmp_path / "custom"))
    monkeypatch.delenv("AGENT_DESKTOP_STATE", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agent-desktop").mkdir()

    path = session.state_dir()

    assert path == str(tmp_path / "custom")
    assert (tmp_path / ".agent-desktop").exists(), "untouched when an override is set"


# ---------------------------------------------------------------------------
# no stale names anywhere in the shipped code
# ---------------------------------------------------------------------------

#: A hint the user is meant to copy, e.g. "run `agent-desktop snapshot`". Matches
#: the old command *used as a command*, which is what must not come back.
_STALE_HINT = re.compile(r"(?<![\w/.`-])agent-desktop\s+(?!{})[a-z]".format("py"))


def test_no_python_module_tells_the_user_to_run_the_old_command():
    """Guards the next rename: hints must name the supported command.

    Prose that *explains* the migration (``naming.py``) and the legacy
    ``.agent-desktop/`` path are legitimate; a hint telling someone to run
    ``agent-desktop`` is not.
    """
    offenders = []
    for path in _python_files():
        if path.name == "test_rename.py":
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _STALE_HINT.search(line):
                offenders.append(f"{path}:{number}: {line.strip()}")
    assert not offenders, "hints still point at the old command:\n" + "\n".join(offenders)


def _python_files():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for path in sorted(root.rglob("*.py")):
        if any(part in {".venv", "build", "__pycache__", ".git"} for part in path.parts):
            continue
        yield path
