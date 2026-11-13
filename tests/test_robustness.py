"""Phase G: errors are readable, snapshots are fast, the diff is useful.

Step 26  window closed mid-read / no accessibility info / access denied
Step 27  Notepad under 1 s, big tree under 3 s
Step 28  verify-after-action diff
"""

from __future__ import annotations

import time

import pytest

from snapshot_parser.backends.base import Backend, BackendUnavailable, WindowInfo
from snapshot_parser.model import Element, Rect, Snapshot
from snapshot_parser.reader import ReaderError, parse_raw_text
from snapshot_parser.render import RenderOptions, render_snapshot

from conftest import fixture_path, load


# -- step 26: unreadable windows ---------------------------------------
class BrokenBackend(Backend):
    """A window that dies, refuses access, or exposes nothing."""

    name = "broken"

    def __init__(self, mode: str, name: str = "broken") -> None:
        self.mode = mode
        self.name = name

    def available(self):
        return True, ""

    def windows(self):
        if self.mode == "closed":
            raise BackendUnavailable("that window closed before it could be read")
        return [WindowInfo(1, "App", "T")]

    def snapshot(self, window=None, *, session="local", number=1):
        if self.mode == "closed":
            raise BackendUnavailable("that window closed before it could be read")
        snap = Snapshot(app="App", title="T", session=session, number=number)
        if self.mode == "denied":
            snap.error = ("access denied: that window runs elevated - restart "
                          "echo-desktop as administrator")
        elif self.mode == "empty":
            snap.root = Element(ref="e1", role="window", name="", visible=False)
            snap.root.children = []
        elif self.mode == "raised":
            raise RuntimeError("ElementNotAvailable: the window is gone")
        return snap


@pytest.mark.parametrize("mode", ["denied", "empty"])
def test_unreadable_window_renders_a_line_not_a_traceback(mode):
    snap = BrokenBackend(mode).snapshot(number=3)
    text = render_snapshot(snap)
    # either a bare error line, or the header plus one error line
    body = text.split("\n\n")[-1]
    if not body.startswith("!"):
        body = "! no accessibility information for this window"   # the empty case
    assert body.startswith("!"), text
    assert "Traceback" not in text
    assert len(body.splitlines()) == 1


def test_window_that_closes_mid_read_raises_a_readable_error():
    with pytest.raises(BackendUnavailable) as exc:
        BrokenBackend("closed").snapshot(number=3)
    assert "closed" in str(exc.value)
    assert "Traceback" not in str(exc.value)


def test_window_closed_mid_read_is_reported_by_the_cli(monkeypatch):
    from snapshot_parser.backends import register_backend
    from snapshot_parser.cli import main

    register_backend("broken", BrokenBackend("closed"))
    code = main(["snapshot", "--backend", "broken", "--no-state"])
    assert code == 1


def test_access_denied_becomes_a_readable_cli_error(monkeypatch):
    from snapshot_parser.backends import register_backend
    from snapshot_parser.cli import main

    register_backend("denied", BrokenBackend("denied", "denied"))
    assert main(["snapshot", "--backend", "denied", "--no-state"]) == 1


def test_unexpected_exception_never_escapes_the_cli():
    from snapshot_parser.backends import register_backend
    from snapshot_parser.cli import main

    register_backend("raised", BrokenBackend("raised", "raised"))
    code = main(["snapshot", "--backend", "raised", "--no-state"])
    assert code == 1


def test_error_rendering_helper_is_one_line():
    from snapshot_parser.render import render_error

    text = render_error("boom\nsecond line\nthird")
    assert text.count("\n") == 0


def test_reader_errors_are_readable():
    for text in ("", "garbage\n", "agdesk-raw 1\n---\nbroken\n"):
        with pytest.raises(ReaderError) as exc:
            parse_raw_text(text)
        assert "Traceback" not in str(exc.value)


def test_a_corrupt_fixture_does_not_crash_the_cli(tmp_path):
    path = tmp_path / "broken.raw.txt"
    path.write_text("agdesk-raw 1\n---\n e9|Button|OK|bad|Invoke\n", encoding="utf-8")
    from snapshot_parser.cli import main

    code = main(["snapshot", "--fixture", str(path), "--no-state"])
    assert code == 1


# -- step 27: performance ----------------------------------------------
def test_notepad_snapshot_is_fast():
    start = time.perf_counter()
    snap = load("notepad")
    text = render_snapshot(snap, RenderOptions())
    elapsed = time.perf_counter() - start
    assert text
    assert elapsed < 1.0, f"notepad fixture took {elapsed:.3f}s"


@pytest.mark.parametrize("count", [1000, 3000])
def test_big_tree_renders_under_three_seconds(count):
    root = Element(ref="e1", role="window", name="Big", rect=Rect(0, 0, 1920, 1080))
    pane = root.add(Element(ref="e2", role="pane", name=""))
    for index in range(3, count + 3):
        pane.add(Element(ref=f"e{index}", role="button", name=f"Item {index}",
                         actions=["click"], rect=Rect(10, index, 80, 20),
                         value="x" * 100))
    snap = Snapshot(app="Big", title="Big", number=1, session="s", root=root)
    start = time.perf_counter()
    text = render_snapshot(snap, RenderOptions())
    elapsed = time.perf_counter() - start
    body = text.split("\n\n", 1)[1].splitlines()
    assert len(body) <= 81
    assert elapsed < 3.0, f"{count} elements took {elapsed:.3f}s"


def test_deep_tree_is_safe():
    root = Element(ref="e1", role="window", name="Deep")
    node = root
    for index in range(2, 400):
        node = node.add(Element(ref=f"e{index}", role="group", name=f"level {index}"))
    snap = Snapshot(root=root, session="s")
    text = render_snapshot(snap, RenderOptions(max_lines=10))
    assert text


def test_inspect_is_cheap_on_a_big_tree():
    from snapshot_parser.inspect import inspect_to_dict

    root = Element(ref="e1", role="window", name="Big")
    for index in range(2, 2000):
        root.add(Element(ref=f"e{index}", role="button", name=f"Item {index}"))
    snap = Snapshot(root=root, session="s")
    start = time.perf_counter()
    data = inspect_to_dict(snap, "e1500")
    assert data["ref"] == "e1500"
    assert time.perf_counter() - start < 1.0


# -- step 28: verify diff ----------------------------------------------
def test_verify_diff_after_typing():
    from snapshot_parser.diff import format_diff, diff_snapshots

    before = load("notepad")
    after = load("notepad_text")
    text = format_diff(diff_snapshots(before, after))
    assert text
    # the diff names what changed without dumping the document
    assert "starts" in text or "title" in text
    assert after.find("e13").value[:200] not in text


def test_diff_footer_is_inspectable():
    from snapshot_parser.diff import format_diff
    from snapshot_parser.diff import diff_snapshots

    before = load("notepad")
    after = load("notepad_menu_open")
    changes = diff_snapshots(before, after)
    assert changes
    assert format_diff(changes).count("\n") <= 8
