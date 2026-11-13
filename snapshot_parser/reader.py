"""Raw tree -> :class:`~snapshot_parser.model.Element` tree.

Two raw formats are supported, both line/pipe based so a fixture diff is
readable in a PR review:

``agdesk-raw 1`` (the native, preferred format)::

    agdesk-raw 1
    app: Notepad
    title: snap.js - Notepad
    hwnd: 0x00512A
    number: 14
    session: syzqsppsg5kob
    ---
     e1|Window|Notepad|0,0,1920,1080|focus|maximized||0x00512A|visible|
      e2|Document|Text Editor|12,96,1896,960|focus,set value|focused|||visible|chars=2340
    ---
    e10|Button|Close|1842,12,46,32|click|||0x0031|visible|

The element block may repeat after a second ``---`` to describe an active
modal dialog (R9). ``\`` escapes ``|`` and newlines inside a field.

JSON is accepted too (``{"app": ..., "elements": [...]}``) because the Windows
UIA dumper produced that shape first and old captures keep working.

Nothing here prints anything: errors are raised as :class:`ReaderError` and the
CLI turns them into a readable line (Step 26).
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Iterable, Optional

from .geometry import parse_rect
from .model import Element, Rect, Snapshot, link_parents
from .roles import canonical_actions, normalize_role
from .state import extract_state

__all__ = [
    "ReaderError",
    "MAGIC",
    "parse_raw_text",
    "read_snapshot",
    "load_snapshot",
    "parse_raw_json",
    "render_raw_text",
    "raw_format_help",
]

MAGIC = "agdesk-raw 1"

_FIELD_COUNT = 10
_FIELDS = (
    "ref",
    "raw_role",
    "name",
    "rect",
    "actions",
    "state",
    "value",
    "native_id",
    "flags",
    "props",
)


class ReaderError(RuntimeError):
    """A raw dump could not be turned into a snapshot."""


# --------------------------------------------------------------------------
# field escaping
# --------------------------------------------------------------------------
def _escape(text: str) -> str:
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r", "")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )


def _unescape(text: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            out.append({"n": "\n", "t": "\t", "\\": "\\", "|": "|"}.get(nxt, nxt))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _split_fields(line: str) -> list[str]:
    """Split on unescaped pipes."""
    fields: list[str] = []
    current: list[str] = []
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line):
            current.append(ch)
            current.append(line[i + 1])
            i += 2
            continue
        if ch == "|":
            fields.append("".join(current))
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1
    fields.append("".join(current))
    return fields


# --------------------------------------------------------------------------
# element construction
# --------------------------------------------------------------------------
def build_element(
    *,
    ref: str,
    raw_role: str,
    name: str,
    rect: Rect,
    raw_actions: Iterable[str] = (),
    raw_state: Any = None,
    value: Optional[str] = None,
    native_id: str = "",
    flags: Iterable[str] = (),
    properties: Optional[dict] = None,
) -> Element:
    """Create one normalised element from raw provider values."""
    actions = canonical_actions(raw_actions)
    action_ids = {}
    for raw in raw_actions or ():
        short = canonical_actions([raw])
        if short:
            action_ids.setdefault(short[0], str(raw))
    state = extract_state(raw_state)
    flag_set = {str(f).strip().lower() for f in flags or ()}
    visible = "hidden" not in flag_set and "invisible" not in flag_set
    if "offscreen" in flag_set:
        state.setdefault("offscreen", True)
    element = Element(
        ref=str(ref),
        role=normalize_role(raw_role),
        name=(name or "").strip(),
        actions=actions,
        state=state,
        visible=visible,
        rect=rect,
        value=value,
        native_id=str(native_id or ""),
        raw_role=str(raw_role or ""),
        action_ids=action_ids,
        properties=dict(properties or {}),
    )
    return element


def _parse_pairs(text: str, sep: str = ",") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for chunk in str(text or "").split(sep):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" in chunk:
            key, _, val = chunk.partition("=")
            out[key.strip()] = val.strip()
        else:
            out[chunk] = True
    return out


# --------------------------------------------------------------------------
# native text format
# --------------------------------------------------------------------------
def parse_raw_text(text: str, *, source: str = "") -> Snapshot:
    lines = str(text or "").splitlines()
    if not lines:
        raise ReaderError(f"empty raw dump{f' in {source}' if source else ''}")
    header: dict[str, str] = {}
    blocks: list[list[tuple[int, str]]] = [[]]
    seen_magic = False
    for lineno, raw_line in enumerate(lines, start=1):
        if not raw_line.strip():
            continue
        if not seen_magic:
            if raw_line.strip().lower().startswith("agdesk-raw"):
                seen_magic = True
                version = raw_line.strip().split()[-1]
                if version != MAGIC.split()[-1]:
                    raise ReaderError(
                        f"unsupported raw format version '{version}', expected {MAGIC!r}"
                    )
                continue
            raise ReaderError(
                f"{source or 'raw dump'} is not an accessibility dump "
                f"(first line should be '{MAGIC}')"
            )
        if raw_line.strip() == "---":
            blocks.append([])
            continue
        if not raw_line[0].isspace() and raw_line.count("|") == 0:
            key, _, value = raw_line.partition(":")
            if _:
                header[key.strip().lower()] = value.strip()
                continue
        if "|" not in raw_line:
            continue
        blocks[-1].append((lineno, raw_line))

    snapshot = Snapshot(
        app=header.get("app", ""),
        title=header.get("title", ""),
        session=header.get("session", "local") or "local",
        backend=header.get("backend", "raw"),
    )
    try:
        number = int(header.get("number", "1") or 1)
    except ValueError:
        number = 1
    snapshot.number = number
    snapshot.bounds_snapshot = number
    snapshot.error = header.get("error", "")

    tree_blocks = [b for b in blocks if b]
    if not tree_blocks:
        raise ReaderError(
            f"raw dump{f' {source}' if source else ''} has no elements "
            "(expected lines like ' e1|Window|Notepad|0,0,800,600|focus')"
        )

    snapshot.root = _parse_block(tree_blocks[0], source, dialog=False)
    if len(tree_blocks) > 1:
        snapshot.dialog = _parse_block(tree_blocks[1], source, dialog=True)
    link_parents(snapshot.root)
    if snapshot.dialog is not None:
        link_parents(snapshot.dialog)
    return snapshot


def _parse_block(block: list[tuple[int, str]], source: str, dialog: bool) -> Element:
    root: Optional[Element] = None
    stack: list[tuple[int, Element]] = []
    for lineno, line in block:
        depth = len(line) - len(line.lstrip(" "))
        fields = _split_fields(line.strip())
        if len(fields) < 3:
            raise ReaderError(
                f"{source or 'raw dump'}:{lineno}: needs at least "
                f"ref|role|name, got {line.strip()!r}"
            )
        fields += [""] * (_FIELD_COUNT - len(fields))
        (
            ref,
            raw_role,
            name,
            rect_text,
            actions_text,
            state_text,
            value,
            native_id,
            flags,
            props,
        ) = fields[:_FIELD_COUNT]
        try:
            rect = parse_rect([int(v) for v in rect_text.split(",")]) if rect_text else Rect()
        except (ValueError, TypeError):
            raise ReaderError(
                f"{source or 'raw dump'}:{lineno}: bad rect {rect_text!r} "
                "(expected x,y,width,height)"
            ) from None
        element = build_element(
            ref=_unescape(ref).strip(),
            raw_role=_unescape(raw_role),
            name=_unescape(name),
            rect=rect,
            raw_actions=[a for a in _unescape(actions_text).split(",") if a.strip()],
            raw_state=_unescape(state_text),
            value=_unescape(value) if value else None,
            native_id=_unescape(native_id),
            flags=[f for f in flags.split(",") if f.strip()],
            properties=_parse_pairs(_unescape(props), sep=";"),
        )
        if dialog and root is None:
            # the dialog itself is modal; its children just live inside it (R9)
            element.properties.setdefault("modal", True)
            element.state.setdefault("modal", True)
        if root is None:
            if depth:
                raise ReaderError(
                    f"{source or 'raw dump'}:{lineno}: first element must be at depth 0"
                )
            root = element
            stack = [(depth, element)]
            continue
        while stack and stack[-1][0] >= depth:
            stack.pop()
        if not stack:
            raise ReaderError(
                f"{source or 'raw dump'}:{lineno}: indentation jumps (depth {depth})"
            )
        stack[-1][1].add(element)
        stack.append((depth, element))
    if root is None:
        raise ReaderError(f"{source or 'raw dump'}: empty element block")
    return root


def render_raw_text(snapshot: Snapshot, *, include_dialog: bool = True) -> str:
    """Serialise a snapshot back to the native raw format (fixture authoring)."""
    lines = [MAGIC]
    lines.append(f"app: {snapshot.app}")
    lines.append(f"title: {snapshot.title}")
    lines.append(f"number: {snapshot.number}")
    lines.append(f"session: {snapshot.session}")
    if snapshot.backend:
        lines.append(f"backend: {snapshot.backend}")
    if snapshot.error:
        lines.append(f"error: {_escape(snapshot.error)}")
    if snapshot.root is not None:
        lines.append("---")
        lines.extend(_render_block(snapshot.root, 0))
    if include_dialog and snapshot.dialog is not None:
        lines.append("---")
        lines.extend(_render_block(snapshot.dialog, 0))
    return "\n".join(lines) + "\n"


def _render_block(root: Element, base_depth: int) -> list[str]:
    out: list[str] = []
    for el in root.walk():
        depth = _depth_of(root, el)
        rect = "" if el.rect.empty else f"{el.rect.x},{el.rect.y},{el.rect.width},{el.rect.height}"
        actions = ",".join(el.action_ids.get(a, a) for a in el.actions)
        state = ",".join(f"{k}={str(v).lower()}" if not isinstance(v, bool) else k
                         for k, v in el.state.items())
        flags = "visible" if el.visible else "hidden"
        props = ";".join(f"{k}={v}" for k, v in el.properties.items() if v not in (None, ""))
        fields = [
            _escape(el.ref),
            _escape(el.raw_role or el.role),
            _escape(el.name),
            rect,
            actions,
            state,
            _escape(el.value or ""),
            _escape(el.native_id),
            flags,
            _escape(props),
        ]
        out.append(" " * (base_depth + depth) + "|".join(fields))
    return out


def _depth_of(root: Element, target: Element) -> int:
    stack: list[tuple[Element, int]] = [(root, 0)]
    while stack:
        node, depth = stack.pop()
        if node is target:
            return depth
        for child in reversed(node.children):
            stack.append((child, depth + 1))
    return 0


# --------------------------------------------------------------------------
# JSON format (legacy Windows dumps)
# --------------------------------------------------------------------------
_JSON_ROLE_KEYS = ("role", "control_type", "controlType", "type", "class_name")
_JSON_NAME_KEYS = ("name", "title", "text", "label")
_JSON_ACTION_KEYS = ("actions", "patterns", "action_ids")
_JSON_STATE_KEYS = ("state", "states", "properties")


def parse_raw_json(data: dict, *, source: str = "") -> Snapshot:
    if not isinstance(data, dict):
        raise ReaderError(f"{source or 'dump'} is not a JSON object")
    root_data = data.get("root") or data.get("tree") or data.get("elements")
    if root_data is None:
        raise ReaderError(f"{source or 'dump'} has no 'root'/'elements' key")
    if isinstance(root_data, list):
        if not root_data:
            raise ReaderError(f"{source or 'dump'} has an empty element list")
        root_data = root_data[0]
    snapshot = Snapshot(
        app=str(data.get("app", data.get("application", "")) or ""),
        title=str(data.get("title", data.get("window_title", "")) or ""),
        number=int(data.get("number", data.get("snapshot", 1)) or 1),
        session=str(data.get("session", "local") or "local"),
        backend=str(data.get("backend", "json")),
    )
    snapshot.bounds_snapshot = snapshot.number
    snapshot.root = _json_element(root_data, counter=[0])
    dialog_data = data.get("dialog")
    if dialog_data:
        snapshot.dialog = _json_element(dialog_data, counter=[0])
    return snapshot


def _json_element(node: dict, counter: list[int]) -> Element:
    counter[0] += 1
    ref = str(node.get("ref") or node.get("id") or f"e{counter[0]}")
    raw_role = ""
    for key in _JSON_ROLE_KEYS:
        if node.get(key):
            raw_role = str(node[key])
            break
    name = ""
    for key in _JSON_NAME_KEYS:
        if node.get(key):
            name = str(node[key])
            break
    rect = parse_rect(node.get("rect") or node.get("bounds") or node.get("rectangle"))
    actions: list[str] = []
    for key in _JSON_ACTION_KEYS:
        val = node.get(key)
        if isinstance(val, list):
            actions.extend(str(v) for v in val)
        elif isinstance(val, str):
            actions.extend(part for part in val.split(",") if part.strip())
    state: Any = {}
    for key in _JSON_STATE_KEYS:
        if isinstance(node.get(key), dict):
            state = node[key]
            break
    if not state:
        state = {k: v for k, v in node.items()
                 if k in ("focused", "enabled", "checked", "expanded", "selected",
                          "offscreen", "modal", "readonly")}
        if "enabled" in state:
            state["disabled"] = not bool(state.pop("enabled"))
    el = build_element(
        ref=ref,
        raw_role=raw_role,
        name=name,
        rect=rect,
        raw_actions=actions,
        raw_state=state,
        value=node.get("value"),
        native_id=str(node.get("native_id") or node.get("runtime_id") or node.get("handle") or ""),
        flags=[] if node.get("visible", True) else ["hidden"],
        properties={k: v for k, v in node.items()
                    if k not in _JSON_ROLE_KEYS + _JSON_NAME_KEYS + _JSON_ACTION_KEYS
                    and k not in ("ref", "id", "children", "rect", "bounds", "rectangle")},
    )
    for child in node.get("children", []) or []:
        el.add(_json_element(child, counter))
    return el


# --------------------------------------------------------------------------
# entry points
# --------------------------------------------------------------------------
def read_snapshot(source: str | dict, *, backend: str = "auto") -> Snapshot:
    """Load a snapshot from a path, raw text, or parsed JSON dict."""
    if isinstance(source, dict):
        return parse_raw_json(source)
    if not isinstance(source, str):
        raise ReaderError(f"cannot read a snapshot from {type(source).__name__}")
    if "\n" in source or source.lstrip().lower().startswith("agdesk-raw"):
        return parse_raw_text(source)
    if not os.path.exists(source):
        raise ReaderError(
            f"no such snapshot file: {source} "
            "(capture one with `python tools/capture_raw.py --out <file>`)"
        )
    try:
        with open(source, "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        raise ReaderError(f"cannot read {source}: {exc.strerror or exc}") from None
    except UnicodeDecodeError:
        raise ReaderError(f"{source} is not UTF-8 text") from None
    if source.lower().endswith(".json") or text.lstrip().startswith("{"):
        try:
            return parse_raw_json(json.loads(text), source=source)
        except json.JSONDecodeError as exc:
            raise ReaderError(f"{source} is not valid JSON: {exc.msg} (line {exc.lineno})") from None
    return parse_raw_text(text, source=source)


#: Backwards-friendly alias used by tools and tests.
load_snapshot = read_snapshot


def raw_format_help() -> str:
    return (
        f"{MAGIC}\n"
        "app: <app name>\n"
        "title: <window title>\n"
        "number: <snapshot number>\n"
        "session: <session id>\n"
        "---\n"
        " <depth spaces>ref|role|name|x,y,w,h|actions|state|value|native_id|flags|props\n"
        "---                      # optional second block: active modal dialog\n"
    )
