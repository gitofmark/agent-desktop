"""``echo-desktop``: read and act on a window through the minimal snapshot.

Read
----
    echo-desktop windows                    list open windows
    echo-desktop snapshot [--bounds]        print the minimal snapshot
    echo-desktop inspect <ref>              full detail for one element
    echo-desktop bounds <ref>               just the geometry
    echo-desktop find "<text>" [--role r]   find refs without printing the tree
    echo-desktop read <ref>                 full text/value of one element
    echo-desktop diff                       what changed since the last snapshot

Act
---
    echo-desktop click <ref> [--double] [--right]
    echo-desktop click-at <x> <y> [--double] [--right]
    echo-desktop type <ref> "<text>"
    echo-desktop set-value <ref> "<text>"
    echo-desktop focus <ref>
    echo-desktop scroll <ref> --down 3
    echo-desktop expand <ref> / collapse <ref>
    echo-desktop key "<combo>"
    echo-desktop move-window <x> <y> / resize-window <w> <h>
    echo-desktop wait <ref-or-text> --timeout 5

Every command takes ``--json``; every Act command takes ``--dry-run`` and
prints a verify diff unless ``--no-verify`` is given.

Exit codes: 0 ok, 1 general error, 2 bad arguments, 3 stale or unknown ref,
4 window not found, 5 action failed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from . import __version__
from . import naming
from .naming import ALIAS, PROG
from .backends import Backend, BackendUnavailable, resolve_backend
from .backends.base import WindowInfo
from .diff import format_diff, diff_snapshots
from .geometry import format_bounds
from .inspect import (
    bounds_of,
    find_elements,
    format_bounds_line,
    format_inspect,
    read_value,
)
from .model import Element, Snapshot
from .reader import ReaderError, read_snapshot
from .refs import RefError, parse_ref, validate_ref
from .render import RenderOptions, render_error, render_snapshot
from .session import SessionState, state_dir
from .state import visible_state

__all__ = ["main", "build_parser", "EXIT_CODES", "CliError"]

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_STALE_REF = 3
EXIT_NO_WINDOW = 4
EXIT_ACTION_FAILED = 5

EXIT_CODES = {
    0: "ok",
    1: "general error",
    2: "bad arguments",
    3: "stale or unknown ref",
    4: "window not found",
    5: "action failed",
}


class CliError(Exception):
    """An error with a message written for a model to read and act on."""

    def __init__(self, message: str, exit_code: int = EXIT_ERROR) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.message = message


# --------------------------------------------------------------------------
# context: state, backend, snapshots
# --------------------------------------------------------------------------
@dataclass
class Context:
    args: argparse.Namespace
    state: SessionState
    backend: Backend
    use_state: bool = True

    @property
    def json(self) -> bool:
        return bool(getattr(self.args, "json", False))

    @property
    def session(self) -> str:
        return self.state.session

    def render_options(self, **overrides: Any) -> RenderOptions:
        opts = RenderOptions(
            max_lines=getattr(self.args, "max_lines", 80) or 80,
            show_bounds=bool(getattr(self.args, "bounds", False)),
            full=bool(getattr(self.args, "full", False)),
            ascii=bool(getattr(self.args, "ascii", False)),
        )
        for key, value in overrides.items():
            setattr(opts, key, value)
        return opts

    # -- snapshots ----------------------------------------------------
    def take_snapshot(self, *, bump: bool = True, store: bool = True) -> Snapshot:
        number = self.state.next_number() if bump else max(1, self.state.number)
        window = getattr(self.args, "window", None)
        try:
            snapshot = self.backend.snapshot(window, session=self.session, number=number)
        except BackendUnavailable as exc:
            message = str(exc)
            lowered = message.lower()
            code = EXIT_NO_WINDOW if ("no window" in lowered or "no such window" in
                                      lowered) else EXIT_ERROR
            raise CliError(message, code) from None
        except ReaderError as exc:
            raise CliError(str(exc), EXIT_ERROR) from None
        if snapshot.error:
            raise CliError(snapshot.error, EXIT_ERROR)
        if snapshot.root is None:
            raise CliError(
                f"window '{window or 'foreground'}' has no accessibility tree",
                EXIT_NO_WINDOW,
            )
        if store and self.use_state:
            try:
                self.state.store(snapshot)
                self.state.remember_refs(snapshot)
                self.state.backend = self.backend.name
                self.state.save()
            except OSError as exc:
                raise CliError(f"cannot write session state: {exc.strerror or exc}") from None
        return snapshot

    def current_snapshot(self) -> Snapshot:
        """The snapshot refs are validated against (fresh only if there is none)."""
        if self.use_state:
            stored = self.state.last_snapshot()
            if stored is not None and stored.root is not None:
                return stored
        return self.take_snapshot()

    def resolve_ref(self, ref: str, snapshot: Snapshot) -> str:
        # refs inside an active modal dialog are valid targets too (R9), so the
        # existence check has to look at both blocks before rejecting
        parts = None
        try:
            parts = parse_ref(ref)
            validate_ref(ref, snapshot.number, self.session, None)
        except RefError as exc:
            raise CliError(str(exc), exc.exit_code) from None
        found = snapshot.find(parts.short)
        if found is None and snapshot.dialog is not None:
            found = snapshot.dialog.find(parts.short)
        if found is not None and parts.session in ("", self.session):
            try:
                self.state.check_ref_identity(parts.short, found, snapshot.number)
            except RefError as exc:
                raise CliError(str(exc), exc.exit_code) from None
        if found is None:
            raise CliError(
                f"unknown ref '{ref}' in snapshot {snapshot.number} "
                f"(session {self.session}); it may have been collapsed by the line "
                'cap - try `echo-desktop snapshot --full` or `echo-desktop find "<name>"`',
                EXIT_STALE_REF,
            )
        return parts.short

    def element(self, ref: str, snapshot: Snapshot) -> Element:
        short = self.resolve_ref(ref, snapshot)
        el = snapshot.find(short)
        if el is None and snapshot.dialog is not None:
            # refs inside an active modal dialog are valid targets too (R9)
            el = snapshot.dialog.find(short)
        if el is None:
            raise CliError(f"unknown ref '{ref}'", EXIT_STALE_REF)
        return el


def make_context(args: argparse.Namespace) -> Context:
    use_state = not getattr(args, "no_state", False)
    state = SessionState.load() if use_state else SessionState()
    if not state.session:
        from .session import new_session_id

        state.session = new_session_id()
    backend_name = getattr(args, "backend", "auto") or "auto"
    fixture = getattr(args, "fixture", None)
    if fixture and backend_name in ("auto", "fixture"):
        os.environ[naming.ENV_FIXTURE] = fixture
        backend_name = "fixture"
    if backend_name == "auto" and fixture:
        backend_name = "fixture"
    try:
        backend = resolve_backend(backend_name)
    except BackendUnavailable as exc:
        raise CliError(str(exc), EXIT_ERROR) from None
    except ValueError as exc:
        raise CliError(str(exc), EXIT_USAGE) from None
    if backend.name == "fixture" and fixture:
        backend.path = fixture  # type: ignore[attr-defined]
    ok, why = backend.available()
    if not ok:
        # `doctor` exists to explain exactly this situation, so it must not fail
        # with it: it reports per-backend availability itself.
        if getattr(args, "command", "") != "doctor":
            raise CliError(why, EXIT_ERROR)
    return Context(args=args, state=state, backend=backend, use_state=use_state)


# --------------------------------------------------------------------------
# output helpers
# --------------------------------------------------------------------------
def emit(text: str) -> None:
    sys.stdout.write(text if text.endswith("\n") else text + "\n")


def emit_json(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def _warn(message: str) -> None:
    sys.stderr.write(f"warning: {message}\n")


def _window_arg(ctx: Context) -> Optional[str]:
    return getattr(ctx.args, "window", None)


def _require_window_exists(ctx: Context) -> None:
    """Turn 'no window matching x' into exit code 4.

    A backend that cannot enumerate windows at all (a fixture, or X11 without
    wmctrl) is not an error: asking for a specific window just proceeds and
    lets the snapshot itself report the real problem.
    """
    try:
        windows = ctx.backend.windows()
    except (BackendUnavailable, ReaderError) as exc:
        # a backend that cannot enumerate windows is not a "window not found":
        # asking for a specific window proceeds and lets the snapshot report
        if _window_arg(ctx):
            return
        raise CliError(str(exc), EXIT_ERROR) from None
    wanted = _window_arg(ctx)
    if wanted is None:
        if not windows:
            raise CliError("no windows are open", EXIT_NO_WINDOW)
        return
    text = str(wanted).lower()
    for info in windows:
        if text == info.handle.lower() or text in info.title.lower() or text == info.app.lower():
            return
    names = ", ".join(f'"{w.title or w.app}"' for w in windows[:6]) or "none"
    raise CliError(
        f"no window matching '{wanted}'; open windows: {names}",
        EXIT_NO_WINDOW,
    )


# --------------------------------------------------------------------------
# read commands
# --------------------------------------------------------------------------
def cmd_windows(ctx: Context) -> int:
    try:
        windows = ctx.backend.windows()
    except BackendUnavailable as exc:
        raise CliError(str(exc), EXIT_NO_WINDOW) from None
    if ctx.json:
        emit_json({"windows": [w.as_dict() for w in windows], "backend": ctx.backend.name})
        return EXIT_OK
    if not windows:
        emit("no windows are open")
        return EXIT_OK
    emit("    #  app            title                                                        "
         "position size")
    for info in windows:
        emit(info.line())
    return EXIT_OK


def cmd_snapshot(ctx: Context) -> int:
    if ctx.backend.name == "fixture":
        # a capture stands in for exactly one window: nothing to look up
        pass
    else:
        _require_window_exists(ctx)
    snapshot = ctx.take_snapshot()
    opts = ctx.render_options()
    text = render_snapshot(snapshot, opts)
    if ctx.json:
        emit_json({
            "snapshot": snapshot.number,
            "session": snapshot.session,
            "app": snapshot.app,
            "title": snapshot.title,
            "backend": snapshot.backend,
            "text": text,
            "elements": snapshot.count(),
            "lines": len(text.splitlines()),
        })
        return EXIT_OK
    emit(text)
    return EXIT_OK


def cmd_inspect(ctx: Context) -> int:
    snapshot = ctx.current_snapshot()
    if ctx.json:
        from .inspect import inspect_to_dict

        try:
            emit_json(inspect_to_dict(snapshot, ctx.args.ref, snapshot.number, ctx.session))
        except RefError as exc:
            raise CliError(str(exc), exc.exit_code) from None
        return EXIT_OK
    try:
        emit(format_inspect(snapshot, ctx.args.ref, snapshot.number, ctx.session))
    except RefError as exc:
        raise CliError(str(exc), exc.exit_code) from None
    return EXIT_OK


def cmd_bounds(ctx: Context) -> int:
    snapshot = ctx.current_snapshot()
    try:
        if ctx.json:
            emit_json(bounds_of(snapshot, ctx.args.ref, snapshot.number, ctx.session))
        else:
            emit(format_bounds_line(snapshot, ctx.args.ref, snapshot.number, ctx.session))
    except RefError as exc:
        raise CliError(str(exc), exc.exit_code) from None
    return EXIT_OK


def cmd_find(ctx: Context) -> int:
    snapshot = ctx.current_snapshot()
    try:
        matches = find_elements(snapshot, ctx.args.text, ctx.args.role or "")
    except RefError as exc:
        raise CliError(str(exc), exc.exit_code) from None
    if ctx.json:
        emit_json({"matches": matches, "snapshot": snapshot.number,
                   "session": snapshot.session})
        return EXIT_OK
    if not matches:
        emit(f'no element matches "{ctx.args.text}"; try `echo-desktop snapshot`')
        return EXIT_ERROR
    for row in matches:
        extra = " - " + ", ".join(row["state"]) if row["state"] else ""
        name = row["name"] or "(unnamed)"
        emit(f"{row['ref']} [{row['role']}] {name}{extra}")
    return EXIT_OK


def cmd_read(ctx: Context) -> int:
    snapshot = ctx.current_snapshot()
    try:
        text = read_value(snapshot, ctx.args.ref, snapshot.number, ctx.session)
    except RefError as exc:
        raise CliError(str(exc), exc.exit_code) from None
    if ctx.json:
        emit_json({"ref": ctx.args.ref, "value": text, "length": len(text)})
    else:
        sys.stdout.write(text if text.endswith("\n") else text + "\n")
    return EXIT_OK


def cmd_diff(ctx: Context) -> int:
    if ctx.use_state:
        previous = ctx.state.previous_snapshot()
        current = ctx.state.last_snapshot()
    else:
        previous, current = None, None
    if current is None:
        current = ctx.take_snapshot()
        previous = None
    changes = diff_snapshots(previous, current)
    if ctx.json:
        emit_json({
            "changes": [c.text for c in changes],
            "from": previous.number if previous else None,
            "to": current.number,
        })
        return EXIT_OK
    emit(format_diff(changes))
    return EXIT_OK


# --------------------------------------------------------------------------
# act commands
# --------------------------------------------------------------------------
DESTRUCTIVE_KEYS = {"alt+f4", "alt+F4", "ctrl+w", "delete", "del", "shift+delete"}


def _dry_run(ctx: Context) -> bool:
    return bool(getattr(ctx.args, "dry_run", False))


def _finish_action(ctx: Context, snapshot: Snapshot, result: Any,
                   action: str, ref: str, target: Optional[Element]) -> int:
    """Common tail of every action: output, verify diff, exit code."""
    payload: dict[str, Any] = {
        "ok": bool(getattr(result, "ok", True)),
        "action": action,
        "ref": ref,
        "method": getattr(result, "method", ""),
        "dry_run": _dry_run(ctx),
    }
    if target is not None:
        cx, cy = target.rect.center
        payload["point"] = {"x": cx, "y": cy}
    message = getattr(result, "message", "")
    if message:
        payload["message"] = message

    dry = _dry_run(ctx)
    verify_text = ""
    if not dry and getattr(result, "ok", True) and not getattr(ctx.args, "no_verify", False):
        try:
            after = ctx.take_snapshot()
            changes = diff_snapshots(snapshot, after)
            verify_text = format_diff(changes)
            payload["verify"] = [c.text for c in changes]
            payload["snapshot"] = after.number
        except CliError as exc:
            verify_text = f"(could not verify: {exc.message})"
            payload["verify"] = []

    if ctx.json:
        payload["text"] = verify_text
        emit_json(payload)
    else:
        if dry:
            point = payload.get("point")
            where = f" at {point['x']},{point['y']}" if point else ""
            emit(f"dry run: would {action} {ref}{where} - nothing was changed")
        elif message:
            emit(message)
        if verify_text:
            emit("")
            emit(verify_text)
    if dry:
        return EXIT_OK
    if not getattr(result, "ok", True):
        return EXIT_ACTION_FAILED
    return EXIT_OK


def _confirm_risky(ctx: Context, action: str, target: Optional[Element],
                   ref: str) -> None:
    if getattr(ctx.args, "yes", False) or _dry_run(ctx):
        return
    name = (target.name if target else "") or ""
    risky = action == "click" and name.strip().lower() in ("close", "exit", "quit")
    if risky:
        _warn(f"'{name}' closes the window; pass --yes to silence this warning")


def cmd_click(ctx: Context) -> int:
    snapshot = ctx.current_snapshot()
    el = ctx.element(ctx.args.ref, snapshot)
    _confirm_risky(ctx, "click", el, el.ref)
    action = "double click" if ctx.args.double else ("right click" if ctx.args.right else "click")
    if _dry_run(ctx):
        return _finish_action(ctx, snapshot, type("R", (), {"ok": True, "method": "", "message": ""})(),
                              action, el.ref, el)
    result = ctx.backend.perform(el, action, snapshot)
    return _finish_action(ctx, snapshot, result, action, el.ref, el)


def cmd_click_at(ctx: Context) -> int:
    x, y = int(ctx.args.x), int(ctx.args.y)
    snapshot = ctx.current_snapshot() if ctx.use_state else ctx.take_snapshot()
    if _dry_run(ctx):
        if ctx.json:
            emit_json({"ok": True, "action": "click-at", "point": {"x": x, "y": y},
                       "dry_run": True})
        else:
            emit(f"dry run: would click at {x},{y} - nothing was changed")
        return EXIT_OK
    try:
        ctx.backend.click_point(x, y, button="right" if ctx.args.right else "left",
                                double=bool(ctx.args.double))
    except BackendUnavailable as exc:
        raise CliError(str(exc), EXIT_ACTION_FAILED) from None
    try:
        after = ctx.take_snapshot()
        changes = diff_snapshots(snapshot, after)
    except CliError:
        changes = []
    if ctx.json:
        emit_json({"ok": True, "action": "click-at", "point": {"x": x, "y": y},
                   "verify": [c.text for c in changes]})
    else:
        emit(f"clicked {x},{y}")
        if changes:
            emit("")
            emit(format_diff(changes))
    return EXIT_OK


def cmd_type(ctx: Context) -> int:
    snapshot = ctx.current_snapshot()
    el = ctx.element(ctx.args.ref, snapshot)
    text = ctx.args.text
    if _dry_run(ctx):
        if ctx.json:
            emit_json({"ok": True, "action": "type", "ref": el.ref, "text": text,
                       "dry_run": True, "point": dict(zip(("x", "y"), el.rect.center))})
        else:
            emit(f'dry run: would type "{text}" into {el.ref} - nothing was changed')
        return EXIT_OK
    try:
        ctx.backend.type_text(el, text)
        result = type("R", (), {"ok": True, "method": "type", "message": ""})()
    except BackendUnavailable as exc:
        result = type("R", (), {"ok": False, "method": "type", "message": str(exc)})()
    return _finish_action(ctx, snapshot, result, "type", el.ref, el)


def cmd_set_value(ctx: Context) -> int:
    snapshot = ctx.current_snapshot()
    el = ctx.element(ctx.args.ref, snapshot)
    if _dry_run(ctx):
        value = ctx.args.text
        if ctx.json:
            emit_json({"ok": True, "action": "set-value", "ref": el.ref,
                       "text": value, "dry_run": True})
        else:
            emit(f'dry run: would set {el.ref} to "{value}" - nothing was changed')
        return EXIT_OK
    # the value travels through the element the backend will act on
    el.value = ctx.args.text
    result = ctx.backend.perform(el, "set value", snapshot)
    return _finish_action(ctx, snapshot, result, "set-value", el.ref, el)


def cmd_focus(ctx: Context) -> int:
    snapshot = ctx.current_snapshot()
    el = ctx.element(ctx.args.ref, snapshot)
    if _dry_run(ctx):
        if ctx.json:
            emit_json({"ok": True, "action": "focus", "ref": el.ref, "dry_run": True})
        else:
            emit(f"dry run: would focus {el.ref} - nothing was changed")
        return EXIT_OK
    result = ctx.backend.perform(el, "focus", snapshot)
    return _finish_action(ctx, snapshot, result, "focus", el.ref, el)


def cmd_scroll(ctx: Context) -> int:
    snapshot = ctx.current_snapshot()
    el = ctx.element(ctx.args.ref, snapshot)
    amount = int(ctx.args.down or ctx.args.up or 3)
    direction = "up" if ctx.args.up else "down"
    if _dry_run(ctx):
        if ctx.json:
            emit_json({"ok": True, "action": "scroll", "ref": el.ref,
                       "direction": direction, "amount": amount, "dry_run": True})
        else:
            emit(f"dry run: would scroll {el.ref} {direction} {amount} - nothing was changed")
        return EXIT_OK
    el.properties["scroll_amount"] = amount
    el.properties["scroll_direction"] = direction
    result = ctx.backend.perform(el, "scroll", snapshot)
    return _finish_action(ctx, snapshot, result, "scroll", el.ref, el)


def cmd_expand(ctx: Context) -> int:
    return _toggle(ctx, "expand")


def cmd_collapse(ctx: Context) -> int:
    return _toggle(ctx, "collapse")


def _toggle(ctx: Context, action: str) -> int:
    snapshot = ctx.current_snapshot()
    el = ctx.element(ctx.args.ref, snapshot)
    if _dry_run(ctx):
        if ctx.json:
            emit_json({"ok": True, "action": action, "ref": el.ref, "dry_run": True})
        else:
            emit(f"dry run: would {action} {el.ref} - nothing was changed")
        return EXIT_OK
    result = ctx.backend.perform(el, action, snapshot)
    return _finish_action(ctx, snapshot, result, action, el.ref, el)


def cmd_key(ctx: Context) -> int:
    combo = ctx.args.combo
    snapshot = ctx.current_snapshot() if ctx.use_state else ctx.take_snapshot()
    if combo.strip().lower() in DESTRUCTIVE_KEYS and not (
        getattr(ctx.args, "yes", False) or _dry_run(ctx)
    ):
        _warn(f"'{combo}' is destructive and may close the window without saving; "
              "pass --yes to silence this warning")
    if _dry_run(ctx):
        if ctx.json:
            emit_json({"ok": True, "action": "key", "keys": combo, "dry_run": True})
        else:
            emit(f"dry run: would send {combo} - nothing was changed")
        return EXIT_OK
    try:
        ctx.backend.send_keys(combo)
        result = type("R", (), {"ok": True, "method": "keys", "message": ""})()
    except BackendUnavailable as exc:
        result = type("R", (), {"ok": False, "method": "keys", "message": str(exc)})()
    return _finish_action(ctx, snapshot, result, "key", combo, None)


def cmd_move_window(ctx: Context) -> int:
    return _window_op(ctx, "move")


def cmd_resize_window(ctx: Context) -> int:
    return _window_op(ctx, "resize")


def _window_op(ctx: Context, kind: str) -> int:
    window = _window_arg(ctx)
    if kind == "move":
        x, y = int(ctx.args.x), int(ctx.args.y)
        what = f"{x},{y}"
    else:
        x, y = int(ctx.args.width), int(ctx.args.height)
        what = f"{x}x{y}"
    if _dry_run(ctx):
        if ctx.json:
            emit_json({"ok": True, "action": kind + "-window", "value": what, "dry_run": True})
        else:
            emit(f"dry run: would {kind} window to {what} - nothing was changed")
        return EXIT_OK
    try:
        if kind == "move":
            ctx.backend.move_window(window, x, y)
        else:
            ctx.backend.resize_window(window, x, y)
    except BackendUnavailable as exc:
        raise CliError(str(exc), EXIT_ACTION_FAILED) from None
    try:
        after = ctx.take_snapshot()
        rect = after.window_rect()
    except CliError:
        rect = None
    if ctx.json:
        emit_json({"ok": True, "action": kind + "-window", "value": what,
                   "rect": rect.as_dict() if rect else None})
    else:
        emit(f"{kind}d window to {what}" + (f"; it is now at {rect}" if rect else ""))
    return EXIT_OK


def cmd_wait(ctx: Context) -> int:
    wanted = ctx.args.target
    timeout = float(ctx.args.timeout)
    interval = max(0.1, min(0.5, float(ctx.args.interval)))
    deadline = time.time() + timeout
    last_error = ""
    while True:
        try:
            snapshot = ctx.take_snapshot()
        except CliError as exc:
            last_error = exc.message
            snapshot = None
        if snapshot is not None:
            found = None
            try:
                parts = parse_ref(wanted)
                found = snapshot.find(parts.short)
            except RefError:
                wanted_lower = wanted.lower()
                for el in snapshot.walk():
                    if wanted_lower in (el.name or "").lower():
                        found = el
                        break
            if found is not None:
                if ctx.json:
                    emit_json({"ok": True, "ref": found.ref, "role": found.role,
                               "name": found.name, "snapshot": snapshot.number})
                else:
                    emit(f"found {found.ref} [{found.role}] {found.name}")
                return EXIT_OK
        if time.time() >= deadline:
            break
        time.sleep(interval)
    message = f'waited {timeout:g}s for "{wanted}" but it did not appear'
    if last_error:
        message += f" (last read error: {last_error})"
    raise CliError(message, EXIT_ACTION_FAILED)


def cmd_doctor(ctx: Context) -> int:
    """Print what this machine can do - useful on Windows and Linux alike."""
    from .backends import list_backends

    checks: list[dict[str, Any]] = []
    for name in list_backends():
        try:
            backend = resolve_backend(name)
            ok, why = backend.available()
        except Exception as exc:  # pragma: no cover - defensive
            ok, why = False, str(exc)
        checks.append({"backend": name, "available": ok, "detail": why})
    try:
        import shutil

        tools = {t: shutil.which(t) for t in ("xdotool", "xwininfo", "wmctrl", "xprop")}
    except Exception:  # pragma: no cover
        tools = {}
    payload = {
        "version": __version__,
        "platform": sys.platform,
        "display": os.environ.get("DISPLAY", ""),
        "state_dir": state_dir(create=False),
        "backend_in_use": ctx.backend.name,
        "backends": checks,
        "x_tools": tools,
    }
    if ctx.json:
        emit_json(payload)
        return EXIT_OK
    emit(f"{PROG} {__version__} on {sys.platform}")
    emit(f"backend in use: {ctx.backend.name}")
    for row in checks:
        mark = "ok  " if row["available"] else "no  "
        emit(f"  {mark}{row['backend']:<12} {row['detail'] or 'ready'}")
    if sys.platform.startswith("linux"):
        missing = [t for t, path in tools.items() if not path]
        if missing:
            emit(f"  hint: install {', '.join(missing)} for element-level detail "
                 "(sudo apt install xdotool x11-utils wmctrl)")
    return EXIT_OK


READ_COMMANDS = {"windows", "snapshot", "inspect", "bounds", "find", "read", "diff", "doctor"}
ACT_COMMANDS = {"click", "click-at", "type", "set-value", "focus", "scroll",
                "expand", "collapse", "key", "move-window", "resize-window", "wait"}


# --------------------------------------------------------------------------
# argument parsing
# --------------------------------------------------------------------------
EPILOG = """exit codes: 0 ok, 1 general error, 2 bad arguments, 3 stale or unknown ref,
            4 window not found, 5 action failed

the old name ``agent-desktop-py`` still works for one release and prints a
notice on stderr; it is removed in 1.1

examples:
  echo-desktop windows
  echo-desktop snapshot
  echo-desktop bounds e10
  echo-desktop click e10 --dry-run
  echo-desktop click e10
  echo-desktop --fixture tests/fixtures/notepad.raw.txt snapshot
"""


def _common(parser: argparse.ArgumentParser, *, act: bool = False) -> None:
    parser.add_argument("--json", action="store_true", help="machine readable output")
    parser.add_argument("--window", metavar="TITLE|HWND",
                        help="target window (default: the foreground window)")
    parser.add_argument("--backend", default=None,
                        help="where the tree comes from: auto, fixture, "
                             "windows-uia, linux-x11 (default: auto)")
    parser.add_argument("--fixture", metavar="FILE",
                        help="read the tree from a captured raw dump instead of a desktop")
    parser.add_argument("--no-state", action="store_true",
                        help=f"do not read or write {naming.STATE_DIR_NAME}/ session state")
    if act:
        parser.add_argument("--dry-run", action="store_true",
                            help="print what would happen and do nothing")
        parser.add_argument("--no-verify", action="store_true",
                            help="skip the verify diff after the action")
        parser.add_argument("--yes", action="store_true",
                            help="silence destructive-action warnings")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Minimal natural snapshots of a desktop window, plus actions on refs.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    p = subparsers.add_parser("windows", help="list open windows")
    _common(p)
    p.set_defaults(func=cmd_windows)

    p = subparsers.add_parser("snapshot", help="print the minimal snapshot of a window")
    _common(p)
    p.add_argument("--bounds", action="store_true", help="show geometry inline (R10)")
    p.add_argument("--max-lines", type=int, default=80, metavar="N",
                   help="line cap (default 80, 0 = no cap)")
    p.add_argument("--full", action="store_true",
                   help="no line cap and include offscreen elements")
    p.add_argument("--ascii", action="store_true", help="use | and - instead of · and —")
    p.set_defaults(func=cmd_snapshot)

    p = subparsers.add_parser("inspect", help="full details of one element")
    _common(p)
    p.add_argument("ref")
    p.set_defaults(func=cmd_inspect)

    p = subparsers.add_parser("bounds", help="geometry of one element")
    _common(p)
    p.add_argument("ref")
    p.set_defaults(func=cmd_bounds)

    p = subparsers.add_parser("find", help="find refs by name")
    _common(p)
    p.add_argument("text")
    p.add_argument("--role", default="", help="restrict to one role, e.g. button")
    p.set_defaults(func=cmd_find)

    p = subparsers.add_parser("read", help="full text/value of one element")
    _common(p)
    p.add_argument("ref")
    p.set_defaults(func=cmd_read)

    p = subparsers.add_parser("diff", help="what changed since the previous snapshot")
    _common(p)
    p.set_defaults(func=cmd_diff)

    p = subparsers.add_parser("doctor", help="check what this machine can read")
    _common(p)
    p.set_defaults(func=cmd_doctor)

    p = subparsers.add_parser("click", help="click an element")
    _common(p, act=True)
    p.add_argument("ref")
    p.add_argument("--double", action="store_true")
    p.add_argument("--right", action="store_true")
    p.set_defaults(func=cmd_click)

    p = subparsers.add_parser("click-at", help="click raw screen coordinates")
    _common(p, act=True)
    p.add_argument("x", type=int)
    p.add_argument("y", type=int)
    p.add_argument("--double", action="store_true")
    p.add_argument("--right", action="store_true")
    p.set_defaults(func=cmd_click_at)

    p = subparsers.add_parser("type", help="focus an element and type text")
    _common(p, act=True)
    p.add_argument("ref")
    p.add_argument("text")
    p.set_defaults(func=cmd_type)

    p = subparsers.add_parser("set-value", help="replace the content of an element")
    _common(p, act=True)
    p.add_argument("ref")
    p.add_argument("text")
    p.set_defaults(func=cmd_set_value)

    p = subparsers.add_parser("focus", help="move focus to an element")
    _common(p, act=True)
    p.add_argument("ref")
    p.set_defaults(func=cmd_focus)

    p = subparsers.add_parser("scroll", help="scroll an element")
    _common(p, act=True)
    p.add_argument("ref")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--down", type=int, nargs="?", const=3, default=None)
    group.add_argument("--up", type=int, nargs="?", const=3, default=None)
    p.set_defaults(func=cmd_scroll)

    p = subparsers.add_parser("expand", help="open a menu or tree item")
    _common(p, act=True)
    p.add_argument("ref")
    p.set_defaults(func=cmd_expand)

    p = subparsers.add_parser("collapse", help="close a menu or tree item")
    _common(p, act=True)
    p.add_argument("ref")
    p.set_defaults(func=cmd_collapse)

    p = subparsers.add_parser("key", help="send a key combination, e.g. ctrl+s")
    _common(p, act=True)
    p.add_argument("combo")
    p.set_defaults(func=cmd_key)

    p = subparsers.add_parser("move-window", help="move the window to x,y")
    _common(p, act=True)
    p.add_argument("x", type=int)
    p.add_argument("y", type=int)
    p.set_defaults(func=cmd_move_window)

    p = subparsers.add_parser("resize-window", help="resize the window")
    _common(p, act=True)
    p.add_argument("width", type=int)
    p.add_argument("height", type=int)
    p.set_defaults(func=cmd_resize_window)

    p = subparsers.add_parser("wait", help="wait until an element or text appears")
    _common(p, act=True)
    p.add_argument("target", help="a ref (e10) or part of a name")
    p.add_argument("--timeout", type=float, default=5.0)
    p.add_argument("--interval", type=float, default=0.25,
                   help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_wait)

    return parser


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------
def main(argv: Optional[Sequence[str]] = None) -> int:
    naming.set_executable(sys.argv[0] if sys.argv else "")
    naming.warn_alias()
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_USAGE

    ctx: Optional[Context] = None
    try:
        ctx = make_context(args)
        return int(args.func(ctx)) or EXIT_OK
    except CliError as exc:
        _report(args, exc.message, exc.exit_code)
        return exc.exit_code
    except RefError as exc:
        _report(args, str(exc), exc.exit_code)
        return exc.exit_code
    except ReaderError as exc:
        _report(args, str(exc), EXIT_ERROR)
        return EXIT_ERROR
    except BackendUnavailable as exc:
        _report(args, str(exc), EXIT_ERROR)
        return EXIT_ERROR
    except KeyboardInterrupt:
        _report(args, "interrupted", EXIT_ERROR)
        return EXIT_ERROR
    except SystemExit:
        raise
    except Exception as exc:  # never show a traceback (Step 26)
        detail = f"{exc.__class__.__name__}: {exc}" if str(exc) else exc.__class__.__name__
        _report(args, f"unexpected error - {detail}", EXIT_ERROR)
        return EXIT_ERROR
    finally:
        if ctx is not None:
            try:
                ctx.backend.close()
            except Exception:
                pass


def _report(args: argparse.Namespace, message: str, exit_code: int) -> None:
    """One readable line on stderr, or a JSON object on stdout with --json."""
    if getattr(args, "json", False):
        sys.stdout.write(json.dumps(
            {"ok": False, "error": message, "exit_code": exit_code,
             "code": EXIT_CODES.get(exit_code, "error")},
            indent=2, ensure_ascii=False) + "\n")
        return
    hint = ""
    if exit_code == EXIT_STALE_REF:
        hint = " (run `echo-desktop snapshot` and use the refs it prints)"
    elif exit_code == EXIT_NO_WINDOW:
        hint = " (run `echo-desktop windows` to see what is open)"
    sys.stderr.write(f"! {message}{hint}\n")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
