"""A deterministic in-process fake desktop for CLI and action tests.

It behaves like a real backend - it owns a mutable window tree, actions change
that tree, refs are renumbered on every snapshot - but it needs no screen, so
the whole Act half of the CLI can be tested anywhere, including CI on Linux.

This is the same contract the UIA and X11 backends implement; the live tests
in ``test_live_*`` exercise those, this file exercises everything above them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from snapshot_parser.backends.base import (
    ActionResult,
    Backend,
    BackendUnavailable,
    WindowInfo,
)
from snapshot_parser.model import Element, Rect, Snapshot


@dataclass
class FakeWindow:
    title: str = "Untitled - Notepad"
    app: str = "Notepad"
    x: int = 100
    y: int = 100
    width: int = 900
    height: int = 600
    text: str = ""
    closed: bool = False
    dialog: Optional[str] = None          # "Save As" when a modal dialog is up
    typed: str = ""
    keys: list[str] = field(default_factory=list)
    clicks: list[tuple[int, int]] = field(default_factory=list)


class FakeBackend(Backend):
    """A tiny mutable desktop with one Notepad window."""

    name = "fake"
    interactive = True

    def __init__(self, window: Optional[FakeWindow] = None) -> None:
        self.window = window or FakeWindow()
        self.snapshot_count = 0
        self.performed: list[dict[str, Any]] = []
        self._last: dict[str, tuple[str, str]] = {}

    # -- availability -------------------------------------------------
    def available(self) -> tuple[bool, str]:
        return True, ""

    def windows(self) -> list[WindowInfo]:
        if self.window.closed:
            return []
        w = self.window
        return [WindowInfo(1, w.app, w.title,
                           Rect(w.x, w.y, w.width, w.height),
                           handle="0x0001", pid=4242, foreground=True)]

    # -- snapshot -----------------------------------------------------
    def snapshot(self, window: Any = None, *, session: str = "local",
                 number: int = 1) -> Snapshot:
        if self.window.closed:
            raise BackendUnavailable("that window closed; run `agent-desktop windows`")
        self.snapshot_count += 1
        w = self.window
        snap = Snapshot(app=w.app, title=w.title, number=number, session=session,
                        backend=self.name)
        snap.bounds_snapshot = number
        root = Element(ref="e1", role="window", name=w.title,
                       rect=Rect(w.x, w.y, w.width, w.height),
                       state={"maximized": False}, native_id="0x0001")
        root.add(Element(ref="e2", role="pane", name=""))
        title_bar = root.children[0]
        for index, (label, offset) in enumerate(
            (("Minimize", 0), ("Maximize", 46), ("Close", 92)), start=3
        ):
            title_bar.add(Element(
                ref=f"e{index}", role="button", name=label, actions=["click"],
                rect=Rect(w.x + w.width - 140 + offset, w.y + 6, 42, 28),
                native_id=f"0x000{index}",
            ))
        editor = root.add(Element(
            ref="e6", role="edit", name="Text Editor", actions=["set value", "focus"],
            state={"focused": True}, value=w.text,
            rect=Rect(w.x, w.y + 40, w.width, w.height - 80), native_id="0x0006",
        ))
        editor.properties["chars"] = len(w.text)
        status = root.add(Element(
            ref="e7", role="text", name=f"{len(w.text)} chars",
            rect=Rect(w.x, w.y + w.height - 24, 200, 20),
        ))
        snap.root = root
        self._last = {"close": ("e5", "Close"), "editor": ("e6", "Text Editor")}
        if w.dialog:
            dialog = Element(ref="e8", role="dialog", name=w.dialog,
                             rect=Rect(w.x + 100, w.y + 100, 400, 300),
                             state={"modal": True})
            dialog.add(Element(ref="e9", role="button", name="Save",
                               actions=["click"], rect=Rect(w.x + 300, w.y + 350, 80, 30)))
            dialog.add(Element(ref="e10", role="button", name="Cancel",
                               actions=["click"], rect=Rect(w.x + 390, w.y + 350, 80, 30)))
            snap.dialog = dialog
        return snap

    # -- actions ------------------------------------------------------
    def perform(self, element: Element, action: str, snapshot: Snapshot) -> ActionResult:
        self.performed.append({"action": action, "ref": element.ref,
                               "name": element.name, "value": element.value})
        w = self.window
        name = (element.name or "").strip().lower()
        if action in ("click", "double click", "right click"):
            if name == "close":
                w.closed = True
            elif name == "minimize":
                pass
            elif name == "maximize":
                pass
            elif name == "save" and w.dialog:
                w.dialog = None
            elif name == "cancel":
                w.dialog = None
            w.clicks.append(element.rect.center)
            return ActionResult(True, action, element.ref, "fake-invoke")
        if action == "focus":
            return ActionResult(True, action, element.ref, "fake-focus")
        if action == "set value":
            w.text = element.value or ""
            w.title = "snap.js - Notepad" if w.text else "Untitled - Notepad"
            return ActionResult(True, action, element.ref, "fake-set-value")
        if action == "type":
            w.text += element.value or ""
            w.typed += element.value or ""
            return ActionResult(True, action, element.ref, "fake-type")
        if action in ("expand", "collapse"):
            if name == "file":
                return ActionResult(True, action, element.ref, "fake-expand")
        if action == "scroll":
            return ActionResult(True, action, element.ref, "fake-scroll")
        return ActionResult(False, action, element.ref, "",
                            f"the fake backend has no way to {action} {element.ref}")

    def click_point(self, x: int, y: int, *, button: str = "left",
                    double: bool = False) -> None:
        self.window.clicks.append((x, y))
        self.performed.append({"action": "click-at", "point": (x, y)})
        if self.window.dialog and Rect(self.window.x + 300, self.window.y + 350,
                                       80, 30).contains(x, y):
            self.window.dialog = None

    def type_text(self, element: Optional[Element], text: str) -> None:
        self.window.text += text
        self.window.typed += text
        if self.window.text:
            self.window.title = "snap.js - Notepad"
        self.performed.append({"action": "type", "text": text})

    def send_keys(self, combo: str) -> None:
        self.window.keys.append(combo)
        if combo.lower() in ("alt+f4",):
            self.window.closed = True
        self.performed.append({"action": "key", "keys": combo})

    def move_window(self, window: Any, x: int, y: int) -> None:
        self.window.x, self.window.y = x, y
        self.performed.append({"action": "move-window", "point": (x, y)})

    def resize_window(self, window: Any, width: int, height: int) -> None:
        self.window.width, self.window.height = width, height
        self.performed.append({"action": "resize-window", "size": (width, height)})
