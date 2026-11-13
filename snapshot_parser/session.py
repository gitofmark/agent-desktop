"""Session and snapshot bookkeeping between CLI invocations.

A snapshot is only useful if the *next* command can talk about it, and every
CLI call is a new process.  This module keeps a tiny piece of state on disk::

    .echo-desktop/
      state.json      session id, snapshot counter, selected window, backend
      last.raw.txt    the last snapshot, in the native raw format
      prev.raw.txt    the one before it, for ``diff``

Refs carry the session id (``@syzqsppsg5kob:e10``) so a ref from another
machine, another window, or an older snapshot is rejected instead of quietly
clicking the wrong thing (R8, Step 14).

Nothing here is committed to git: the directory is in ``.gitignore``.
"""

from __future__ import annotations

import json
import os
import random
import string
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from . import naming
from .model import Snapshot
from .reader import ReaderError, parse_raw_text, render_raw_text

__all__ = [
    "STATE_DIR_NAME",
    "LEGACY_STATE_DIR_NAME",
    "_migrate_legacy_state",
    "SessionState",
    "new_session_id",
    "state_dir",
    "load_state",
    "save_state",
    "clear_state",
]

STATE_DIR_NAME = naming.STATE_DIR_NAME
LEGACY_STATE_DIR_NAME = naming.LEGACY_STATE_DIR_NAME
_ALPHABET = string.ascii_lowercase + string.digits


def new_session_id(length: int = 10) -> str:
    """Short, lowercase, url-safe, collision-unlikely: ``syzqsppsg5kob``."""
    return "".join(random.choice(_ALPHABET) for _ in range(length))


def state_dir(create: bool = True) -> str:
    """Where session state lives: ``$ECHO_STATE``, else ``./.echo-desktop``.

    A pre-rename ``./.agent-desktop/`` is migrated on first use so an existing
    user keeps their ref history instead of silently losing it.
    """
    override = naming.env(naming.ENV_STATE, naming.LEGACY_ENV_STATE)
    path = override or os.path.join(os.getcwd(), STATE_DIR_NAME)
    if create:
        if not override:
            # migrate *before* creating, or the destination exists and the move
            # is skipped as a clobber - which silently dropped every ref history
            _migrate_legacy_state(os.getcwd())
        os.makedirs(path, exist_ok=True)
    return path


def _migrate_legacy_state(base: str) -> Optional[str]:
    """Move ``./.agent-desktop/`` to ``./.echo-desktop/`` once. Returns the path moved."""
    old = os.path.join(base, LEGACY_STATE_DIR_NAME)
    new = os.path.join(base, STATE_DIR_NAME)
    if not os.path.isdir(old) or os.path.exists(new):
        return None
    try:
        os.rename(old, new)
    except OSError:
        return None
    return new


@dataclass
class SessionState:
    session: str = ""
    number: int = 0
    window: str = ""
    backend: str = "auto"
    fixture: str = ""
    updated: float = 0.0
    history: list[str] = field(default_factory=list)
    #: what each ref meant at each snapshot number we printed:
    #: ``{"4": {"e6": ["button", "Close"]}}``. This is what makes a bare
    #: ``e6`` safe: on the *same* snapshot number, if ``e6`` has become a
    #: different element the ref is rejected instead of acting on a stranger.
    #: The last three snapshots are kept.
    refs: dict[str, dict[str, list[str]]] = field(default_factory=dict)

    @classmethod
    def load(cls, directory: Optional[str] = None) -> "SessionState":
        directory = directory or state_dir()
        path = os.path.join(directory, "state.json")
        data: dict[str, Any] = {}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
            except (OSError, json.JSONDecodeError):
                data = {}
        state = cls(
            session=str(data.get("session") or ""),
            number=int(data.get("number", 0) or 0),
            window=str(data.get("window") or ""),
            backend=str(data.get("backend") or "auto"),
            fixture=str(data.get("fixture") or ""),
            updated=float(data.get("updated", 0) or 0),
            history=[str(h) for h in data.get("history", [])][-20:],
            refs={
                str(number): {
                    str(ref): [str(pair[0]), str(pair[1])]
                    for ref, pair in (entries or {}).items()
                    if isinstance(pair, list) and len(pair) == 2
                }
                for number, entries in (data.get("refs") or {}).items()
                if isinstance(entries, dict)
            },
        )
        if not state.session:
            state.session = new_session_id()
        return state

    def save(self, directory: Optional[str] = None) -> str:
        directory = directory or state_dir()
        self.updated = time.time()
        path = os.path.join(directory, "state.json")
        payload = {
            "session": self.session,
            "number": self.number,
            "window": self.window,
            "backend": self.backend,
            "fixture": self.fixture,
            "updated": self.updated,
            "history": self.history[-20:],
            "refs": self.refs,
        }
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        os.replace(tmp, path)
        return path

    # -- snapshot files ----------------------------------------------
    def _path(self, name: str, directory: Optional[str] = None) -> str:
        return os.path.join(directory or state_dir(), name)

    def store(self, snapshot: Snapshot, directory: Optional[str] = None) -> str:
        """Persist this snapshot, rotating the previous one to ``prev.raw.txt``."""
        directory = directory or state_dir()
        last = self._path("last.raw.txt", directory)
        prev = self._path("prev.raw.txt", directory)
        if os.path.exists(last):
            try:
                os.replace(last, prev)
            except OSError:
                pass
        # the dialog block is part of the snapshot: persist it, or refs to the
        # dialog would be rejected on the next command (R9, step 24)
        text = render_raw_text(snapshot, include_dialog=True)
        with open(last, "w", encoding="utf-8") as handle:
            handle.write(text)
        self.number = snapshot.number
        self.session = snapshot.session or self.session
        self.history.append(f"{snapshot.number}:{snapshot.title or snapshot.app}")
        return last

    def last_snapshot(self, directory: Optional[str] = None) -> Optional[Snapshot]:
        return self._load_file("last.raw.txt", directory)

    def previous_snapshot(self, directory: Optional[str] = None) -> Optional[Snapshot]:
        return self._load_file("prev.raw.txt", directory)

    def _load_file(self, name: str, directory: Optional[str]) -> Optional[Snapshot]:
        path = self._path(name, directory)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return parse_raw_text(handle.read(), source=path)
        except (OSError, ReaderError):
            return None

    def remember_refs(self, snapshot: Snapshot) -> None:
        """Record ref -> (role, name) for the snapshot that was just printed."""
        entries: dict[str, list[str]] = {}
        for el in snapshot.walk():
            entries[el.ref] = [el.role, el.name]
        if snapshot.dialog is not None:
            for el in snapshot.dialog.walk():
                entries[el.ref] = [el.role, el.name]
        self.refs[str(snapshot.number)] = entries
        # keep the map small: the last three snapshots are enough to explain
        # any ref a caller can still be holding
        for number in sorted(self.refs, key=lambda n: int(n) if n.isdigit() else 0)[:-3]:
            self.refs.pop(number, None)

    def refs_for(self, number: int) -> dict[str, list[str]]:
        return self.refs.get(str(number), {})

    def check_ref_identity(self, ref: str, element, number: Optional[int] = None) -> None:
        """Raise RefError when ``ref`` means a different element than it did.

        Only checked against the snapshot number the ref was taken at, so a
        legitimately refreshed tree (a new number) never trips it.
        """
        from .refs import RefError

        entries = self.refs_for(self.number if number is None else number)
        known = entries.get(ref)
        if not known:
            return          # never printed by us: nothing to compare against
        role, name = known
        if role == element.role and name == element.name:
            return
        raise RefError(
            f"ref '{ref}' is stale: it meant [{role}] {name or '(unnamed)'!r} when "
            f"it was printed, but the tree has changed and it now means "
            f"[{element.role}] {element.name or '(unnamed)'!r}; run "
            f"`{naming.command()} snapshot` and use the refs it prints",
            ref=ref,
        )

    def next_number(self) -> int:
        return int(self.number) + 1

    def reset_number(self) -> None:
        self.number = 0


def load_state(directory: Optional[str] = None) -> SessionState:
    return SessionState.load(directory)


def save_state(state: SessionState, directory: Optional[str] = None) -> str:
    return state.save(directory)


def clear_state(directory: Optional[str] = None) -> None:
    directory = directory or state_dir(create=False)
    for name in ("state.json", "last.raw.txt", "prev.raw.txt"):
        try:
            os.remove(os.path.join(directory, name))
        except OSError:
            continue
