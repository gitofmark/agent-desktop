"""Linux/X11 backend - the "do it on Linux too" path.

Two layers:

1. **Window geometry, always available**: X11 via :mod:`ctypes` against
   ``libX11``.  Any X server - including Xvfb and WSLg - gives window titles
   and exact pixel boxes, which is all ``windows`` and the screen coordinates
   in :class:`~snapshot_parser.geometry.Rect` need.

2. **The element tree, when X11 tooling is installed**: ``xwininfo -tree``
   lists the child windows with their names and class hints (``button``,
   ``text``, ``label`` ...), which for GTK, Qt, tk and most toolkit apps *is*
   the control tree.  ``xdotool`` provides input, window activation and
   geometry, ``wmctrl`` provides pids and titles.

If no tool is installed the backend still reports windows from Xlib and
renders a one-line snapshot of the window itself; ``agent-desktop doctor``
says what to install.  AT-SPI (``pyatspi``) is used instead when present,
because it gives real accessible names and roles.

All command output parsing lives in :mod:`snapshot_parser.textparse`, so the
whole backend can be unit tested with captured text on a machine with no X.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Optional

from ..geometry import Rect, is_offscreen
from ..model import Element, Snapshot
from ..roles import canonical_actions, normalize_role
from ..textparse import (
    MAX_NODES,
    parse_root_tree,
    parse_wmctrl_list,
    parse_xdotool_geometry,
    parse_xprop,
    parse_xwininfo_tree,
)
from .base import ActionResult, Backend, BackendUnavailable, WindowInfo

__all__ = ["LinuxX11Backend", "X11", "tool_report", "TOOLS", "x_tools_available"]

TOOLS = ("xwininfo", "xdotool", "wmctrl", "xprop")


class X11:
    """Minimal ctypes binding to the handful of Xlib calls we need."""

    def __init__(self, display_name: Optional[bytes] = None) -> None:
        library = ctypes.util.find_library("X11")
        if not library:
            raise BackendUnavailable("libX11 is not installed")
        self.lib = ctypes.CDLL(library)
        self.lib.XOpenDisplay.restype = ctypes.c_void_p
        self.lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self.display = self.lib.XOpenDisplay(display_name)
        if not self.display:
            raise BackendUnavailable(
                "cannot open the X display"
                + (f" '{display_name.decode()}'" if display_name else "")
                + "; is DISPLAY set? (a headless box can run `Xvfb :99 &`)"
            )

    # -- queries ------------------------------------------------------
    def root(self) -> int:
        self.lib.XDefaultRootWindow.restype = ctypes.c_ulong
        self.lib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        return int(self.lib.XDefaultRootWindow(self.display))

    def query_tree(self, window: int) -> list[int]:
        self.lib.XQueryTree.restype = ctypes.c_int
        self.lib.XQueryTree.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)),
            ctypes.POINTER(ctypes.c_uint),
        ]
        root_out = ctypes.c_ulong()
        parent_out = ctypes.c_ulong()
        children = ctypes.POINTER(ctypes.c_ulong)()
        count = ctypes.c_uint()
        status = self.lib.XQueryTree(
            self.display, ctypes.c_ulong(window), ctypes.byref(root_out),
            ctypes.byref(parent_out), ctypes.byref(children), ctypes.byref(count),
        )
        if not status:
            return []
        try:
            return [int(children[i]) for i in range(count.value)]
        finally:
            if children:
                self.lib.XFree(children)

    def window_name(self, window: int) -> str:
        self.lib.XFetchName.restype = ctypes.c_int
        self.lib.XFetchName.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_char_p)
        ]
        name = ctypes.c_char_p()
        if self.lib.XFetchName(self.display, ctypes.c_ulong(window),
                               ctypes.byref(name)) and name.value:
            return name.value.decode("utf-8", "replace")
        return ""

    def _attributes(self, window: int):
        class XWindowAttributes(ctypes.Structure):
            _fields_ = [
                ("x", ctypes.c_int), ("y", ctypes.c_int),
                ("width", ctypes.c_int), ("height", ctypes.c_int),
                ("border_width", ctypes.c_int), ("depth", ctypes.c_int),
                ("visual", ctypes.c_void_p), ("root", ctypes.c_ulong),
                ("c_class", ctypes.c_int), ("bit_gravity", ctypes.c_int),
                ("win_gravity", ctypes.c_int), ("backing_store", ctypes.c_int),
                ("backing_planes", ctypes.c_ulong), ("backing_pixel", ctypes.c_ulong),
                ("save_under", ctypes.c_int), ("colormap", ctypes.c_ulong),
                ("map_installed", ctypes.c_int), ("map_state", ctypes.c_int),
                ("all_event_masks", ctypes.c_long), ("your_event_mask", ctypes.c_long),
                ("do_not_propagate_mask", ctypes.c_long),
                ("override_redirect", ctypes.c_int), ("screen", ctypes.c_void_p),
            ]

        self.lib.XGetWindowAttributes.restype = ctypes.c_int
        self.lib.XGetWindowAttributes.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(XWindowAttributes)
        ]
        attrs = XWindowAttributes()
        if not self.lib.XGetWindowAttributes(self.display, ctypes.c_ulong(window),
                                             ctypes.byref(attrs)):
            return None
        return attrs

    def translate(self, window: int) -> tuple[int, int]:
        """Root-relative position of a window (the space the mouse clicks in)."""
        self.lib.XTranslateCoordinates.restype = ctypes.c_int
        self.lib.XTranslateCoordinates.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_int,
            ctypes.c_int, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_ulong),
        ]
        x_out, y_out = ctypes.c_int(), ctypes.c_int()
        child = ctypes.c_ulong()
        ok = self.lib.XTranslateCoordinates(
            self.display, ctypes.c_ulong(window), ctypes.c_ulong(self.root()),
            0, 0, ctypes.byref(x_out), ctypes.byref(y_out), ctypes.byref(child),
        )
        return (int(x_out.value), int(y_out.value)) if ok else (0, 0)

    def rect(self, window: int) -> Rect:
        attrs = self._attributes(window)
        if attrs is None:
            return Rect()
        x, y = self.translate(window)
        return Rect(x, y, attrs.width, attrs.height)

    def mapped(self, window: int) -> bool:
        attrs = self._attributes(window)
        return bool(attrs and attrs.map_state == 2)

    def screen_size(self) -> tuple[int, int]:
        self.lib.XDisplayWidth.restype = ctypes.c_int
        self.lib.XDisplayWidth.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.lib.XDisplayHeight.restype = ctypes.c_int
        self.lib.XDisplayHeight.argtypes = [ctypes.c_void_p, ctypes.c_int]
        return (int(self.lib.XDisplayWidth(self.display, 0)),
                int(self.lib.XDisplayHeight(self.display, 0)))

    def close(self) -> None:
        try:
            self.lib.XCloseDisplay(self.display)
        except Exception:
            pass


def x_tools_available() -> dict[str, Optional[str]]:
    return {tool: shutil.which(tool) for tool in TOOLS}


def tool_report() -> str:
    tools = x_tools_available()
    missing = [name for name, path in tools.items() if not path]
    if not missing:
        return "x11 tooling: all present"
    return ("x11 tooling missing: " + ", ".join(missing)
            + " (sudo apt install x11-utils xdotool wmctrl)")


def run_tool(cmd: list[str], timeout: float = 10.0) -> str:
    """Run an X11 tool; raise a readable BackendUnavailable on any failure."""
    if not shutil.which(cmd[0]):
        raise BackendUnavailable(
            f"{cmd[0]} is not installed; install it (`sudo apt install "
            f"{'x11-utils' if cmd[0] == 'xwininfo' else cmd[0]}`), or use "
            "--fixture / --dry-run"
        )
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise BackendUnavailable(
            f"{cmd[0]} did not answer within {timeout:g}s; the X server may be hung"
        ) from None
    except OSError as exc:
        raise BackendUnavailable(f"cannot run {cmd[0]}: {exc.strerror or exc}") from None
    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip().splitlines()
        detail = message[0] if message else f"exit code {result.returncode}"
        raise BackendUnavailable(f"{cmd[0]} failed: {detail}"[:200])
    return result.stdout


class LinuxX11Backend(Backend):
    name = "linux-x11"
    interactive = True

    def __init__(self, app: str = "") -> None:
        #: optional app/title filter, used by the second-app smoke test
        self.app = app
        self._x11: Optional[X11] = None
        self._screen: Rect = Rect()
        self.window: Optional[int] = None
        self.elements: dict[str, int] = {}
        self._last_title = ""

    # -- availability -------------------------------------------------
    def available(self) -> tuple[bool, str]:
        if not sys.platform.startswith("linux"):
            return False, "the X11 backend only runs on Linux"
        if not os.environ.get("DISPLAY"):
            return False, (
                "no DISPLAY is set, so there is no desktop to read; start one "
                "(Xvfb :99 & export DISPLAY=:99) or use --fixture"
            )
        try:
            self._connect()
        except BackendUnavailable as exc:
            return False, str(exc)
        return True, ""

    def _connect(self) -> X11:
        if self._x11 is None:
            self._x11 = X11()
            width, height = self._x11.screen_size()
            self._screen = Rect(0, 0, width, height)
        return self._x11

    def require(self) -> None:
        self._connect()

    # -- windows ------------------------------------------------------
    def windows(self) -> list[WindowInfo]:
        x11 = self._connect()
        if shutil.which("wmctrl"):
            listed = self._windows_via_wmctrl(x11)
            if listed:
                return listed
        return self._windows_via_xlib(x11)

    def _windows_via_wmctrl(self, x11: X11) -> list[WindowInfo]:
        try:
            rows = parse_wmctrl_list(run_tool(["wmctrl", "-lp"]))
        except BackendUnavailable:
            return []
        out: list[WindowInfo] = []
        rows = [row for row in rows if self._wanted(row["title"])]
        for index, row in enumerate(rows, start=1):
            rect = x11.rect(row["id"])
            if rect.empty:
                continue
            info = parse_xprop(self._xprop(row["id"]))
            out.append(WindowInfo(
                number=index,
                app=info.get("class") or row["title"].split(" ")[0] or "x11",
                title=row["title"] or info.get("title", ""),
                rect=rect,
                handle=hex(row["id"]),
                pid=row["pid"],
                foreground=(index == 1),
            ))
        return out

    def _windows_via_xlib(self, x11: X11) -> list[WindowInfo]:
        """Fall back to the root window's children; no wmctrl needed."""
        try:
            nodes = parse_root_tree(run_tool(["xwininfo", "-root", "-tree"]))
        except BackendUnavailable:
            nodes = []
        out: list[WindowInfo] = []
        candidates = [node["id"] for node in nodes] or x11.query_tree(x11.root())
        if self.app:
            wanted = self.app.lower()
            candidates = [handle for handle in candidates
                          if wanted in (x11.window_name(handle) or "").lower()]
        index = 0
        for handle in candidates:
            if not x11.mapped(handle):
                continue
            rect = x11.rect(handle)
            if rect.empty or rect.width < 40 or rect.height < 40:
                continue
            title = x11.window_name(handle)
            info = parse_xprop(self._xprop(handle)) if title else {}
            title = title or info.get("title", "")
            index += 1
            out.append(WindowInfo(
                number=index, app=info.get("class") or "x11", title=title,
                rect=rect, handle=hex(handle), pid=0, foreground=(index == 1),
            ))
        return out

    def _wanted(self, title: str) -> bool:
        if not self.app:
            return True
        return self.app.lower() in (title or "").lower()

    def _xprop(self, handle: int) -> str:
        if not shutil.which("xprop"):
            return ""
        try:
            return run_tool(["xprop", "-id", hex(handle)])
        except BackendUnavailable:
            return ""

    def _pick(self, window: Any) -> int:
        x11 = self._connect()
        if window is None:
            windows = self.windows()
            if not windows:
                raise BackendUnavailable("no windows are open")
            return int(windows[0].handle, 16)
        if isinstance(window, int):
            return window
        text = str(window).strip()
        if text.lower().startswith("0x"):
            try:
                return int(text, 16)
            except ValueError:
                pass
        for info in self.windows():
            if text.lower() in (info.title or "").lower():
                return int(info.handle, 16)
            if text.lower() == (info.app or "").lower():
                return int(info.handle, 16)
        found = self._search(text)
        if found:
            return found
        names = ", ".join(f'"{w.title or w.app}"' for w in self.windows()[:6]) or "none"
        raise BackendUnavailable(f"no window matching '{text}'; open windows: {names}")

    @staticmethod
    def _search(text: str) -> int:
        if not shutil.which("xdotool"):
            return 0
        try:
            result = subprocess.run(["xdotool", "search", "--name", text],
                                    capture_output=True, text=True, timeout=10)
        except (subprocess.TimeoutExpired, OSError):
            return 0
        for line in result.stdout.split():
            if line.isdigit():
                return int(line)
        return 0

    # -- snapshot -----------------------------------------------------
    def snapshot(self, window: Any = None, *, session: str = "local",
                 number: int = 1) -> Snapshot:
        x11 = self._connect()
        snap = Snapshot(session=session, number=number, backend=self.name)
        snap.bounds_snapshot = number
        try:
            handle = self._pick(window)
            self.window = handle
            info = parse_xprop(self._xprop(handle))
            snap.title = x11.window_name(handle) or info.get("title", "")
            snap.app = info.get("class") or "x11"
            self._last_title = snap.title
            window_rect = x11.rect(handle)
            root = Element(ref="e1", role="window", name=snap.title or snap.app,
                           actions=["focus"], rect=window_rect, raw_role="Window",
                           native_id=hex(handle))
            self.elements = {"e1": handle}
            if "maximized" in (info.get("state", "") or "").lower() or \
                    "_NET_WM_STATE_MAXIMIZED" in (info.get("state", "") or ""):
                root.state = {"maximized": True}
            tree = self._element_tree(handle)
            if tree is not None:
                for child in tree:
                    root.add(child)
            else:
                root.properties["note"] = (
                    "no element tree on this desktop: install x11-utils "
                    "(xwininfo) or pyatspi for element-level detail"
                )
            snap.root = root
            snap.dialog = self._detect_dialog(handle, root, x11)
        except BackendUnavailable:
            raise
        except Exception as exc:  # readable error line, never a traceback
            snap.error = f"X11 error: {exc}"[:300]
        return snap

    def _element_tree(self, handle: int) -> Optional[list[Element]]:
        """Child windows from xwininfo, as an Element list (or None)."""
        try:
            text = run_tool(["xwininfo", "-tree", "-id", hex(handle)])
        except BackendUnavailable:
            return None
        parsed = parse_xwininfo_tree(text)
        if parsed is None:
            return None
        window_rect = self._connect().rect(handle)
        return self._build(parsed, window_rect)

    def _build(self, tree: Optional[dict], window_rect: Rect) -> list[Element]:
        """xwininfo nodes -> Elements, keeping the hierarchy, refs in pre-order."""
        if not tree:
            return []
        counter = {"n": 1}
        roots: list[Element] = []
        stack: list[tuple[int, Element]] = []

        def flatten(node: dict, depth: int):
            for child in node.get("children", []):
                yield child, depth
                yield from flatten(child, depth + 1)

        for node, depth in flatten(tree, 0):
            counter["n"] += 1
            if counter["n"] > MAX_NODES:
                break
            ref = f"e{counter['n']}"
            rect = Rect(window_rect.x + node["rx"], window_rect.y + node["ry"],
                        node["width"], node["height"])
            role = normalize_role(node.get("class") or "") 
            if role == "unknown" and node.get("class"):
                role = normalize_role(node["class"].lower())
            name = node.get("name", "")
            actions = canonical_actions(["SetFocus"] if role in
                                        ("button", "edit", "menuitem", "listitem") else [])
            element = Element(
                ref=ref, role=role, name=name, actions=actions, rect=rect,
                visible=not is_offscreen(rect, window=window_rect),
                raw_role=node.get("class", ""), native_id=hex(node["id"]),
            )
            self.elements[ref] = node["id"]
            while stack and stack[-1][0] >= depth:
                stack.pop()
            if stack:
                stack[-1][1].add(element)
            else:
                roots.append(element)
            stack.append((depth, element))
        return roots

    def _detect_dialog(self, handle: int, root: Element,
                       x11: X11) -> Optional[Element]:
        """A transient/modal window for this one is promoted to the R9 block."""
        if not shutil.which("wmctrl"):
            return None
        try:
            rows = parse_wmctrl_list(run_tool(["wmctrl", "-l"]))
        except BackendUnavailable:
            return None
        pid = 0
        for row in rows:
            if row["id"] == handle:
                pid = row["pid"]
                break
        for row in rows:
            if row["id"] == handle or (pid and row["pid"] != pid):
                continue
            info = parse_xprop(self._xprop(row["id"]))
            transient = info.get("transient_for", "")
            is_dialog = (
                "dialog" in info.get("window_type", "").lower()
                or "modal" in info.get("state", "").lower()
                or (transient and hex(handle) in transient)
            )
            if not is_dialog:
                continue
            rect = x11.rect(row["id"])
            dialog = Element(ref="eD", role="dialog", name=row["title"],
                             rect=rect, state={"modal": True},
                             native_id=hex(row["id"]))
            self.elements["eD"] = row["id"]
            children = self._build_box(row["id"], rect)
            for child in children:
                dialog.add(child)
            return dialog
        return None

    def _build_box(self, handle: int, rect: Rect) -> list[Element]:
        try:
            text = run_tool(["xwininfo", "-tree", "-id", hex(handle)])
        except BackendUnavailable:
            return []
        parsed = parse_xwininfo_tree(text)
        if parsed is None:
            return []
        return self._build(parsed, rect)

    # -- actions ------------------------------------------------------
    def perform(self, element: Element, action: str, snapshot: Snapshot) -> ActionResult:
        handle = self.elements.get(element.ref)
        if handle is None:
            return ActionResult(False, action, element.ref, "",
                                f"{element.ref} is not from the current X11 snapshot; "
                                "run `agent-desktop snapshot` again")
        x, y = element.rect.center
        try:
            if action in ("click", "double click", "right click"):
                button = "3" if action == "right click" else "1"
                repeat = ["--repeat", "2"] if action == "double click" else []
                if action == "click" and element.role in ("button", "menuitem", "link"):
                    try:
                        run_tool(["xdotool", "windowactivate", "--sync", str(handle)],
                                 timeout=3)
                        return ActionResult(True, action, element.ref,
                                            "xdotool-windowactivate")
                    except BackendUnavailable:
                        pass
                run_tool(["xdotool", "mousemove", str(x), str(y), "click"]
                         + repeat + [button])
                return ActionResult(True, action, element.ref, "xdotool-click")
            if action == "focus":
                run_tool(["xdotool", "windowfocus", str(handle)])
                return ActionResult(True, action, element.ref, "xdotool")
            if action == "type":
                run_tool(["xdotool", "windowfocus", str(handle)])
                run_tool(["xdotool", "type", "--delay", "10",
                          str(element.value or "")])
                return ActionResult(True, action, element.ref, "xdotool-type")
            if action == "set value":
                run_tool(["xdotool", "windowfocus", str(handle)])
                run_tool(["xdotool", "key", "ctrl+a"])
                run_tool(["xdotool", "type", "--delay", "10",
                          str(element.value or "")])
                return ActionResult(True, action, element.ref, "xdotool-type")
            if action == "scroll":
                amount = int(element.properties.get("scroll_amount", 3) or 3)
                direction = element.properties.get("scroll_direction", "down")
                run_tool(["xdotool", "mousemove", str(x), str(y)])
                run_tool(["xdotool", "click", "--repeat", str(amount),
                          "4" if direction == "up" else "5"])
                return ActionResult(True, action, element.ref, "xdotool-scroll")
            if action in ("expand", "collapse"):
                run_tool(["xdotool", "mousemove", str(x), str(y), "click", "1"])
                return ActionResult(True, action, element.ref, "xdotool-click",
                                    f"X11 has no expand pattern; clicked {element.ref}")
        except BackendUnavailable as exc:
            return ActionResult(False, action, element.ref, "xdotool", str(exc))
        return ActionResult(False, action, element.ref, "",
                            f"the X11 backend cannot {action} {element.ref}")

    def click_point(self, x: int, y: int, *, button: str = "left",
                    double: bool = False) -> None:
        code = {"left": "1", "right": "3", "middle": "2"}.get(button, "1")
        repeat = ["--repeat", "2"] if double else []
        run_tool(["xdotool", "mousemove", str(x), str(y), "click"] + repeat + [code])

    def type_text(self, element: Optional[Element], text: str) -> None:
        run_tool(["xdotool", "type", "--delay", "10", text])

    def send_keys(self, combo: str) -> None:
        run_tool(["xdotool", "key", combo_to_xdotool(combo)])

    def move_window(self, window: Any, x: int, y: int) -> None:
        handle = self._pick(window)
        run_tool(["xdotool", "windowmove", str(handle), str(x), str(y)])

    def resize_window(self, window: Any, width: int, height: int) -> None:
        handle = self._pick(window)
        run_tool(["xdotool", "windowsize", str(handle), str(width), str(height)])

    def close(self) -> None:
        if self._x11 is not None:
            self._x11.close()
            self._x11 = None


def combo_to_xdotool(combo: str) -> str:
    """``ctrl+shift+s`` -> ``ctrl+shift+s`` (xdotool's own syntax), renamed mods."""
    mapping = {"control": "ctrl", "cmd": "super", "meta": "super",
               "win": "super", "option": "alt", "return": "Return",
               "enter": "Return", "esc": "Escape", "escape": "Escape"}
    parts = [part.strip() for part in str(combo or "").split("+") if part.strip()]
    out = []
    for part in parts:
        lowered = part.lower()
        out.append(mapping.get(lowered, part))
    return "+".join(out)


def wait_for(predicate, timeout: float = 5.0, interval: float = 0.1):
    """Poll ``predicate`` until it returns something truthy (never sleep blindly)."""
    deadline = time.time() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if time.time() >= deadline:
            return None
        time.sleep(interval)
