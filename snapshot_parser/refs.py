"""Short ref <-> full ref conversion and stale-ref detection (R2, R8, R10).

A ref is the only handle an agent gets.  It must be cheap to write (``e10``),
globally unique (``@syzqsppsg5kob:e10``) and it must fail loudly when it belongs
to a snapshot that has moved on.
"""

from __future__ import annotations

# hints point at the installed command name
from .naming import command as _command

import re
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from .model import Snapshot

__all__ = [
    "RefError",
    "RefParts",
    "parse_ref",
    "short_ref",
    "full_ref",
    "make_ref",
    "sort_refs_numerically",
    "validate_ref",
    "RefRegistry",
]

_SHORT_RE = re.compile(r"^(?:@(?P<session>[A-Za-z0-9_\-]+):)?(?P<short>e(?P<num>\d+))$")


class RefError(ValueError):
    """Raised when a ref is unknown, stale or malformed.

    The message is written for a model to read: it says what was wrong *and*
    what to do next, so the agent can recover in one turn.
    """

    def __init__(self, message: str, exit_code: int = 3, ref: str = "") -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.ref = ref


@dataclass(frozen=True)
class RefParts:
    short: str
    session: str
    number: int


def parse_ref(ref: str) -> RefParts:
    """Split ``@sess:e10`` / ``e10`` into its parts. Raises :class:`RefError`."""
    if ref is None or not str(ref).strip():
        raise RefError(
            f"empty ref; try `{_command()} snapshot` and use the refs it prints",
            ref="",
        )
    text = str(ref).strip()
    match = _SHORT_RE.match(text)
    if not match:
        raise RefError(
            f"'{text}' is not a ref; refs look like e12 or @session:e12 "
            f"(run `{_command()} snapshot` to get them)",
            ref=text,
        )
    return RefParts(
        short=match.group("short"),
        session=match.group("session") or "",
        number=int(match.group("num")),
    )


def short_ref(ref: str) -> str:
    """``@abc:e10`` -> ``e10``; leaves a short ref untouched."""
    return parse_ref(ref).short


def full_ref(ref: str, session: str = "") -> str:
    """``e10`` + session -> ``@abc:e10``; keeps an already-full ref."""
    parts = parse_ref(ref)
    sess = parts.session or session
    if not sess:
        raise RefError(f"cannot build a full ref for '{ref}' without a session id", ref=ref)
    return f"@{sess}:{parts.short}"


def make_ref(session: str, number: int) -> str:
    return f"@{session}:e{number}"


def sort_refs_numerically(refs: Iterable[str]) -> list[str]:
    def key(ref: str):
        m = re.search(r"e(\d+)$", str(ref))
        return (0, int(m.group(1))) if m else (1, str(ref))

    return sorted(refs, key=key)


def validate_ref(
    ref: str,
    snapshot_number: int,
    session: str = "",
    snapshot: Optional[Snapshot] = None,
) -> str:
    """Validate ``ref`` against the current snapshot; return the short ref.

    Raises :class:`RefError` (exit code 3) for:
      * malformed refs
      * refs from another session
      * refs from an older snapshot (stale: the tree changed under you)
      * refs that do not exist in the snapshot (when one is supplied)
    """
    parts = parse_ref(ref)
    if session and parts.session and parts.session != session:
        raise RefError(
            f"ref '{ref}' belongs to session '{parts.session}', the current session "
            f"is '{session}'; refs are not portable across sessions - take a fresh snapshot",
            ref=ref,
        )
    if snapshot is not None:
        current = snapshot.number
        current_session = session or snapshot.session
    else:
        current = snapshot_number
        current_session = session
    if snapshot_number and current and snapshot_number != current:
        raise RefError(
            f"ref '{ref}' is stale: it was taken at snapshot {snapshot_number}, the "
            f"current snapshot is number {current}; the tree changed since then - "
            f"run `{_command()} snapshot` again and use the new refs",
            ref=ref,
        )
    if snapshot is not None:
        if snapshot.find(parts.short) is None:
            raise RefError(
                f"unknown ref '{ref}' in snapshot {current} (session {current_session}); "
                "it may have been collapsed by the line cap - try "
                f"`{_command()} snapshot --full` or `{_command()} find \"<name>\"`",
                ref=ref,
            )
    return parts.short


class RefRegistry:
    """Assigns short refs from the raw tree and remembers where they came from.

    Refs are copied from the raw dump, never invented (R8): the reader supplies
    them, this registry only guarantees uniqueness and can hand back the
    original provider ref for the executor.
    """

    def __init__(self, used: Sequence[str] = ()) -> None:
        self._by_short: dict[str, str] = {}
        self._used: set[str] = set(used)
        self._next = 1

    def register(self, raw_ref: str, short: str = "") -> str:
        short = short or ""
        if short and short not in self._used:
            self._used.add(short)
            self._by_short[short] = raw_ref
            return short
        if raw_ref and raw_ref in self._by_short:
            return raw_ref
        while True:
            candidate = f"e{self._next}"
            self._next += 1
            if candidate not in self._used:
                break
        self._used.add(candidate)
        self._by_short[candidate] = raw_ref
        return candidate

    def raw_of(self, short: str) -> str:
        return self._by_short.get(short, "")

    def __len__(self) -> int:
        return len(self._by_short)
