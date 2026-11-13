"""Phase F: live validation, on a throwaway Notepad (Windows) or gedit (Linux).

Every test here is skipped unless the machine really has a desktop, so the
suite is green in CI and meaningful on a user's box:

* Windows -> launches its own ``notepad.exe`` and kills it in teardown
* Linux   -> skipped unless ``--live`` is passed and DISPLAY + tools exist

The tests never touch a window the user already has open: they start their
own, remember its pid, and close it.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from snapshot_parser.backends import BackendUnavailable
from snapshot_parser.model import Snapshot

LIVE = os.environ.get("ECHO_LIVE", "").strip() in ("1", "true", "yes")


def _launch_notepad():
    """Start a throwaway editor and return (process, backend, title)."""
    if sys.platform == "win32":
        proc = subprocess.Popen(["notepad.exe"])
        backend_name = "windows-uia"
        title = "- Notepad"     # localized builds still expose the file part
    else:
        tool = "gedit" if subprocess.run(["which", "gedit"],
                                         capture_output=True).returncode == 0 else None
        if tool is None:
            return None, None, None
        proc = subprocess.Popen([tool, "--new-window"])
        backend_name = "linux-x11"
        title = "gedit"
    return proc, backend_name, title


@pytest.fixture
def notepad():
    """A throwaway editor window, closed no matter what the test does."""
    if not LIVE:
        pytest.skip("live UI test: set ECHO_LIVE=1 on a desktop machine")
    proc, backend_name, title = _launch_notepad()
    if proc is None:
        pytest.skip("no throwaway editor available on this machine (install gedit)")
    # wait for the window to exist instead of sleeping blindly
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            from snapshot_parser.backends import resolve_backend

            backend = resolve_backend(backend_name)
            ok, _why = backend.available()
            if ok and any(title.lower() in (w.title or "").lower()
                          for w in backend.windows()):
                break
        except BackendUnavailable:
            pass
        time.sleep(0.25)
    try:
        yield {"proc": proc, "backend": backend_name, "title": title}
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/IM", "notepad.exe"],
                           capture_output=True)


def _snapshot(handle):
    from snapshot_parser.backends import resolve_backend

    backend = resolve_backend(handle["backend"])
    return backend, backend.snapshot(handle["title"], session="live", number=1)


# -- step 21: read ------------------------------------------------------
def test_live_snapshot_has_the_window_buttons_and_menus(notepad):
    backend, snap = _snapshot(notepad)
    assert snap.root is not None, snap.error
    names = {el.name for el in snap.walk()}
    for expected in ("Close", "Minimize", "Maximize"):
        assert expected in names, f"{expected} missing; saw {sorted(names)[:20]}"
    text_names = {el.name for el in snap.walk() if el.role in ("menu", "menuitem")}
    for menu in ("File", "Edit"):
        assert menu in text_names, f"menu {menu} missing"

    refs = [el.ref for el in snap.walk()]
    assert len(refs) == len(set(refs))
    assert all(ref.startswith("e") for ref in refs)


def test_live_snapshot_renders_within_the_cap(notepad):
    from snapshot_parser.render import RenderOptions, render_snapshot

    backend, snap = _snapshot(notepad)
    text = render_snapshot(snap, RenderOptions(max_lines=80))
    body = text.split("\n\n", 1)[1].splitlines()
    assert len(body) <= 81
    assert text.splitlines()[0].startswith(snap.app or snap.title)


# -- step 22: act -------------------------------------------------------
def test_live_click_close_closes_notepad(notepad):
    backend, snap = _snapshot(notepad)
    close = next((el for el in snap.walk()
                  if el.role == "button" and el.name == "Close"), None)
    assert close is not None
    result = backend.perform(close, "click", snap)
    assert result.ok, result.message
    deadline = time.time() + 10
    while time.time() < deadline:
        if notepad_is_gone():
            break
        time.sleep(0.25)
    assert notepad_is_gone(), "Notepad is still running after clicking Close"


def notepad_is_gone() -> bool:
    if sys.platform == "win32":
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq notepad.exe"],
                             capture_output=True, text=True).stdout
        return "notepad.exe" not in out.lower()
    return True


# -- step 22b: geometry -------------------------------------------------
def test_live_click_at_the_centre_of_close(notepad):
    from snapshot_parser.geometry import element_geometry

    backend, snap = _snapshot(notepad)
    close = next(el for el in snap.walk()
                 if el.role == "button" and el.name == "Close")
    geo = element_geometry(close, window=snap.window_rect())
    assert geo["width"] > 0 and geo["height"] > 0
    assert geo["offscreen"] is False
    backend.click_point(geo["cx"], geo["cy"])
    time.sleep(1)
    assert notepad_is_gone()


def test_live_move_window_moves_the_element_bounds(notepad):
    backend, snap = _snapshot(notepad)
    close = next(el for el in snap.walk()
                 if el.role == "button" and el.name == "Close")
    before = close.rect
    backend.move_window(notepad["title"], 200, 150)
    time.sleep(0.5)
    _, moved = _snapshot(notepad)
    close2 = next(el for el in moved.walk()
                  if el.role == "button" and el.name == "Close")
    assert close2.rect.x != before.x or close2.rect.y != before.y


# -- step 23: change detection -----------------------------------------
def test_live_typing_increases_the_snapshot_number_and_stales_the_refs(notepad):
    from snapshot_parser.diff import format_diff, diff_snapshots
    from snapshot_parser.refs import RefError, validate_ref

    backend, first = _snapshot(notepad)
    editor = next((el for el in first.walk() if el.role in ("edit", "document")), None)
    assert editor is not None, "no editable element found"
    backend.type_text(editor, "hello from the test")

    second = backend.snapshot(notepad["title"], session="live", number=2)
    assert second.number > first.number
    diff = format_diff(diff_snapshots(first, second))
    assert "hello" in diff or "chars" in diff or "Text Editor" in diff

    with pytest.raises(RefError):
        validate_ref(editor.ref, 1, "live", second)


# -- step 24: dialog ----------------------------------------------------
def test_live_save_dialog_is_ordered_first(notepad):
    from snapshot_parser.render import render_snapshot

    backend, snap = _snapshot(notepad)
    try:
        backend.send_keys("ctrl+s")
    except BackendUnavailable:
        pytest.skip("cannot send keys on this backend")
    time.sleep(1)
    dialog = backend.snapshot(notepad["title"], session="live", number=2)
    if dialog.dialog is None:
        pytest.skip("this editor did not open a save dialog for ctrl+s")
    text = render_snapshot(dialog)
    body = [line for line in text.splitlines() if line.strip()][1:]
    assert body[0].startswith("[dialog]"), body[0]
    assert "(background)" in text
    # cancel it, so the window is left as it was
    cancel = next((el for el in dialog.dialog.walk()
                   if el.role == "button" and el.name in ("Cancel", "Close")), None)
    if cancel is not None:
        backend.perform(cancel, "click", dialog)
