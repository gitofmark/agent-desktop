"""Acting on a ref in a *later* command, against a backend that needs live controls.

This is the shape a real Windows session has, and the one the fake-backend tests
missed: `snapshot` runs in one process and writes `.echo-desktop/`, then `click`
runs in another. Refs survive because they are reloaded from disk, but a live
backend cannot act on a reloaded snapshot - there are no controls behind it.
"""

from __future__ import annotations

import itertools

import pytest

from snapshot_parser.backends.base import ActionResult, Backend, WindowInfo
from snapshot_parser.backends.windows_uia import looks_like_browser, browser_hint
from snapshot_parser.cli import main
from snapshot_parser.model import Element, Rect, Snapshot


class LiveOnlyBackend(Backend):
    """Refuses to act unless `snapshot()` ran in this process - like UIA/X11."""

    name = "live-only"
    interactive = True

    def __init__(self) -> None:
        self.acted: list[str] = []
        self.live = False        # set only by snapshot()
        self.snapshot_calls = 0

    def available(self):
        return True, ""

    def windows(self):
        # one window, so `win`-based commands do not report "no windows are open"
        return [WindowInfo(number=1, app="Notepad", title="Untitled - Notepad",
                           rect=Rect(0, 0, 800, 600), handle="0x1", pid=1,
                           foreground=True)]

    def snapshot(self, window=None, *, session="local", number=1):
        self.snapshot_calls += 1
        self.live = True
        root = Element(ref="e1", role="window", name="Untitled - Notepad",
                       rect=Rect(0, 0, 800, 600),
                       children=[
                           Element(ref="e2", role="button", name="Close",
                                   rect=Rect(760, 8, 32, 24), actions=["click"]),
                       ])
        return Snapshot(app="Notepad", title="Untitled - Notepad", number=number,
                        session=session, backend=self.name, root=root)

    def perform(self, element, action, snapshot):
        if not self.live:
            return ActionResult(False, action, element.ref, "",
                                "no live tree; snapshot first")
        self.acted.append(element.ref)
        return ActionResult(True, action, element.ref, "invoke")

    def close(self):
        pass


@pytest.fixture
def live_backend(monkeypatch, tmp_path):
    """A backend registered under its own name, with a persistent state dir."""
    from snapshot_parser.backends import base

    backend = LiveOnlyBackend()
    # the registry stores an *instance*, not a factory (see resolve_backend)
    monkeypatch.setitem(base.CUSTOM_BACKENDS, "live-only", backend)
    monkeypatch.setenv("ECHO_STATE", str(tmp_path / "state"))
    return backend


def run(*argv):
    return main(list(argv))


def test_click_works_in_a_second_command(live_backend):
    """The bug: `snapshot` then `click` in a new process failed with 'no live tree'."""
    assert run("snapshot", "--backend", "live-only") == 0
    live_backend.live = False          # a new process: no controls yet
    assert run("click", "e2", "--backend", "live-only", "--no-verify") == 0
    assert live_backend.acted == ["e2"]


def test_the_ref_the_user_saw_is_the_one_acted_on(live_backend):
    """Re-reading must reuse the same element and not renumber the refs."""
    run("snapshot", "--backend", "live-only")
    live_backend.live = False
    live_backend.acted.clear()
    run("click", "e2", "--backend", "live-only", "--no-verify")
    assert live_backend.acted == ["e2"]


def test_a_live_backend_is_read_once_per_action(live_backend):
    """One extra read to act, plus one to verify - not a re-read per command."""
    run("snapshot", "--backend", "live-only")
    live_backend.live = False
    before = live_backend.snapshot_calls
    run("click", "e2", "--backend", "live-only", "--no-verify")
    assert live_backend.snapshot_calls - before == 1


def test_a_stale_ref_is_still_rejected_without_acting(live_backend):
    run("snapshot", "--backend", "live-only")
    live_backend.live = False
    live_backend.acted.clear()
    assert run("click", "e999", "--backend", "live-only", "--no-verify") == 3
    assert live_backend.acted == []


@pytest.mark.parametrize("title,exe", [
    ("README.md - Visual Studio Code", "Code.exe"),
    ("New tab - Personal - Microsoft\u200b Edge", "msedge.exe"),
    ("WhatsApp Beta", "WhatsApp.exe"),
    ("Arena | Benchmark", "msedge.exe"),
])
def test_chromium_windows_are_recognised(title, exe):
    assert looks_like_browser(title, exe)


@pytest.mark.parametrize("title,exe", [
    ("Untitled - Notepad", "notepad.exe"),
    ("C:\\Windows\\system32\\cmd.exe - echo-desktop  windows", "cmd.exe"),
    ("Calculator", "CalculatorApp.exe"),
])
def test_native_windows_are_not_mistaken_for_browsers(title, exe):
    assert not looks_like_browser(title, exe)


def test_the_hint_names_the_flag_and_the_executable():
    hint = browser_hint("README.md - Visual Studio Code", "Code.exe")
    assert "--force-renderer-accessibility" in hint
    assert "code --force-renderer-accessibility" in hint
    assert "existing windows never enable it later" in hint


def test_the_hint_is_specific_per_browser():
    assert "msedge" in browser_hint("New tab - Microsoft\u200b Edge", "msedge.exe")
    assert "chrome" in browser_hint("Something - Google Chrome", "chrome.exe")


def test_an_empty_browser_tree_renders_the_hint_not_a_blank_header():
    """A VS Code/Edge window with renderer accessibility off reads as one Pane."""
    from snapshot_parser.render import render_snapshot

    root = Element(ref="e1", role="window", name="", rect=Rect(0, 0, 800, 600))
    snap = Snapshot(app="Code", title="README.md - Visual Studio Code", number=1,
                    session="s", backend="windows-uia", root=root, hint=browser_hint(
                        "README.md - Visual Studio Code", "Code.exe"))
    text = render_snapshot(snap)
    assert "--force-renderer-accessibility" in text
    assert "no accessibility tree" in text
    # header, a blank line, then one readable line - never a traceback
    lines = text.splitlines()
    assert len(lines) == 3, text
    assert lines[0].startswith("Code — ")
    assert lines[2].startswith("! ")


def test_a_healthy_tree_does_not_render_a_hint():
    from snapshot_parser.render import render_snapshot

    root = Element(ref="e1", role="window", name="Untitled - Notepad",
                   rect=Rect(0, 0, 800, 600),
                   children=[Element(ref="e2", role="button", name="Close",
                                     rect=Rect(760, 8, 32, 24))])
    snap = Snapshot(app="Notepad", title="Untitled - Notepad", number=1,
                    session="s", backend="windows-uia", root=root)
    assert "force-renderer" not in render_snapshot(snap)
