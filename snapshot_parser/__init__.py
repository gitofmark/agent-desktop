"""ECHO minimal natural snapshot parser.

Turns a raw accessibility tree (Windows UIA, Linux X11, or a captured fixture)
into a short, stable, ref-preserving text snapshot an agent can read and act
on::

    Notepad — "snap.js - Notepad"  (snapshot 14, session syzqsppsg5kob)

    [edit] Text Editor e1 — focused, 2340 chars, starts "const snap = ..."
    [scrollbar] Vertical e2 — line up e3, page up e4, line down e5
    [button] Minimize e8 · Maximize e9 · Close e10

Public API::

    from snapshot_parser import read_snapshot, render_snapshot, inspect_to_dict

    snap = read_snapshot("tests/fixtures/notepad.raw.txt")
    print(render_snapshot(snap))
    print(inspect_to_dict(snap, "e10"))
"""

from __future__ import annotations

from . import budget, diff, geometry, inspect, model, reader, refs, render, roles, state
from .budget import DEFAULT_MAX_LINES, cap_lines, is_renderable
from .diff import Change, diff_snapshots, format_diff, verify_diff
from .geometry import DpiInfo, Rect, element_geometry, format_bounds, is_offscreen
from .inspect import (
    bounds_of,
    find_elements,
    format_bounds_line,
    format_inspect,
    inspect_to_dict,
    read_value,
)
from .model import Element, Snapshot
from .reader import ReaderError, load_snapshot, parse_raw_text, read_snapshot
from .refs import RefError, full_ref, parse_ref, short_ref, validate_ref
from .render import MAX_PREVIEW, RenderOptions, render_snapshot
from .roles import actions_of, normalize_action, normalize_role

__version__ = "1.0.0"

__all__ = [
    "__version__",
    # model
    "Element",
    "Snapshot",
    "Rect",
    # reading
    "read_snapshot",
    "load_snapshot",
    "parse_raw_text",
    "ReaderError",
    # rendering
    "render_snapshot",
    "render",
    "RenderOptions",
    "MAX_PREVIEW",
    "DEFAULT_MAX_LINES",
    "cap_lines",
    "is_renderable",
    # refs
    "RefError",
    "parse_ref",
    "short_ref",
    "full_ref",
    "validate_ref",
    # inspect
    "inspect_to_dict",
    "format_inspect",
    "format_bounds_line",
    "bounds_of",
    "read_value",
    "find_elements",
    # geometry
    "element_geometry",
    "format_bounds",
    "is_offscreen",
    "DpiInfo",
    # diff
    "diff_snapshots",
    "format_diff",
    "verify_diff",
    "Change",
    # roles / state
    "normalize_role",
    "normalize_action",
    "actions_of",
    # modules
    "model",
    "reader",
    "roles",
    "state",
    "render",
    "refs",
    "budget",
    "geometry",
    "inspect",
    "diff",
]

# ``inspect_element`` is just the dict form under a shorter name for callers
# that want the raw record rather than the formatted block.
inspect_element = inspect_to_dict
