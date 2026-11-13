"""Shared test helpers: fixture paths, snapshot loading, CLI running."""

from __future__ import annotations

import io
import json
import os
import sys
from contextlib import redirect_stderr, redirect_stdout

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "tests", "fixtures")
GOLDEN = os.path.join(ROOT, "tests", "golden")

ALL_FIXTURES = [
    "notepad.raw.txt",
    "notepad_text.raw.txt",
    "notepad_menu_open.raw.txt",
    "notepad_save_dialog.raw.txt",
]


@pytest.fixture(autouse=True)
def _session_state_in_tmp(tmp_path, monkeypatch):
    """Never let a test touch the developer's real .echo-desktop/ state."""
    monkeypatch.setenv("ECHO_STATE", str(tmp_path / "state"))
    monkeypatch.delenv("ECHO_FIXTURE", raising=False)
    yield


@pytest.fixture
def fixtures():
    return FIXTURES


@pytest.fixture
def golden():
    return GOLDEN


def fixture_path(name: str) -> str:
    if not name.endswith(".txt"):
        name += ".raw.txt"
    return os.path.join(FIXTURES, name)


def golden_path(name: str) -> str:
    if not name.endswith(".txt"):
        name += ".txt"
    return os.path.join(GOLDEN, name)


def load(name: str):
    from snapshot_parser import read_snapshot

    return read_snapshot(fixture_path(name))


def sha(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def run_cli(*argv: str, fixture: str | None = None):
    """Run the CLI in-process; returns (exit_code, stdout, stderr)."""
    from snapshot_parser.cli import main

    args = list(argv)
    if fixture and "--fixture" not in args:
        args += ["--fixture", fixture_path(fixture)]
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = main(args)
        except SystemExit as exc:  # argparse errors
            code = int(exc.code or 0)
    return code, out.getvalue(), err.getvalue()


def run_cli_json(*argv: str, fixture: str | None = None):
    code, out, err = run_cli(*argv, "--json", fixture=fixture)
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        payload = {"_raw": out, "_stderr": err}
    return code, payload, err


def read_golden(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def write_golden(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def tree_text(snapshot) -> str:
    """A compact debug dump of an Element tree, handy in assertion messages."""
    lines = []

    def visit(el, depth):
        lines.append("  " * depth + f"{el.ref} {el.role} {el.name!r} "
                     f"{el.actions} {el.state} {el.rect}")
        for child in el.children:
            visit(child, depth + 1)

    visit(snapshot.root, 0)
    return "\n".join(lines)
