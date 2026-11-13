"""Fixture backend: snapshots from files, actions simulated.

This is the backend the tests use and the one that works on a machine with no
desktop at all, so the whole CLI can be exercised in CI.  Actions are recorded
instead of performed: a ``click`` returns ``ok`` and the caller can still see
the click point, which is exactly what ``--dry-run`` needs.
"""

from __future__ import annotations

import copy
import os
from typing import Any, Optional

from .. import naming
from ..geometry import center
from ..model import Element, Snapshot
from ..reader import ReaderError, read_snapshot
from .base import ActionResult, Backend, BackendUnavailable, WindowInfo

__all__ = ["FixtureBackend", "DEFAULT_FIXTURE"]


def _here(*parts: str) -> str:
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, *parts)


DEFAULT_FIXTURE = _here("tests", "fixtures", "notepad.raw.txt")


class FixtureBackend(Backend):
    name = "fixture"
    interactive = False

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or naming.env(naming.ENV_FIXTURE, naming.LEGACY_ENV_FIXTURE)
        self._snapshot: Optional[Snapshot] = None
        #: every simulated action, so a test can assert what would have run
        self.performed: list[dict] = []

    def available(self) -> tuple[bool, str]:
        if not self.path:
            return False, (
                f"no fixture selected; set --fixture or {naming.ENV_FIXTURE}, "
                "or run on Windows/Linux with a desktop"
            )
        if not os.path.exists(self.path):
            return False, f"fixture not found: {self.path}"
        return True, ""

    def windows(self) -> list[WindowInfo]:
        self.require()
        snap = self._load()
        return [WindowInfo(1, snap.app or "fixture", snap.title, snap.window_rect(),
                           handle="fixture", pid=0, foreground=True)]

    def _load(self) -> Snapshot:
        if self._snapshot is None:
            try:
                self._snapshot = read_snapshot(self.path)
            except ReaderError as exc:
                raise BackendUnavailable(str(exc)) from None
        return self._snapshot

    def snapshot(self, window: Any = None, *, session: str = "local",
                 number: int = 1) -> Snapshot:
        self.require()
        if window and not self._matches(window):
            raise BackendUnavailable(
                f"no window matching '{window}' in this capture; it contains "
                f'"{self._load().title}"'
            )
        # a fresh copy each time, so one run cannot mutate the next one
        snap = copy.deepcopy(self._load())
        snap.session = session or snap.session
        snap.number = number
        snap.bounds_snapshot = number
        snap.backend = "fixture"
        return snap

    def _matches(self, window: Any) -> bool:
        snap = self._load()
        needle = str(window).strip().lower()
        if not needle:
            return True
        return (needle in (snap.title or "").lower()
                or needle in (snap.app or "").lower()
                or needle == "fixture")

    def perform(self, element: Element, action: str, snapshot: Snapshot) -> ActionResult:
        cx, cy = center(element.rect)
        self.performed.append({"action": action, "ref": element.ref,
                               "point": (cx, cy), "name": element.name,
                               "value": element.value})
        return ActionResult(
            True,
            action,
            element.ref,
            "fixture",
            f"simulated {action} of {element.ref} at {cx},{cy} (fixture backend)",
        )

    def click_point(self, x: int, y: int, *, button: str = "left",
                    double: bool = False) -> None:
        self.performed.append({"action": "click-at", "point": (x, y),
                               "button": button, "double": double})

    def type_text(self, element: Optional[Element], text: str) -> None:
        self.performed.append({"action": "type", "ref": getattr(element, "ref", ""),
                               "text": text})

    def send_keys(self, combo: str) -> None:
        self.performed.append({"action": "key", "keys": combo})

    def move_window(self, window, x: int, y: int) -> None:
        self.performed.append({"action": "move-window", "point": (x, y)})

    def resize_window(self, window, width: int, height: int) -> None:
        self.performed.append({"action": "resize-window", "size": (width, height)})
