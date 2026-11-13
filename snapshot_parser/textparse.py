"""Parsers for the common text tools used as a Linux accessibility bridge.

kept separate from the backends so they can be unit-tested with canned output
from a real machine - which is how the X11 backend is validated in CI, where
there is no X server.

Three formats:

* ``xwininfo -tree -id <win>``      nested child windows + geometry
* ``xprop -id <win>``               window titles and classes
* ``xdotool getwindowgeometry``      position and size
* ``xwininfo -root -tree``          the window list, for ``windows``
"""

from __future__ import annotations

import re
from typing import Optional

from .geometry import Rect

__all__ = [
    "parse_xwininfo_tree",
    "parse_root_tree",
    "parse_xprop",
    "parse_xdotool_geometry",
    "parse_wmctrl_list",
    "decode_xprop_string",
    "MAX_NODES",
]

MAX_NODES = 4000

#: ``     0x3e00021 "Open": ("button" "Button")  32x24+4+2  +124+82``
_TREE_LINE = re.compile(
    r"^(?P<indent>\s+)(?P<id>0x[0-9a-fA-F]+)\s+"
    r'(?P<name>"[^"]*"|\(has no name\))\s*:?\s*'
    r"(?:\((?P<classes>[^)]*)\))?\s*"
    r"(?P<width>\d+)x(?P<height>\d+)\+(?P<rx>-?\d+)\+(?P<ry>-?\d+)\s+"
    r"\+(?P<ax>-?\d+)\+(?P<ay>-?\d+)\s*(?P<rest>.*)$"
)

_ROOT_LINE = re.compile(
    r"^\s+(?P<id>0x[0-9a-fA-F]+)\s+\"(?P<name>[^\"]*)\":\s+"
    r"\((?P<kind>[^)]*)\)\s+(?P<width>\d+)x(?P<height>\d+)\+(?P<x>-?\d+)\+(?P<y>-?\d+)"
)


def _clean_name(raw: str) -> str:
    """``(has no name)`` means no name; quotes are stripped."""
    text = str(raw or "").strip().strip('"').strip()
    if text.lower().startswith("(has no name"):
        return ""
    return text


def parse_xwininfo_tree(text: str) -> Optional[dict]:
    """``xwininfo -tree`` output -> nested dict of children."""
    root: dict = {"id": 0, "name": "", "width": 0, "height": 0, "rx": 0, "ry": 0,
                  "class": "", "children": [], "depth": -1}
    stack: list[dict] = [root]
    count = 0
    for line in str(text or "").splitlines():
        match = _TREE_LINE.match(line)
        if not match:
            continue
        count += 1
        if count > MAX_NODES:
            break
        depth = len(match.group("indent"))
        name = _clean_name(match.group("name"))
        rest = (match.group("rest") or "").strip()
        # the class hint is the parenthesised pair: ("button" "Button")
        classes = re.findall(r'"([^"]*)"', match.group("classes") or "")
        if not classes:
            classes = re.findall(r'"([^"]*)"', rest)
        node = {
            "id": int(match.group("id"), 16),
            "name": name,
            "width": int(match.group("width")),
            "height": int(match.group("height")),
            "rx": int(match.group("rx")),
            "ry": int(match.group("ry")),
            "class": classes[0] if classes else "",
            "children": [],
            "depth": depth,
        }
        while stack and stack[-1]["depth"] >= depth:
            stack.pop()
        stack[-1]["children"].append(node)
        stack.append(node)
    if not root["children"]:
        return None
    return root


def parse_root_tree(text: str) -> list[dict]:
    """``xwininfo -root -tree`` -> the list of top-level windows.

    Only the first level of the tree is taken: the root's direct children are
    the managed windows, and everything below them is app-internal.
    """
    out: list[dict] = []
    base_depth: Optional[int] = None
    for line in str(text or "").splitlines():
        match = _TREE_LINE.match(line)
        if not match:
            continue
        depth = len(match.group("indent"))
        if base_depth is None:
            base_depth = depth
        if depth != base_depth:
            continue
        name = _clean_name(match.group("name"))
        rest = (match.group("rest") or "").strip()
        classes = re.findall(r'"([^"]*)"', match.group("classes") or "")
        if not classes:
            classes = re.findall(r'"([^"]*)"', rest)
        out.append({
            "id": int(match.group("id"), 16),
            "name": name,
            "class": classes[0] if classes else "",
            "width": int(match.group("width")),
            "height": int(match.group("height")),
            "rx": int(match.group("rx")),
            "ry": int(match.group("ry")),
        })
    return out


def decode_xprop_string(value: str) -> str:
    """xprop prints ``_NET_WM_NAME(UTF8_STRING) = "Notepad"`` - take the words."""
    text = str(value or "").strip()
    if "=" in text:
        text = text.split("=", 1)[1].strip()
    strings = re.findall(r'"([^"]*)"', text)
    if strings:
        return strings[0]
    return text


def parse_xprop(text: str) -> dict[str, str]:
    """Parse ``xprop`` output into ``{property: value}`` for the properties we use."""
    wanted = {
        "WM_CLASS": "class",
        "_NET_WM_NAME": "title",
        "WM_NAME": "title",
        "_NET_WM_WINDOW_TYPE": "window_type",
        "_NET_WM_STATE": "state",
        "WM_TRANSIENT_FOR": "transient_for",
    }
    out: dict[str, str] = {}
    for line in str(text or "").splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = re.sub(r"\([^)]*\)\s*$", "", key.strip())   # WM_CLASS(STRING) -> WM_CLASS
        if key not in wanted:
            continue
        slot = wanted[key]
        if slot == "class":
            strings = re.findall(r'"([^"]*)"', value)
            out["class"] = strings[0] if strings else value.strip()
        elif slot == "title":
            out.setdefault("title", decode_xprop_string(value))
        else:
            out[slot] = value.strip()
    return out


def parse_xdotool_geometry(text: str) -> dict:
    """``xdotool getwindowgeometry`` -> ``{"x":.., "y":.., "width":.., "height":..}``."""
    out: dict[str, int] = {}
    for line in str(text or "").splitlines():
        line = line.strip()
        if line.startswith("Position:"):
            numbers = re.findall(r"-?\d+", line)
            if len(numbers) >= 2:
                out["x"], out["y"] = int(numbers[0]), int(numbers[1])
        elif line.startswith("Geometry:"):
            numbers = re.findall(r"\d+", line)
            if len(numbers) >= 2:
                out["width"], out["height"] = int(numbers[0]), int(numbers[1])
    return out


def parse_wmctrl_list(text: str) -> list[dict]:
    """``wmctrl -lp`` -> ``[{id, desktop, pid, host, title}]``."""
    out: list[dict] = []
    for line in str(text or "").splitlines():
        fields = line.split(None, 4)
        if len(fields) < 5:
            continue
        handle, desktop, pid, host, title = fields
        try:
            handle_int = int(handle, 16)
        except ValueError:
            continue
        out.append({
            "id": handle_int,
            "desktop": int(desktop) if desktop.lstrip("-").isdigit() else -1,
            "pid": int(pid) if pid.isdigit() else 0,
            "host": host,
            "title": title.strip(),
        })
    return out


def rect_from_node(node: dict, window: Rect) -> Rect:
    return Rect(window.x + node["rx"], window.y + node["ry"],
                node["width"], node["height"])
