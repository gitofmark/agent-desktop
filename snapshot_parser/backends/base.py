"""Backend interface: where the tree and the actions actually come from.

A backend has one job - produce a :class:`Snapshot` - and a second, optional
one: perform an action.  Fixtures can only do the first, which is what makes
the whole parser testable without a desktop.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Any, Optional

from .. import naming
from ..model import Element, Rect, Snapshot

__all__ = [
    "BackendUnavailable",
    "ActionResult",
    "WindowInfo",
    "Backend",
    "resolve_backend",
    "list_backends",
    "register_backend",
    "CUSTOM_BACKENDS",
]


class BackendUnavailable(RuntimeError):
    """The backend cannot run here (no display, no package, wrong OS)."""


@dataclass
class WindowInfo:
    number: int
    app: str
    title: str
    rect: Rect = field(default_factory=Rect)
    handle: str = ""
    pid: int = 0
    foreground: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "app": self.app,
            "title": self.title,
            "position": f"{self.rect.x},{self.rect.y}",
            "size": f"{self.rect.width}x{self.rect.height}",
            "rect": self.rect.as_dict(),
            "handle": self.handle,
            "pid": self.pid,
            "foreground": self.foreground,
        }

    def line(self) -> str:
        marker = "*" if self.foreground else " "
        title = self.title or "(untitled)"
        if len(title) > 60:
            title = title[:57] + "..."
        return (f"{marker} {self.number:>3}  {self.app:<14.14} {title:<60.60} "
                f"{self.rect.x},{self.rect.y} {self.rect.width}x{self.rect.height}")


@dataclass
class ActionResult:
    ok: bool
    action: str
    ref: str = ""
    method: str = ""
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "action": self.action,
            "ref": self.ref,
            "method": self.method,
            "message": self.message,
        }


class Backend:
    """Base class; every method raises ``BackendUnavailable`` by default."""

    name = "none"
    interactive = False

    def available(self) -> tuple[bool, str]:
        return False, f"the {self.name} backend does not work on this machine"

    def require(self) -> None:
        ok, why = self.available()
        if not ok:
            raise BackendUnavailable(why)

    # -- reading ------------------------------------------------------
    def windows(self) -> list[WindowInfo]:
        self.require()
        return []

    def snapshot(self, window: Any = None, *, session: str = "local",
                 number: int = 1) -> Snapshot:
        self.require()
        raise BackendUnavailable("this backend cannot take snapshots")

    # -- acting -------------------------------------------------------
    def perform(self, element: Element, action: str, snapshot: Snapshot) -> ActionResult:
        return ActionResult(False, action, element.ref, "",
                            f"the {self.name} backend cannot perform actions")

    def click_point(self, x: int, y: int, *, button: str = "left",
                    double: bool = False) -> None:
        raise BackendUnavailable(f"the {self.name} backend cannot click")

    def type_text(self, element: Optional[Element], text: str) -> None:
        raise BackendUnavailable(f"the {self.name} backend cannot type")

    def send_keys(self, combo: str) -> None:
        raise BackendUnavailable(f"the {self.name} backend cannot send keys")

    def move_window(self, window: Any, x: int, y: int) -> None:
        raise BackendUnavailable(f"the {self.name} backend cannot move windows")

    def resize_window(self, window: Any, width: int, height: int) -> None:
        raise BackendUnavailable(f"the {self.name} backend cannot resize windows")

    def launch(self, command: str) -> int:
        """Start a program and return its pid. Backends without live windows refuse."""
        raise BackendUnavailable(f"the {self.name} backend cannot launch programs")

    def focus_window(self, window: Any) -> None:
        """Bring a window to the foreground so typed keys land in it."""
        raise BackendUnavailable(f"the {self.name} backend cannot focus windows")

    def close(self) -> None:
        pass


def list_backends() -> list[str]:
    return ["fixture", "windows-uia", "linux-x11"]


#: Extra backends registered at runtime (tests, and any host app embedding
#: the parser, e.g. ECHO itself with its own native bridge).
CUSTOM_BACKENDS: dict[str, Any] = {}


def register_backend(name: str, backend: Any) -> None:
    CUSTOM_BACKENDS[name.lower()] = backend


def resolve_backend(name: str = "auto", *, snapshot=None) -> Backend:
    """Pick a backend; ``auto`` prefers a live desktop, then falls back."""
    from .fixture import FixtureBackend
    from .linux_x11 import LinuxX11Backend
    from .windows_uia import WindowsUiaBackend

    name = (name or "auto").lower()
    if name in CUSTOM_BACKENDS:
        return CUSTOM_BACKENDS[name]
    if name in ("fixture", "raw", "file"):
        return FixtureBackend()
    if name in ("windows", "uia", "windows-uia"):
        return WindowsUiaBackend()
    if name in ("linux", "x11", "linux-x11"):
        return LinuxX11Backend()
    if name != "auto":
        raise BackendUnavailable(
            f"unknown backend '{name}' (choose from auto, "
            f"{', '.join(list_backends())}, "
            f"{', '.join(CUSTOM_BACKENDS) or 'no custom backends'})"
        )
    candidates = [FixtureBackend(), WindowsUiaBackend(), LinuxX11Backend()]
    for backend in candidates:
        ok, _why = backend.available()
        if ok:
            return backend
    return FixtureBackend()


def env_state_dir() -> str:
    return naming.env(naming.ENV_STATE, naming.LEGACY_ENV_STATE)


def platform_name() -> str:
    return sys.platform
