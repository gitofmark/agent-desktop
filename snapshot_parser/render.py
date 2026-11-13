"""Element tree -> minimal natural snapshot text.

The output is the product, so every rule lives here in one place:

R1  header with app, title, snapshot number, session (stated once)
R2  ``[role] Name eN`` for every interactive element
R3  elements with no name, no action, no value are skipped (their children
    are hoisted so no interactive child is ever lost)
R4  non-default state, and actions only when role or state does not imply them
R5  no bounds / native ids; editors get a <= 80 char preview plus length
R6  siblings of the same role share one line, separated by ``·``
R7  visible lines only, capped, overflow collapsed to one inspectable line
R9  active modal dialog first, the window behind it marked ``(background)``
R10 coordinates only with ``--bounds``, as ``Close e10 @ 1842,12 46x32``

The exact shape is locked by ``tests/golden/*.txt``::

    Notepad — "snap.js - Notepad"  (snapshot 14, session syzqsppsg5kob)

    [window] snap.js - Notepad e1 — maximized
    [window_button] Minimize e4 · Maximize e5 · Close e6
    [menu] File e8 · Edit e9 · Format e10 · View e11 · Help e12
    [edit] Text Editor e13 — focused, 2340 chars, starts "const snap = ..."
    [scrollbar] Vertical e14 — line up e15, page up e16, line down e18
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from .budget import DEFAULT_MAX_LINES, LineItem, cap_lines, is_renderable
from .geometry import format_bounds
from .model import Element, Rect, Snapshot
from .roles import actions_of, role_label
from .state import visible_state

__all__ = [
    "RenderOptions",
    "MAX_PREVIEW",
    "MAX_NAME",
    "render_snapshot",
    "render_header",
    "render_line",
    "render_element",
    "render_error",
    "preview_text",
    "format_actions",
    "element_extras",
    "shorten_name",
]

#: Editor preview length (R5).
MAX_PREVIEW = 80
#: Names longer than this are shortened; the full name stays in ``inspect``.
MAX_NAME = 60

#: Unnamed structure: not printed, children take its place (R3).
TRANSPARENT_ROLES = frozenset({"pane", "group", "application", "unknown", "custom", "menubar"})

#: Root roles the header already represents, so the root line is skipped
#: unless it carries state of its own.
ROOT_SKIP_ROLES = frozenset({"window", "application", "pane", "group", "unknown"})

#: Names that mean "window button" when they appear as a run of buttons.
WINDOW_BUTTON_NAMES = frozenset({"minimize", "maximize", "restore", "close", "exit"})

#: Roles whose children ride on the parent line instead of their own lines
#: (R6b): a scrollbar's four arrow buttons, or the items of an open menu.
INLINE_CHILD_ROLES = frozenset({"scrollbar", "slider", "spin", "splitbutton",
                                "menuitem", "combobox", "treeitem"})

#: Roles whose inline children keep their original capitalisation.
_CASED_INLINE_PARENTS = frozenset({"menuitem", "treeitem", "combobox"})

#: A grouped line never carries more than this many refs.  A row of 500
#: buttons is not "minimal": long runs wrap into several lines instead.
MAX_RUN = 8

#: Hard width guard for a single line, in characters.
MAX_LINE = 240

#: Roles never grouped with siblings: each one deserves its own line.
NEVER_GROUPED = frozenset({"edit", "document", "text", "dialog", "window",
                           "menu", "menubar", "separator", "unknown"})


@dataclass
class RenderOptions:
    """Everything the caller can change about the output."""

    max_lines: int = DEFAULT_MAX_LINES
    show_bounds: bool = False
    full: bool = False
    max_preview: int = MAX_PREVIEW
    include_offscreen: bool = False
    include_hidden: bool = False
    group: bool = True
    show_actions: bool = True
    max_run: int = MAX_RUN
    #: plain ASCII separators for terminals that cannot show ``—`` or ``·``
    ascii: bool = False

    def __post_init__(self) -> None:
        if self.full:
            self.max_lines = 0
            self.include_offscreen = True

    @property
    def dash(self) -> str:
        return " - " if self.ascii else " — "

    @property
    def dot(self) -> str:
        return " | " if self.ascii else " · "

    @property
    def arrow(self) -> str:
        return " > " if self.ascii else " › "


# --------------------------------------------------------------------------
# small formatting helpers
# --------------------------------------------------------------------------
def shorten_name(name: str, limit: int = MAX_NAME) -> str:
    text = " ".join(str(name or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def preview_text(text: str, limit: int = MAX_PREVIEW) -> str:
    """One-line preview of a long value (R5), cut at ``limit``."""
    flat = " ".join(str(text or "").split()).replace('"', "'")
    if len(flat) > limit:
        flat = flat[:limit].rstrip() + "..."
    return flat


def format_actions(actions: Sequence[str]) -> str:
    return ", ".join(actions)


def _text_of(el: Element) -> Optional[str]:
    if el.value is not None:
        return str(el.value)
    text = el.properties.get("text")
    return None if text is None else str(text)


def _length_note(el: Element) -> str:
    """``2340 chars`` / ``120 chars, 4 lines`` for text-bearing elements."""
    text = _text_of(el)
    if text is None:
        raw = el.properties.get("chars") or el.properties.get("length")
        return f"{int(raw)} chars" if raw else ""
    if text == "":
        return "0 chars"
    if "\n" in text:
        lines = text.count("\n") + (0 if text.endswith("\n") else 1)
        return f"{len(text)} chars, {lines} lines"
    return f"{len(text)} chars"


def _preview_note(el: Element, opts: RenderOptions) -> str:
    text = _text_of(el)
    if text is None or not text.strip():
        return ""
    return f'starts "{preview_text(text, opts.max_preview)}"'


def element_extras(el: Element, opts: RenderOptions) -> list[str]:
    """What an element adds after ``eN``: length, preview, value, actions, bounds."""
    extras: list[str] = []
    if el.role in ("edit", "document"):
        length = _length_note(el)
        if length:
            extras.append(length)
        preview = _preview_note(el, opts)
        if preview:
            extras.append(preview)
    elif el.role == "text":
        # a static label spends its line on the words, not on a name + ref pair
        text = _text_of(el)
        if text and text.strip():
            extras.append(f'"{preview_text(text, opts.max_preview)}"')
    elif el.role in ("combobox", "spin", "slider") and el.value:
        extras.append(f'"{preview_text(str(el.value), 40)}"')
    if opts.show_actions:
        actions = actions_of(el)
        if actions:
            extras.append(format_actions(actions))
    return extras


# --------------------------------------------------------------------------
# one element -> one line
# --------------------------------------------------------------------------
def render_line(el: Element, *, window: Optional[Rect] = None,
                screen: Optional[Rect] = None, opts: Optional[RenderOptions] = None,
                name: Optional[str] = None, extras: Optional[list[str]] = None) -> str:
    opts = opts or RenderOptions()
    shown = shorten_name(el.name) if name is None else name
    head = f"[{el.role}]"
    if shown:
        head += f" {shown}"
    head += f" {el.ref}"

    parts = list(visible_state(el.state))
    if extras is None:
        parts.extend(element_extras(el, opts))
        if opts.show_bounds:
            parts.append("@" + format_bounds(el.rect))
    else:
        parts.extend(extras)
    if not parts:
        return head
    return head + opts.dash + ", ".join(parts)


def _clamp(text: str, limit: int = MAX_LINE) -> str:
    """Keep one line readable; refs past the guard are reachable via overflow."""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _group_label(parent: Optional[Element], run: Sequence[Element],
                 opts: RenderOptions) -> str:
    """Role word in front of a grouped line (R6)."""
    role = run[0].role
    names = {k.name.strip().lower() for k in run if k.name}
    if role == "button" and len(run) >= 2 and names and names <= WINDOW_BUTTON_NAMES:
        return "window_button"
    if role == "menuitem" and parent is not None and (
        parent.role in ("menubar", "toolbar", "menu") or parent.name
    ):
        return "menu"
    return role


def _run_item(parent: Optional[Element], run: Sequence[Element],
              opts: RenderOptions) -> LineItem:
    """One ``·``-joined line for a run of same-role leaves (R6)."""
    label = _group_label(parent, run, opts)
    parts = []
    for kid in run:
        name = shorten_name(kid.name)
        text = f"{name} {kid.ref}" if name else kid.ref
        if opts.show_bounds:
            # R10: every element keeps its own geometry, even inside a run
            text += f" @{format_bounds(kid.rect)}"
        parts.append(text)
    joined = opts.dot.join(parts)
    if label == "menu" and parent is not None and parent.name and len(run) == len(
            parent.children or run):
        joined = f"{shorten_name(parent.name)}{opts.arrow}{joined}"
    text = _clamp(f"[{label}] {joined}")
    return LineItem(text=text, role=label, ref=run[0].ref,
                    refs=[k.ref for k in run])


# --------------------------------------------------------------------------
# grouping decisions
# --------------------------------------------------------------------------
def _groupable(el: Element, opts: RenderOptions) -> bool:
    """Can this single element join a ``·`` run on one line? (R6)"""
    if not opts.group or el.children:
        return False
    if el.role in NEVER_GROUPED:
        return False
    if not is_renderable(el, include_offscreen=opts.include_offscreen,
                         include_hidden=opts.include_hidden):
        return False
    if visible_state(el.state, include_offscreen=opts.include_offscreen):
        return False
    if element_extras(el, RenderOptions(**{**opts.__dict__, "show_bounds": False})):
        return False
    return True


def _inline_children(el: Element, opts: RenderOptions) -> list[Element]:
    """Leaf children that ride on the parent's line instead of their own (R6b)."""
    if el.role not in INLINE_CHILD_ROLES or not el.children:
        return []
    if el.role in ("menuitem", "combobox", "treeitem") and not (
        el.state.get("expanded") or el.role == "combobox"
    ):
        return []
    allowed = {"menuitem"} if el.role in ("menuitem", "combobox") else {"button", "menuitem"}
    kids = [c for c in el.children
            if is_renderable(c, include_offscreen=opts.include_offscreen,
                             include_hidden=opts.include_hidden)]
    if len(kids) < 2:
        return []
    for kid in kids:
        if kid.children or kid.role not in allowed or not kid.name:
            return []
        if visible_state(kid.state, include_offscreen=opts.include_offscreen):
            return []
    return kids


# --------------------------------------------------------------------------
# walking the tree
# --------------------------------------------------------------------------
def _should_show(el: Element, *, also_root: bool, skip_root: bool) -> bool:
    if not is_renderable(el):
        pass
    if also_root and skip_root and el.role in ROOT_SKIP_ROLES and not visible_state(el.state):
        return False
    if el.role in TRANSPARENT_ROLES and not el.name and not visible_state(el.state):
        return False
    return True


def _build_items(root: Element, opts: RenderOptions, *,
                 skip_root: bool = False, background: bool = False) -> list[LineItem]:
    """Turn a subtree into printable lines, applying R3/R6/R6b in document order."""
    items: list[LineItem] = []

    def emit_children(parent: Element) -> None:
        """Children of ``parent`` in order, with runs of same-role leaves grouped."""
        run: list[Element] = []
        inline = {id(k) for k in _inline_children(parent, opts)}

        def flush() -> None:
            if not run:
                return
            if len(run) == 1:
                emit(run[0], False)
            else:
                limit = opts.max_run if opts.max_run and opts.max_run > 0 else len(run)
                for start in range(0, len(run), limit):
                    items.append(_run_item(parent, run[start:start + limit], opts))
            run.clear()

        for child in parent.children:
            if id(child) in inline:
                continue
            if _groupable(child, opts) and (not run or run[0].role == child.role):
                run.append(child)
                continue
            flush()
            emit(child, False)
        flush()

    def emit(el: Element, also_root: bool) -> None:
        renderable = is_renderable(el, include_offscreen=opts.include_offscreen,
                                   include_hidden=opts.include_hidden)
        if not renderable:
            # R3: a dropped container is structure, not content - its children
            # take its place, still grouped, and still in document order
            emit_children(el)
            return

        inline = _inline_children(el, opts)
        if _should_show(el, also_root=also_root, skip_root=skip_root):
            extras = element_extras(el, opts)
            if inline:
                label_children = (el.role in _CASED_INLINE_PARENTS)
                inline_parts = []
                for kid in inline:
                    kid_name = shorten_name(kid.name) if label_children else kid.name.lower()
                    text = f"{kid_name} {kid.ref}"
                    if opts.show_bounds:
                        text += f" @{format_bounds(kid.rect)}"
                    inline_parts.append(text)
                extras.append(", ".join(inline_parts))
            if opts.show_bounds and not inline:
                extras.append("@" + format_bounds(el.rect))
            items.append(LineItem(text=_clamp(render_line(el, opts=opts, extras=extras)),
                                  role=el.role, ref=el.ref))
        emit_children(el)

    emit(root, True)
    if background and items:
        first = items[0]
        items[0] = LineItem(text=first.text + " (background)", role=first.role,
                            ref=first.ref, refs=list(first.refs))
    return items


# --------------------------------------------------------------------------
# header / error
# --------------------------------------------------------------------------
def render_header(snapshot: Snapshot, opts: Optional[RenderOptions] = None) -> str:
    """R1: ``Notepad — "snap.js - Notepad"  (snapshot 14, session sz5k)``"""
    title = " ".join(str(snapshot.title or "").split())
    app = " ".join(str(snapshot.app or "").split())
    if title and not app:
        head = f'"{title}"'
    elif title:
        head = f'{app} — "{title}"'
    elif app:
        head = app
    else:
        head = "?unknown window"
    if opts is not None and opts.ascii:
        head = head.replace("—", "-")
    return f"{head}  (snapshot {snapshot.number}, session {snapshot.session})"


def render_error(message: str, snapshot: Optional[Snapshot] = None) -> str:
    """A readable one-line error, never a traceback (Step 26)."""
    text = " ".join(str(message or "unknown error").split())
    prefix = ""
    if snapshot is not None and (snapshot.app or snapshot.title):
        prefix = render_header(snapshot) + "\n\n"
    return f"{prefix}! {text}"


# --------------------------------------------------------------------------
# top level
# --------------------------------------------------------------------------
def render_snapshot(snapshot: Snapshot, opts: Optional[RenderOptions] = None,
                    screen: Optional[Rect] = None) -> str:
    """Render a whole snapshot: header, then capped element lines."""
    opts = opts or RenderOptions()
    if snapshot.error or snapshot.root is None:
        return render_error(snapshot.error or "no accessibility tree for this window",
                            snapshot)

    items: list[LineItem] = []
    if snapshot.dialog is not None:
        # R9: the dialog first, the window behind it marked as background
        items.extend(_build_items(snapshot.dialog, opts))
        items.extend(_build_items(snapshot.root, opts, skip_root=False, background=True))
    else:
        items.extend(_build_items(snapshot.root, opts, skip_root=True))

    lines = cap_lines(items, opts.max_lines)
    header = render_header(snapshot, opts)
    body = "\n".join(lines)
    return f"{header}\n\n{body}" if body else header


def render_element(snapshot: Snapshot, ref: str,
                   opts: Optional[RenderOptions] = None) -> str:
    """Re-render a single element (``find``, ``diff``)."""
    opts = opts or RenderOptions()
    el = snapshot.find(ref)
    if el is None:
        return ""
    return render_line(el, opts=opts)


def render_role_list(roles: Sequence[str]) -> str:
    return ", ".join(role_label(r) for r in roles)
