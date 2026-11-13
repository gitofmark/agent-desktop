"""Core data model for the ECHO minimal snapshot.

The parser converts a raw accessibility tree into a tree of :class:`Element`
objects wrapped in a :class:`Snapshot`.  Everything else (roles, state,
rendering, refs, budget, inspect) operates on these two classes and nothing
else, so the model has to stay boring and dependency free.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Optional

__all__ = ["Element", "Snapshot", "Rect", "DEFAULT_ACTIONS", "ROLE_INTERESTING"]

# Actions that exist on essentially every element and therefore carry no
# information when rendered.  They are kept on the element (so an executor can
# always fall back to focus/click) but are never printed unless they are
# explicitly requested or are the only reason the element survives R3.
DEFAULT_ACTIONS = frozenset({"focus"})

# Role families the parser keeps even when the element has no actions.
ROLE_INTERESTING = frozenset(
    {
        "window",
        "application",
        "document",
        "text",
        "pane",
        "group",
        "list",
        "listitem",
        "table",
        "row",
        "cell",
        "tree",
        "treeitem",
        "menu",
        "menuitem",
        "menubar",
        "edit",
        "button",
        "checkbox",
        "radio",
        "combobox",
        "slider",
        "spin",
        "tab",
        "tabitem",
        "scrollbar",
        "toolbar",
        "statusbar",
        "link",
        "image",
        "progressbar",
        "separator",
        "splitbutton",
        "header",
        "unknown",
    }
)


@dataclass(frozen=True)
class Rect:
    """A screen-pixel rectangle, top-left origin."""

    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0

    @property
    def empty(self) -> bool:
        return self.width <= 0 or self.height <= 0

    @property
    def cx(self) -> int:
        return self.x + self.width // 2

    @property
    def cy(self) -> int:
        return self.y + self.height // 2

    @property
    def center(self) -> tuple[int, int]:
        return (self.cx, self.cy)

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    def offset(self, ox: int, oy: int) -> "Rect":
        return Rect(self.x - ox, self.y - oy, self.width, self.height)

    def contains(self, px: int, py: int) -> bool:
        return self.x <= px < self.right and self.y <= py < self.bottom

    def moved_by(self, dx: int, dy: int) -> "Rect":
        return Rect(self.x + dx, self.y + dy, self.width, self.height)

    def as_dict(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}

    def __str__(self) -> str:  # 1842,12 46x32
        return f"{self.x},{self.y} {self.width}x{self.height}"


@dataclass
class Element:
    """One node of the accessibility tree.

    ``ref`` is the *short* ref (``e10``).  The full ref ``@session:e10`` is
    reconstructed by :mod:`snapshot_parser.refs`; it is never stored here so a
    snapshot cannot get out of sync with itself.
    """

    ref: str = ""
    role: str = "unknown"
    name: str = ""
    actions: list[str] = field(default_factory=list)
    state: dict[str, Any] = field(default_factory=dict)
    children: list["Element"] = field(default_factory=list)
    visible: bool = True
    rect: Rect = field(default_factory=Rect)
    value: Optional[str] = None
    native_id: str = ""
    raw_role: str = ""
    #: raw provider action ids ("Click", "SetValue", ...) keyed by short action
    action_ids: dict[str, str] = field(default_factory=dict)
    #: anything else the backend saw; shown only by ``inspect``
    properties: dict[str, Any] = field(default_factory=dict)
    #: element ref of the parent, filled in by ``link_parents``
    parent_ref: str = ""

    # -- tree helpers ----------------------------------------------------
    def add(self, child: "Element") -> "Element":
        self.children.append(child)
        return child

    def walk(self) -> Iterator["Element"]:
        """Depth-first pre-order walk including ``self``."""
        yield self
        for child in self.children:
            yield from child.walk()

    def find(self, ref: str) -> Optional["Element"]:
        for el in self.walk():
            if el.ref == ref:
                return el
        return None

    def descendants(self) -> Iterator["Element"]:
        for child in self.children:
            yield from child.walk()

    @property
    def is_leaf(self) -> bool:
        return not self.children

    def has_action(self, action: str) -> bool:
        return action in self.actions

    def non_default_actions(self) -> list[str]:
        return [a for a in self.actions if a not in DEFAULT_ACTIONS]

    def __str__(self) -> str:  # debugging only, never user output
        return f"<Element {self.ref} {self.role} {self.name!r}>"


def link_parents(root: Element) -> None:
    """Fill ``parent_ref`` for every element below ``root``."""
    for el in root.walk():
        for child in el.children:
            child.parent_ref = el.ref


@dataclass
class Snapshot:
    """A numbered capture of one window."""

    app: str = ""
    title: str = ""
    number: int = 1
    session: str = "local"
    root: Optional[Element] = None
    #: active modal dialog, rendered first, with the window marked (background)
    dialog: Optional[Element] = None
    #: app-level error, rendered as a single readable line (never a traceback)
    error: str = ""
    #: monotonic capture time, used by the performance checks
    elapsed_ms: float = 0.0
    #: source of the tree: "fixture", "linux-x11", "windows-uia", ...
    backend: str = ""
    #: snapshot number the stored geometry was read at (R10)
    bounds_snapshot: Optional[int] = None

    def __post_init__(self) -> None:
        if self.bounds_snapshot is None:
            self.bounds_snapshot = self.number
        if self.root is not None:
            link_parents(self.root)

    def walk(self) -> Iterator[Element]:
        if self.root is None:
            return iter(())
        return self.root.walk()

    def elements(self) -> list[Element]:
        return list(self.walk())

    def find(self, ref: str) -> Optional[Element]:
        if self.root is None:
            return None
        return self.root.find(ref)

    def refs(self) -> list[str]:
        return [el.ref for el in self.walk()]

    def by_ref(self) -> dict[str, Element]:
        return {el.ref: el for el in self.walk()}

    def count(self) -> int:
        return sum(1 for _ in self.walk())

    def window_rect(self) -> Rect:
        return self.root.rect if self.root is not None else Rect()

    def full_ref(self, ref: str) -> str:
        return f"@{self.session}:{ref}"

    @property
    def ok(self) -> bool:
        return not self.error and self.root is not None
