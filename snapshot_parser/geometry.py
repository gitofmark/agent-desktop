"""Geometry helpers: center points, window-relative boxes, offscreen tests.

Everything here works in **screen pixels, top-left origin** - the same space
the executor clicks in (R10).  DPI handling lives in
:func:`normalize_dpi` / :class:`DpiInfo`: on Windows the UIA tree can report
physical pixels while the click API wants logical ones (or the reverse), and
on Linux only the X11 root coordinates are trusted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from .model import Element, Rect, Snapshot

__all__ = [
    "DpiInfo",
    "normalize_dpi",
    "center",
    "relative",
    "is_offscreen",
    "element_geometry",
    "format_bounds",
    "parse_rect",
    "union",
]


@dataclass(frozen=True)
class DpiInfo:
    """Screen scaling information.

    ``scale`` is the factor between logical (DIP) and physical pixels: on a
    150% Windows display ``scale == 1.5``.  ``origin`` is the virtual-desktop
    origin, which is negative when a monitor sits left/above the primary one.
    """

    scale: float = 1.0
    origin: tuple[int, int] = (0, 0)
    multi_monitor: bool = False

    def to_physical(self, rect: Rect) -> Rect:
        if self.scale == 1.0:
            return rect
        s = self.scale
        return Rect(
            round(rect.x * s),
            round(rect.y * s),
            round(rect.width * s),
            round(rect.height * s),
        )

    def to_logical(self, rect: Rect) -> Rect:
        if self.scale == 1.0:
            return rect
        s = self.scale
        return Rect(
            round(rect.x / s),
            round(rect.y / s),
            round(rect.width / s),
            round(rect.height / s),
        )


def normalize_dpi(scale: float = 1.0, origin: tuple[int, int] = (0, 0),
                  multi_monitor: bool = False) -> DpiInfo:
    if not scale or scale <= 0:
        scale = 1.0
    return DpiInfo(scale=float(scale), origin=(int(origin[0]), int(origin[1])),
                   multi_monitor=bool(multi_monitor))


def center(rect: Rect) -> tuple[int, int]:
    """The safe click point: the geometric center of the box."""
    return rect.center


def relative(rect: Rect, window: Rect) -> tuple[int, int]:
    """Box position relative to the window's top-left corner (``rx, ry``)."""
    return (rect.x - window.x, rect.y - window.y)


def is_offscreen(rect: Rect, screen: Optional[Rect] = None,
                 window: Optional[Rect] = None) -> bool:
    """True when the element cannot be clicked: zero size, hidden or outside.

    With no ``screen`` given, an element is offscreen when it has no area or
    lies completely outside its own window.
    """
    if rect.empty:
        return True
    area = screen or window
    if area is None or area.empty:
        return False
    # no overlap at all
    if rect.right <= area.x or rect.bottom <= area.y:
        return True
    if rect.x >= area.right or rect.y >= area.bottom:
        return True
    return False


def union(rects: Iterable[Rect]) -> Rect:
    rects = [r for r in rects if not r.empty]
    if not rects:
        return Rect()
    x = min(r.x for r in rects)
    y = min(r.y for r in rects)
    right = max(r.right for r in rects)
    bottom = max(r.bottom for r in rects)
    return Rect(x, y, right - x, bottom - y)


def element_geometry(el: Element, window: Optional[Rect] = None,
                     screen: Optional[Rect] = None,
                     snapshot_number: int = 0) -> dict:
    """Full geometry record for ``inspect`` / ``bounds`` (R10)."""
    rect = el.rect
    win = window or Rect()
    cx, cy = center(rect)
    rx, ry = relative(rect, win) if not win.empty else (0, 0)
    return {
        "x": rect.x,
        "y": rect.y,
        "width": rect.width,
        "height": rect.height,
        "cx": cx,
        "cy": cy,
        "rx": rx,
        "ry": ry,
        "offscreen": is_offscreen(rect, screen=screen, window=win),
        "snapshot": snapshot_number,
    }


def format_bounds(rect: Rect, window: Optional[Rect] = None,
                  screen: Optional[Rect] = None) -> str:
    """``1842,12 46x32`` - the only geometry form the snapshot ever prints."""
    win = window or Rect()
    parts = [f"{rect.x},{rect.y}", f"{rect.width}x{rect.height}"]
    text = " ".join(parts)
    if not win.empty:
        rx, ry = relative(rect, win)
        text += f" (win {rx},{ry})"
    if is_offscreen(rect, screen=screen, window=win):
        text += " offscreen"
    return text


def parse_rect(value) -> Rect:
    """Accept ``Rect``, ``{x,y,width,height}`` or ``[x, y, w, h]``."""
    if isinstance(value, Rect):
        return value
    if value is None:
        return Rect()
    if isinstance(value, dict):
        return Rect(
            int(value.get("x", 0) or 0),
            int(value.get("y", 0) or 0),
            int(value.get("width", value.get("w", 0)) or 0),
            int(value.get("height", value.get("h", 0)) or 0),
        )
    seq: Sequence = tuple(value)
    if len(seq) == 4:
        return Rect(int(seq[0]), int(seq[1]), int(seq[2]), int(seq[3]))
    raise ValueError(f"cannot parse a rectangle from {value!r}")
