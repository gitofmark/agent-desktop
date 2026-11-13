"""Line budget: visible-only filtering, the line cap, overflow collapsing (R3, R7).

The renderer builds one *line item* per printed line; this module decides how
many of them survive and turns the rest into a single ``... 12 more buttons
(inspect e40)`` line so nothing is ever silently lost.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

from .model import Element, Snapshot
from .roles import ROLE_LABELS, SHORT_ROLES, role_label
from .state import is_offstage

__all__ = [
    "ANONYMOUS_ROLES",
    "DEFAULT_MAX_LINES",
    "LineItem",
    "is_interesting",
    "is_renderable",
    "cap_lines",
    "overflow_line",
    "renderable_elements",
]

#: Roles that carry no information on their own: an unnamed pane/group is
#: structure, not content, so it is dropped and its children take its place.
ANONYMOUS_ROLES = frozenset({"pane", "group", "unknown", "application", "window",
                             "document", "text"})

DEFAULT_MAX_LINES = 80


@dataclass
class LineItem:
    """One printable line, before the budget is applied."""

    text: str
    role: str = ""
    ref: str = ""
    #: refs of every element represented by this line (a group holds several)
    refs: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.refs and self.ref:
            self.refs = [self.ref]


def is_interesting(el: Element) -> bool:
    """R3: keep when it has something to say.

    A name, a real action, a value or a notable state is content.  An unnamed
    container only holds other elements, and an unnamed leaf with no action
    (a separator, a decorative image) is noise: both are dropped, and a
    container's children take its place in the output.
    """
    if el.role == "dialog":
        return True
    if el.name or el.value:
        return True
    if el.non_default_actions():
        return True
    return False


def is_renderable(el: Element, include_offscreen: bool = False,
                  include_hidden: bool = False) -> bool:
    """Visible-only (R7) + R3 filter, applied to one element."""
    if not include_hidden and not el.visible:
        return False
    if not include_offscreen and is_offstage(el.state):
        return False
    return is_interesting(el)


def renderable_elements(snapshot: Snapshot, include_offscreen: bool = False,
                        include_hidden: bool = False) -> list[Element]:
    """Flat depth-first list of everything that may be printed."""
    if snapshot.root is None:
        return []
    out: list[Element] = []

    def visit(el: Element) -> None:
        if is_renderable(el, include_offscreen=include_offscreen,
                         include_hidden=include_hidden):
            out.append(el)
        for child in el.children:
            visit(child)

    visit(snapshot.root)
    return out


def overflow_line(dropped: Sequence[LineItem]) -> str:
    """``... 12 more buttons, 3 more menu items (inspect e40)``"""
    if not dropped:
        return ""
    counts: dict[str, int] = {}
    order: list[str] = []
    for item in dropped:
        role = item.role or "element"
        if role not in counts:
            counts[role] = 0
            order.append(role)
        counts[role] += 1
    parts = [f"{counts[r]} more {role_label(r)}" for r in order]
    first_ref = dropped[0].ref or (dropped[0].refs[0] if dropped[0].refs else "")
    hint = f" (inspect {first_ref})" if first_ref else ""
    return "... " + ", ".join(parts) + hint


def cap_lines(items: Sequence[LineItem], max_lines: int = DEFAULT_MAX_LINES) -> list[str]:
    """Apply the line cap, replacing the tail with one overflow line (R7).

    ``max_lines <= 0`` means no cap (``--full``).  The cap counts *content*
    lines; when anything is dropped, one extra footer line names what was
    dropped and how to inspect it, so the total is ``max_lines + 1``.
    """
    if max_lines is None or max_lines <= 0 or len(items) <= max_lines:
        return [item.text for item in items]
    kept = list(items[:max_lines])
    dropped = list(items[max_lines:])
    text = [item.text for item in kept]
    line = overflow_line(dropped)
    if line:
        # the overflow line is a footer, not one of the capped content lines
        text.append(line)
    return text
