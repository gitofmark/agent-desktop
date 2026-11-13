"""Phase F analogue for Linux: the X11 backend, without an X server.

The parsers are exercised against captured tool output (``tests/samples/``),
so a machine with no display - including this CI sandbox - still validates the
whole Linux path except the final click.
"""

from __future__ import annotations

import os

import pytest

from snapshot_parser.backends.linux_x11 import (
    LinuxX11Backend,
    combo_to_xdotool,
    tool_report,
    wait_for,
)
from snapshot_parser.model import Rect
from snapshot_parser.render import render_snapshot
from snapshot_parser.textparse import (
    parse_root_tree,
    parse_wmctrl_list,
    parse_xdotool_geometry,
    parse_xprop,
    parse_xwininfo_tree,
)

SAMPLES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "samples")


def sample(name: str) -> str:
    with open(os.path.join(SAMPLES, name), "r", encoding="utf-8") as handle:
        return handle.read()


# -- xwininfo -tree -----------------------------------------------------
def test_parse_xwininfo_tree_keeps_the_hierarchy():
    node = parse_xwininfo_tree(sample("xwininfo_tree.txt"))
    assert node is not None
    assert len(node["children"]) == 4
    title_bar = node["children"][1]
    assert title_bar["children"][0]["children"][2]["name"] == "Close"
    close = title_bar["children"][0]["children"][2]
    assert close["class"] == "button"
    assert (close["width"], close["height"]) == (32, 24)
    assert (close["rx"], close["ry"]) == (76, 2)


def test_parse_xwininfo_tree_of_junk_is_none():
    assert parse_xwininfo_tree("") is None
    assert parse_xwininfo_tree("xwininfo: error: no such window") is None


def test_parse_root_tree_takes_only_the_top_level():
    nodes = parse_root_tree(sample("xwininfo_root.txt"))
    assert {node["name"] for node in nodes} == {"Terminal", "gedit"}
    assert nodes[1]["width"] == 800


# -- xprop / xdotool / wmctrl ------------------------------------------
def test_parse_xprop():
    text = (
        'WM_CLASS(STRING) = "gedit", "Gedit"\n'
        '_NET_WM_NAME(UTF8_STRING) = "snap.js - gedit"\n'
        '_NET_WM_WINDOW_TYPE(ATOM) = _NET_WM_WINDOW_TYPE_NORMAL\n'
        '_NET_WM_STATE(ATOM) = _NET_WM_STATE_MAXIMIZED_VERT, _NET_WM_STATE_MAXIMIZED_HORZ\n'
    )
    info = parse_xprop(text)
    assert info["class"] == "gedit"
    assert info["title"] == "snap.js - gedit"
    assert "NORMAL" in info["window_type"]
    assert "MAXIMIZED" in info["state"]


def test_parse_xdotool_geometry():
    text = ("Window 65011719\n  Position: 120,80 (screen: 0)\n"
            "  Geometry: 800x600\n")
    assert parse_xdotool_geometry(text) == {"x": 120, "y": 80,
                                            "width": 800, "height": 600}


def test_parse_wmctrl_list():
    text = ("0x03e00007  0 4242   host  snap.js - gedit\n"
            "0x04000001  1 4243   host  Terminal\n")
    rows = parse_wmctrl_list(text)
    assert rows[0]["id"] == 0x3E00007
    assert rows[0]["pid"] == 4242
    assert rows[1]["title"] == "Terminal"


# -- the backend --------------------------------------------------------
def test_backend_is_unavailable_without_a_display(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    backend = LinuxX11Backend()
    if os.uname().sysname != "Linux":  # pragma: no cover
        pytest.skip("linux only")
    ok, why = backend.available()
    assert not ok
    assert "DISPLAY" in why


def test_tool_report_mentions_missing_tools_or_all_present():
    report = tool_report()
    assert report


def test_combo_translation():
    assert combo_to_xdotool("ctrl+shift+s") == "ctrl+shift+s"
    assert combo_to_xdotool("control+Return") == "ctrl+Return"
    assert combo_to_xdotool("win+e") == "super+e"
    assert combo_to_xdotool("alt+F4") == "alt+F4"


def test_wait_for_polls_a_condition():
    calls = {"n": 0}

    def predicate():
        calls["n"] += 1
        return calls["n"] >= 3

    assert wait_for(predicate, timeout=2, interval=0.01) is True
    assert not wait_for(lambda: False, timeout=0.05, interval=0.01)


def test_building_elements_from_captured_xwininfo():
    """The tree the backend would produce for the captured gedit window."""
    backend = LinuxX11Backend()
    parsed = parse_xwininfo_tree(sample("xwininfo_tree.txt"))
    window_rect = Rect(120, 80, 800, 600)
    elements = backend._build(parsed, window_rect)
    assert len(elements) == 4
    refs = [el.ref for el in elements]
    assert refs[0] == "e2" and len(set(refs)) == 4

    def find_named(name):
        for root in elements:
            for el in root.walk():
                if el.name == name:
                    return el
        return None

    close = find_named("Close")
    assert close is not None
    assert close.role == "button"
    # screen coordinates: window origin + window-relative offset
    assert close.rect == Rect(120 + 76, 80 + 2, 32, 24)
    assert backend.elements[close.ref] == 0x3E00023
    assert find_named("Text Editor").role == "text"


def test_rendering_a_captured_x11_tree():
    from snapshot_parser.model import Snapshot

    backend = LinuxX11Backend()
    parsed = parse_xwininfo_tree(sample("xwininfo_tree.txt"))
    window_rect = Rect(120, 80, 800, 600)
    from snapshot_parser.model import Element

    root = Element(ref="e1", role="window", name="snap.js - gedit",
                   rect=window_rect)
    for child in backend._build(parsed, window_rect):
        root.add(child)
    snap = Snapshot(app="gedit", title="snap.js - gedit", number=1,
                    session="x11sess", root=root)
    text = render_snapshot(snap)
    assert text.splitlines()[0] == 'gedit — "snap.js - gedit"  (snapshot 1, session x11sess)'
    assert "Close e" in text
    assert "Open e" in text
    assert "Text Editor" in text
    assert "(has no name)" not in text


def test_snapshot_without_display_is_a_readable_error(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":99")
    backend = LinuxX11Backend()
    ok, why = backend.available()
    # either the display really exists (unlikely in CI) or the reason is readable
    assert ok or "display" in why.lower() or "libX11" in why


def test_actions_need_a_live_tree():
    from snapshot_parser.model import Element, Snapshot

    backend = LinuxX11Backend()
    result = backend.perform(Element(ref="e9", role="button", name="Close"),
                             "click", Snapshot())
    assert not result.ok
    assert "not from the current" in result.message
