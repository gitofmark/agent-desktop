"""On-demand detail for one ref (R5, R10).

The compact snapshot deliberately drops native ids, full document text and
geometry.  This module is where they come back, for exactly one element, so a
model that needs the position of Close pays for one short block instead of a
table of coordinates.
"""

from __future__ import annotations

from typing import Any, Optional

from .geometry import element_geometry, format_bounds, is_offscreen
from .model import Element, Rect, Snapshot
from .refs import RefError, validate_ref
from .roles import ROLE_LABELS, actions_of, raw_for_action
from .state import visible_state

__all__ = [
    "inspect_to_dict",
    "format_inspect",
    "read_value",
    "bounds_of",
    "find_elements",
    "format_bounds_line",
    "inspect_element",
]


def _window(snapshot: Snapshot) -> Rect:
    return snapshot.window_rect()


def _validate(ref: str, number: int, session: str, snapshot: Snapshot) -> str:
    """Stale/session checks against the *query* snapshot number, then existence."""
    parts = validate_ref(ref, number, session or snapshot.session, None)
    if number != snapshot.number:
        # the caller is asking about a snapshot we no longer have: say so
        # instead of silently answering from the current tree
        raise RefError(
            f"ref '{ref}' is stale: it was taken at snapshot {number}, the current "
            f"snapshot is number {snapshot.number}; the tree changed since then - "
            "run `agent-desktop snapshot` again and use the new refs",
            ref=ref,
        )
    if _find(snapshot, parts) is None:
        raise RefError(
            f"unknown ref '{ref}' in snapshot {number} (session "
            f"{session or snapshot.session}); it may have been collapsed by the line "
            "cap - try `agent-desktop snapshot --full` or `agent-desktop find \"<name>\"`",
            ref=ref,
        )
    return parts


def _find(snapshot: Snapshot, short: str) -> Optional[Element]:
    """Find a ref in the window tree *or* the modal dialog block (R9)."""
    el = snapshot.find(short)
    if el is None and snapshot.dialog is not None:
        el = snapshot.dialog.find(short)
    return el


def inspect_to_dict(snapshot: Snapshot, ref: str,
                    snapshot_number: Optional[int] = None,
                    session: str = "") -> dict[str, Any]:
    """Everything known about one element, as a plain dict (``--json``)."""
    number = snapshot.number if snapshot_number is None else snapshot_number
    short = _validate(ref, number, session, snapshot)
    el = _find(snapshot, short)
    if el is None:  # _validate already raised, but keep the contract tight
        raise RefError(f"unknown ref '{ref}' in snapshot {number}", ref=ref)
    window = _window(snapshot)
    geometry = element_geometry(el, window=window, snapshot_number=snapshot.number)
    actions = list(el.actions)
    return {
        "ref": short,
        "full_ref": snapshot.full_ref(short),
        "role": el.role,
        "raw_role": el.raw_role,
        "name": el.name,
        "state": dict(el.state),
        "state_words": visible_state(el.state, include_offscreen=True),
        "actions": actions,
        "actions_raw": {a: raw_for_action(a, el) for a in actions},
        "hidden_actions": actions_of(el),
        "native_id": el.native_id,
        "value": el.value,
        "value_length": len(el.value) if isinstance(el.value, str) else None,
        "properties": dict(el.properties),
        "visible": el.visible,
        "parent": el.parent_ref,
        "children": [c.ref for c in el.children],
        "geometry": geometry,
        "snapshot": snapshot.number,
        "session": snapshot.session,
    }


#: Short alias for the dict form, for callers that only want the record.
inspect_element = inspect_to_dict


def format_inspect(snapshot: Snapshot, ref: str,
                   snapshot_number: Optional[int] = None,
                   session: str = "") -> str:
    """Human/model readable block for one element."""
    data = inspect_to_dict(snapshot, ref, snapshot_number, session)
    geo = data["geometry"]
    full = snapshot.full_ref(data["ref"])
    lines = [f"[{data['role']}] {data['name'] or '(unnamed)'} {data['ref']}  "
             f"{full}  (snapshot {data['snapshot']})"]
    if data["raw_role"] and data["raw_role"].lower() != data["role"].lower():
        lines.append(f"  raw role    {data['raw_role']}")
    if data["state_words"]:
        lines.append(f"  state       {', '.join(data['state_words'])}")
    if data["actions"]:
        lines.append(f"  actions     {', '.join(data['actions'])}")
    else:
        lines.append("  actions     none")
    if data["native_id"]:
        lines.append(f"  native id   {data['native_id']}")
    if data["value"] is not None:
        value = str(data["value"])
        preview = value if len(value) <= 120 else value[:117] + "..."
        preview = preview.replace("\n", "\\n")
        lines.append(f"  value       {preview}")
    geo_line = (
        f"  geometry    x={geo['x']} y={geo['y']} w={geo['width']} h={geo['height']} "
        f"center={geo['cx']},{geo['cy']} window={geo['rx']},{geo['ry']}"
        + (" offscreen" if geo["offscreen"] else "")
    )
    lines.append(geo_line)
    if data["properties"]:
        props = ", ".join(f"{k}={v}" for k, v in data["properties"].items()
                          if v not in (None, ""))
        if props:
            lines.append(f"  properties  {props}")
    if data["children"]:
        lines.append(f"  children    {', '.join(data['children'])}")
    return "\n".join(lines)


def read_value(snapshot: Snapshot, ref: str,
               snapshot_number: Optional[int] = None, session: str = "") -> str:
    """The full text/value of one element (``agent-desktop read``)."""
    number = snapshot.number if snapshot_number is None else snapshot_number
    short = _validate(ref, number, session, snapshot)
    el = _find(snapshot, short)
    if el is None:
        raise RefError(f"unknown ref '{ref}' in snapshot {number}", ref=ref)
    if el.value is not None:
        return str(el.value)
    text = el.properties.get("text")
    if text is not None:
        return str(text)
    raw = el.properties.get("chars") or el.properties.get("length")
    raise RefError(
        f"element {short} ({el.role} {el.name!r}) has no text"
        + (f" (it reports {raw} chars but no readable value)" if raw else "")
        + "; try `agent-desktop inspect " + short + "`",
        ref=ref,
    )


def bounds_of(snapshot: Snapshot, ref: str,
              snapshot_number: Optional[int] = None,
              session: str = "") -> dict[str, Any]:
    number = snapshot.number if snapshot_number is None else snapshot_number
    short = _validate(ref, number, session, snapshot)
    el = _find(snapshot, short)
    if el is None:
        raise RefError(f"unknown ref '{ref}' in snapshot {number}", ref=ref)
    geo = element_geometry(el, window=_window(snapshot), snapshot_number=snapshot.number)
    geo["ref"] = short
    geo["role"] = el.role
    geo["name"] = el.name
    return geo


def format_bounds_line(snapshot: Snapshot, ref: str,
                       snapshot_number: Optional[int] = None,
                       session: str = "") -> str:
    """``Close e10 @ 1842,12 46x32  center 1865,28``"""
    geo = bounds_of(snapshot, ref, snapshot_number, session)
    label = " ".join(part for part in (geo["name"], geo["ref"]) if part)
    text = f"{label} @ {geo['x']},{geo['y']} {geo['width']}x{geo['height']}"
    text += f"  center {geo['cx']},{geo['cy']}  win {geo['rx']},{geo['ry']}"
    if geo["offscreen"]:
        text += "  offscreen"
    return text


def find_elements(snapshot: Snapshot, text: str, role: str = "",
                  limit: int = 40) -> list[dict[str, Any]]:
    """Find refs by name (case-insensitive substring) and optional role."""
    needle = str(text or "").strip().lower()
    if not needle:
        raise RefError("find needs some text to look for (a name or part of one)",
                       exit_code=2)
    out: list[dict[str, Any]] = []
    elements = list(snapshot.walk())
    if snapshot.dialog is not None:
        elements += list(snapshot.dialog.walk())
    for el in elements:
        if role and el.role != role:
            continue
        name = (el.name or "").lower()
        value = (el.value or "").lower() if isinstance(el.value, str) else ""
        if needle in name or (not name and needle in value):
            out.append({
                "ref": el.ref,
                "role": el.role,
                "name": el.name,
                "state": visible_state(el.state, include_offscreen=True),
                "actions": list(el.actions),
            })
            if len(out) >= limit:
                break
    return out
