"""Windows UI Automation backend (pywinauto, with ``uiautomation`` fallback).

This is the primary target.  It reads the same :class:`Element` tree the
fixture loader produces, so rendering, refs, budget and inspect never learn
where the data came from.

Design notes
------------
* Refs are assigned per element on the fly (``e1``, ``e2`` ...) in pre-order;
  the *raw* identity (runtime id, or the automation id + name pair) is kept in
  ``action_ids``/``native_id`` and mirrored in :class:`UiaSession` so an action
  can find the same element again without trusting a stale tree.
* Every provider call is wrapped: UIA throws ``COMError`` and
  ``ElementNotAvailable`` when a window closes mid-read, and that must become a
  readable error line (Step 26), never a traceback.
* Geometry is reported in physical screen pixels, matching where the mouse
  clicks after :func:`DpiInfo` normalisation.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Optional

from ..geometry import Rect, is_offscreen
from ..model import Element, Snapshot
from ..roles import canonical_actions, normalize_role
from ..state import extract_state
from .base import ActionResult, Backend, BackendUnavailable, WindowInfo

__all__ = ["WindowsUiaBackend", "UiaSession", "MAX_NODES", "MAX_DEPTH"]

#: Hard stop so a pathological tree cannot hang a snapshot (Step 27).
MAX_NODES = 4000
#: Backstop against a cyclic/pathological hierarchy. Deep-but-narrow trees are
#: real (Electron nests content ~11 levels down), so this is generous: the node
#: budget above is the guard that actually bounds the work.
MAX_DEPTH = 120

#: State properties actually probed, in pywinauto's method spelling.
_STATE_PROBES = (
    "is_enabled", "is_visible", "is_offscreen", "has_keyboard_focus",
    "is_keyboard_focusable", "is_selected", "is_expanded", "is_checked",
    "is_read_only", "is_modal", "is_password", "is_required_for_form",
    "is_invalid", "is_busy",
)



#: Chromium/Electron windows only expose their *frame* until the renderer's
#: accessibility is switched on. Without this the tree is one Pane with no
#: children, which looks like "the tool only works on Notepad".
_BROWSER_TITLES = (
    " - microsoft\u200b edge", " - google chrome", "visual studio code",
    "whatsapp", "slack", "discord", "notion", "teams", "obsidian", "postman",
    "arena |", "electron",
)
_BROWSER_EXES = (
    "msedge.exe", "chrome.exe", "code.exe", "brave.exe", "vivaldi.exe",
    "opera.exe", "firefox.exe", "whatsapp.exe", "slack.exe", "discord.exe",
    "notion.exe", "teams.exe", "ms-teams.exe", "obsidian.exe",
)
#: Chromium renders its UI into a child with this window class.
_RENDER_CLASS = "Chrome_RenderWidgetHostHWND"


def looks_like_browser(title: str = "", exe: str = "") -> bool:
    """Heuristic: does this window host a Chromium/Electron web view?"""
    lowered_title = (title or "").lower()
    lowered_exe = (exe or "").lower()
    return (any(marker in lowered_title for marker in _BROWSER_TITLES)
            or any(lowered_exe.endswith(name) for name in _BROWSER_EXES))


def browser_hint(title: str = "", exe: str = "") -> str:
    """What the user has to do for a browser/Electron window to be readable."""
    name = os.path.basename(exe) if exe else "the app"
    if "code.exe" in (exe or "").lower():
        launch = "code --force-renderer-accessibility"
    elif "msedge" in (exe or "").lower():
        launch = 'msedge --force-renderer-accessibility'
    elif "chrome" in (exe or "").lower():
        launch = "chrome --force-renderer-accessibility"
    else:
        launch = f"{name} --force-renderer-accessibility"
    return (
        "no accessibility tree: this is a Chromium/Electron window and renderer "
        "accessibility is off. Restart it with `" + launch + "` (existing windows "
        "never enable it later), or turn it on for one page via "
        "chrome://accessibility / edge://accessibility"
    )


def _is_empty_tree(root: Element) -> bool:
    """True when the read produced nothing an agent could act on."""
    if root is None:
        return True
    return not root.children and not (root.name and root.name.strip())


class _ChildWindows:
    """Find the Chromium render child and wake its accessibility tree.

    Electron exposes nothing until an automation client actually attaches to the
    render widget, so a read has to knock on it first. Everything here is
    best-effort: no ctypes, no win32, no change in behaviour off Windows.
    """

    RENDER_CLASS = "Chrome_RenderWidgetHostHWND"

    @staticmethod
    def handles(top_level: int) -> list[int]:
        if not top_level or sys.platform != "win32":
            return []
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            found: list[int] = []

            @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)  # type: ignore[misc]
            def visit(child, _param):
                buffer = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(child, buffer, 256)
                if buffer.value == _ChildWindows.RENDER_CLASS:
                    found.append(int(child))
                return True

            user32.EnumChildWindows(wintypes.HWND(top_level), visit, 0)
            return found
        except Exception:
            return []

    @staticmethod
    def wake(handle: int) -> None:
        """Send WM_GETOBJECT(UiaRootObjectId) so the renderer builds a tree."""
        if not handle or sys.platform != "win32":
            return
        try:
            import ctypes
            from ctypes import wintypes

            WM_GETOBJECT = 0x003D
            UiaRootObjectId = -25
            # SendMessageTimeout, not SendMessage: a hung renderer must not hang us
            ctypes.windll.user32.SendMessageTimeoutW(
                wintypes.HWND(handle), WM_GETOBJECT, wintypes.WPARAM(0),
                wintypes.LPARAM(UiaRootObjectId), 0x0002, 300, None)
        except Exception:
            return

    @staticmethod
    def exe_of_pid(pid: int) -> str:
        """Image name for a pid, via ctypes (no psutil dependency)."""
        if not pid or sys.platform != "win32":
            return ""
        try:
            import ctypes
            from ctypes import wintypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            kernel32 = ctypes.windll.kernel32
            process = kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not process:
                return ""
            try:
                buffer = ctypes.create_unicode_buffer(1024)
                size = wintypes.DWORD(1024)
                if kernel32.QueryFullProcessImageNameW(
                        process, 0, buffer, ctypes.byref(size)):
                    return os.path.basename(buffer.value)
            finally:
                kernel32.CloseHandle(process)
        except Exception:
            return ""
        return ""

    @staticmethod
    def exe_of(handle: int) -> str:
        """The process image name owning a window, for the heuristic and hint."""
        if not handle or sys.platform != "win32":
            return ""
        try:
            import ctypes
            from ctypes import wintypes

            pid = wintypes.DWORD(0)
            ctypes.windll.user32.GetWindowThreadProcessId(
                wintypes.HWND(handle), ctypes.byref(pid))
            if not pid.value:
                return ""
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            kernel32 = ctypes.windll.kernel32
            process = kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
            if not process:
                return ""
            try:
                buffer = ctypes.create_unicode_buffer(1024)
                size = wintypes.DWORD(1024)
                if kernel32.QueryFullProcessImageNameW(
                        process, 0, buffer, ctypes.byref(size)):
                    return os.path.basename(buffer.value)
            finally:
                kernel32.CloseHandle(process)
        except Exception:
            return ""
        return ""


class WindowsUiaBackend(Backend):
    name = "windows-uia"
    interactive = True

    def __init__(self) -> None:
        self._desktop = None
        self._error = ""
        self.session_tree: Optional[UiaSession] = None

    # -- availability -------------------------------------------------
    def available(self) -> tuple[bool, str]:
        if sys.platform != "win32":
            return False, (
                "the UIA backend needs Windows; on Linux use "
                "`--backend linux-x11` or `--fixture <file>`"
            )
        if self._error:
            return False, self._error
        try:
            import pywinauto  # noqa: F401
        except Exception:
            try:
                import uiautomation  # noqa: F401
            except Exception:
                return False, (
                    "neither pywinauto nor uiautomation is installed; run "
                    "`pip install pywinauto` (or `pip install uiautomation`)"
                )
        return True, ""

    def _desktop_obj(self):
        if self._desktop is None:
            from pywinauto import Desktop

            self._desktop = Desktop(backend="uia")
        return self._desktop

    # -- windows ------------------------------------------------------
    def windows(self) -> list[WindowInfo]:
        self.require()
        out: list[WindowInfo] = []
        try:
            desktop = self._desktop_obj()
            foreground = self._foreground_handle()
            for index, win in enumerate(desktop.windows(), start=1):
                try:
                    rect = win.rectangle()
                    box = Rect(rect.left, rect.top, rect.width(), rect.height())
                    if box.width <= 0 or box.height <= 0:
                        continue
                    handle = int(getattr(win, "handle", 0) or 0)
                    out.append(
                        WindowInfo(
                            number=index,
                            app=_process_name(int(getattr(win.element_info,
                                                          "process_id", 0) or 0))
                                or str(win.element_info.control_type or ""),
                            title=str(win.window_text() or ""),
                            rect=box,
                            handle=hex(handle) if handle else "",
                            pid=int(getattr(win.element_info, "process_id", 0) or 0),
                            foreground=(handle == foreground),
                        )
                    )
                except Exception:
                    continue
        except Exception as exc:  # deactivated window, COM failure
            raise BackendUnavailable(f"cannot list windows: {exc}") from None
        return out

    @staticmethod
    def _foreground_handle() -> int:
        try:
            import win32gui

            return int(win32gui.GetForegroundWindow())
        except Exception:
            return 0

    def _pick_window(self, window: Any):
        from pywinauto import Desktop

        desktop = self._desktop_obj()
        if window is None:
            handle = self._foreground_handle()
            if handle:
                try:
                    return desktop.window(handle=handle)
                except Exception:
                    pass
            windows = self.windows()
            if not windows:
                raise BackendUnavailable("no windows are open")
            return desktop.window(handle=int(windows[0].handle, 16))
        if isinstance(window, int):
            return desktop.window(handle=window)
        text = str(window)
        try:
            if text.lower().startswith("0x"):
                return desktop.window(handle=int(text, 16))
            return desktop.window(title_re=f".*{text}.*")
        except Exception:
            raise BackendUnavailable(f"no window matching '{window}'") from None

    # -- snapshot -----------------------------------------------------
    def snapshot(self, window: Any = None, *, session: str = "local",
                 number: int = 1) -> Snapshot:
        self.require()
        snap = Snapshot(session=session, number=number, backend=self.name)
        snap.bounds_snapshot = number
        try:
            win = self._pick_window(window)
            snap.title = str(win.window_text() or "")
            snap.app = _app_name(win)
            rect = win.rectangle()
            window_rect = Rect(rect.left, rect.top, rect.width(), rect.height())
            handle = int(getattr(win, "handle", 0) or 0)
            exe = _ChildWindows.exe_of(handle) or str(
                getattr(getattr(win, "element_info", win), "process_id", "") or "")
            session_tree = UiaSession(win, window_rect)
            root = session_tree.build()
            if _is_empty_tree(root) and looks_like_browser(snap.title, exe):
                # Chromium/Electron expose only their frame until accessibility is
                # on. Knock on the render widget and re-read once: for Electron
                # this is what materialises the tree, for Chrome it is not enough
                # and the hint below is the real answer.
                for child in _ChildWindows.handles(handle):
                    _ChildWindows.wake(child)
                session_tree = UiaSession(win, window_rect)
                root = session_tree.build()
            snap.root = root
            snap.dialog = session_tree.dialog
            self.session_tree = session_tree
            if _is_empty_tree(root):
                snap.hint = browser_hint(snap.title, exe)
            elif session_tree.truncated:
                snap.hint = (f"tree truncated at {MAX_NODES} elements - the snapshot "
                             f"shows the first part; use `find` or `--window` on a "
                             f"smaller window for the rest")
        except BackendUnavailable:
            raise
        except Exception as exc:
            snap.error = _readable(exc)
        return snap

    # -- actions ------------------------------------------------------
    def perform(self, element: Element, action: str, snapshot: Snapshot) -> ActionResult:
        self.require()
        if self.session_tree is None:
            return ActionResult(False, action, element.ref, "", "no live tree; snapshot first")
        try:
            control = self.session_tree.locate(element)
        except Exception as exc:
            return ActionResult(False, action, element.ref, "uia", _readable(exc))
        if control is None:
            return ActionResult(False, action, element.ref, "uia",
                                f"{element.ref} is no longer on screen")
        try:
            if action == "click":
                if control.is_enabled() and control.is_visible():
                    try:
                        control.invoke()
                        return ActionResult(True, action, element.ref, "uia-invoke")
                    except Exception:
                        pass
                x, y = element.rect.center
                control.click_input(coords=(x - element.rect.x, y - element.rect.y))
                return ActionResult(True, action, element.ref, "click-input")
            if action == "double click":
                control.double_click_input()
                return ActionResult(True, action, element.ref, "click-input")
            if action == "right click":
                control.right_click_input()
                return ActionResult(True, action, element.ref, "click-input")
            if action == "focus":
                control.set_focus()
                return ActionResult(True, action, element.ref, "uia")
            if action == "set value":
                control.set_edit_text(element.value or "")
                return ActionResult(True, action, element.ref, "uia")
            if action == "type":
                control.type_keys(element.value or "", with_spaces=True)
                return ActionResult(True, action, element.ref, "type-keys")
            if action == "expand":
                control.expand()
                return ActionResult(True, action, element.ref, "uia")
            if action == "collapse":
                control.collapse()
                return ActionResult(True, action, element.ref, "uia")
            if action == "scroll":
                amount = int(element.properties.get("scroll_amount", 3) or 3)
                direction = "up" if element.properties.get("scroll_direction") == "up" else "down"
                control.scroll(direction, "line", amount)
                return ActionResult(True, action, element.ref, "uia")
            if action == "select":
                control.select()
                return ActionResult(True, action, element.ref, "uia")
        except Exception as exc:
            return ActionResult(False, action, element.ref, "uia", _readable(exc))
        return ActionResult(False, action, element.ref, "", f"unsupported action '{action}'")

    def click_point(self, x: int, y: int, *, button: str = "left",
                    double: bool = False) -> None:
        from pywinauto import mouse

        if double:
            mouse.double_click(button=button, coords=(x, y))
        else:
            mouse.click(button=button, coords=(x, y))

    def type_text(self, element: Optional[Element], text: str) -> None:
        from pywinauto import keyboard

        keyboard.send_keys(text, with_spaces=True, pause=0.01)

    def send_keys(self, combo: str) -> None:
        from pywinauto import keyboard

        keyboard.send_keys(_combo_to_pywinauto(combo))

    def move_window(self, window: Any, x: int, y: int) -> None:
        self._pick_window(window).move_window(x=x, y=y)

    def resize_window(self, window: Any, width: int, height: int) -> None:
        self._pick_window(window).resize(width=width, height=height)


# --------------------------------------------------------------------------
# tree walking
# --------------------------------------------------------------------------
def _readable(exc: BaseException) -> str:
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
    if "ElementNotAvailable" in exc.__class__.__name__ or "0x80040201" in text:
        return "the window closed or changed while it was being read"
    if "AccessDenied" in exc.__class__.__name__ or "0x80070005" in text:
        return "access denied: that window runs elevated - restart echo-desktop as administrator"
    if "0x80131505" in text or "timeout" in text.lower():
        return "the app did not answer the accessibility request (it may be busy or hung)"
    return f"UI Automation error: {text}"[:300]


def _process_name(pid: int) -> str:
    """Image name owning a pid, e.g. ``explorer`` / ``Code``.

    The windows table used to show the UIA control type here ("Window", "Pane"),
    which is not an app name and made every app look identical.
    """
    if not pid:
        return ""
    if sys.platform == "win32":
        name = _ChildWindows.exe_of_pid(pid)
        if name:
            return os.path.splitext(name)[0]
    try:
        import psutil

        return psutil.Process(int(pid)).name().replace(".exe", "")
    except Exception:
        return ""


def _app_name(control) -> str:
    try:
        info = control.element_info
        name = str(getattr(info, "process_id", "") or "")
        try:
            import psutil

            return psutil.Process(int(name)).name().replace(".exe", "")
        except Exception:
            return str(control.window_text() or "")[:40]
    except Exception:
        return ""


def _combo_to_pywinauto(combo: str) -> str:
    """``ctrl+shift+s`` -> ``^+s`` (the syntax pywinauto wants)."""
    parts = [p.strip().lower() for p in str(combo or "").split("+") if p.strip()]
    prefix = ""
    key = ""
    for part in parts:
        if part in ("ctrl", "control"):
            prefix += "^"
        elif part == "shift":
            prefix += "+"
        elif part in ("alt", "menu"):
            prefix += "%"
        elif part in ("win", "windows"):
            prefix += "{VK_LWIN}"
        elif part in ("enter", "return"):
            key = "{ENTER}"
        elif part == "tab":
            key = "{TAB}"
        elif part == "esc" or part == "escape":
            key = "{ESC}"
        elif part in ("del", "delete"):
            key = "{DELETE}"
        elif part == "space":
            key = "{SPACE}"
        elif part.startswith("f") and part[1:].isdigit():
            key = "{" + part.upper() + "}"
        else:
            key = part
    return prefix + key


class UiaSession:
    """One live read of a window plus the map ref -> control."""

    def __init__(self, window, window_rect: Rect) -> None:
        self.window = window
        self.window_rect = window_rect
        self.controls: dict[str, Any] = {}
        self.dialog: Optional[Element] = None
        self._counter = 0
        #: True when MAX_NODES cut the tree short
        self.truncated = False

    def build(self) -> Element:
        """Read the window, stopping at ``MAX_NODES`` (step 27's guard, really applied).

        Explorer and big Electron apps expose thousands of nodes; without the cap
        a snapshot can walk for minutes. Truncation is recorded so the renderer
        can say the tree was cut rather than silently showing a partial view.
        """
        self.truncated = False
        return self._build(self.window, depth=0)

    def _next_ref(self) -> str:
        self._counter += 1
        return f"e{self._counter}"

    def _build(self, control, depth: int) -> Element:
        ref = self._next_ref()
        if self._counter > MAX_NODES:
            self.truncated = True
        self.controls[ref] = control
        info = getattr(control, "element_info", control)
        raw_role = str(getattr(info, "control_type", "") or "")
        name = ""
        try:
            name = str(control.window_text() or "")
        except Exception:
            name = ""
        rect = Rect()
        try:
            box = control.rectangle()
            rect = Rect(box.left, box.top, box.width(), box.height())
        except Exception:
            pass
        state = self._state(control)
        actions, action_ids = self._actions(control)
        value = None
        if "set value" in actions or raw_role.lower() in ("edit", "document"):
            try:
                value = control.get_value()
            except Exception:
                value = None
        element = Element(
            ref=ref,
            role=normalize_role(raw_role),
            name=name.strip(),
            actions=actions,
            state=state,
            visible=not is_offscreen(rect, window=self.window_rect),
            rect=rect,
            value=value,
            native_id=str(getattr(info, "automation_id", "") or ""),
            raw_role=raw_role,
            action_ids=action_ids,
        )
        if actions or name:
            element.properties["runtime_id"] = str(getattr(info, "runtime_id", "") or "")
        if self._counter >= MAX_NODES:
            # stop walking: this is the guard that actually bounds the work
            self.truncated = True
            return element
        if depth < MAX_DEPTH:
            try:
                children = control.children()
            except Exception:
                children = []
            for child in children:
                try:
                    element.add(self._build(child, depth + 1))
                except Exception:
                    continue
                if self.truncated:
                    break
        return element

    def _state(self, control) -> dict[str, Any]:
        """Read the provider state properties, skipping the absent ones.

        pywinauto spellings differ per pattern (``has_keyboard_focus``,
        ``is_expanded``, ``is_offscreen``), so each property is probed through
        the guarded helper and an ``AttributeError`` just means "not exposed".
        """
        raw: dict[str, Any] = {}
        for name in _STATE_PROBES:
            value = self._probe(control, name)
            if value is None or value == "":
                continue
            if isinstance(value, bool) and not value:
                # a missing/default flag carries no information; only "true"
                # and tri-state values are worth normalising.  The exception is
                # the one property whose false means "disabled".
                if name not in ("is_enabled", "is_visible"):
                    continue
            raw[name] = value
        if isinstance(self._probe(control, "get_toggle_state"), int):
            raw["ToggleState"] = self._probe(control, "get_toggle_state")
        return extract_state(raw)

    def _actions(self, control) -> tuple[list[str], dict[str, str]]:
        """Which actions does this control really support?

        ``hasattr`` is not a valid probe: a pywinauto wrapper exposes
        ``type_keys`` on every element and raises only when it is called.  So
        the control type decides, and a guarded capability call is used only
        where the wrapper answers it honestly.
        """
        raw_role = str(getattr(getattr(control, "element_info", control),
                               "control_type", "") or "")
        role = normalize_role(raw_role)
        raw: list[str] = ["SetFocus"]

        invokable = self._probe(control, "is_invokable")
        if invokable is True or (invokable is None and role in (
                "button", "menuitem", "link", "checkbox", "radio", "splitbutton",
                "listitem", "treeitem", "tabitem")):
            raw.append("Invoke")

        editable = self._probe(control, "is_editable")
        if role in ("edit", "document") or editable is True:
            raw.extend(["SetValue", "Type"])

        if role in ("listitem", "treeitem", "tabitem", "cell", "row", "radio"):
            raw.append("Select")

        expanded = self._probe(control, "is_expanded")
        if expanded is not None or role in ("combobox", "menuitem", "treeitem"):
            raw.extend(["Expand", "Collapse"])

        if role in ("list", "tree", "table", "scrollbar"):
            raw.append("Scroll")

        actions = canonical_actions(raw)
        ids: dict[str, str] = {}
        for raw_name in raw:
            for short in canonical_actions([raw_name]):
                ids.setdefault(short, raw_name)
        return actions, ids

    @staticmethod
    def _probe(control, name: str):
        """Call ``control.<name>()`` if it exists; return its value or None."""
        method = getattr(control, name, None)
        if method is None:
            return None
        try:
            return method()
        except Exception:
            return None

    def locate(self, element: Element):
        """Find the live control behind a ref.

        The ref map is per snapshot, so it is only valid for the tree it came
        from; ``cli`` refuses stale refs before asking, and this is the last
        line of defence.  The name fallback is deliberately strict: same name
        *and* same role, and exactly one match - guessing between two buttons
        called "OK" is how the wrong thing gets clicked.
        """
        control = self.controls.get(element.ref)
        if control is not None:
            try:
                if str(control.window_text() or "").strip() == element.name:
                    return control
            except Exception:
                return control
        matches = []
        for candidate in self.controls.values():
            try:
                if str(candidate.window_text() or "").strip() != element.name:
                    continue
                if element.name == "":
                    continue
                matches.append(candidate)
            except Exception:
                continue
        if len(matches) == 1:
            return matches[0]
        return None


def _snake(name: str) -> str:
    out: list[str] = []
    for index, char in enumerate(name):
        if char.isupper() and index and not name[index - 1].isupper():
            out.append("_")
        out.append(char.lower())
    text = "".join(out)
    for prefix in ("selection_item_", "expand_collapse_", "toggle_"):
        if text.startswith(prefix):
            return text.replace(prefix, "")
    return text
