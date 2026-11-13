"""Short "what changed" diff between two snapshots (Step 28).

After every action the CLI re-snapshots and prints a few lines like::

    + Save dialog e40
    - Close e10
    Text Editor: starts "hello world"
    Focus: Close -> Save

The diff is deliberately conservative: it names at most ``limit`` changes and
summarises the rest, because its job is to let the agent decide whether the
action worked, not to be a complete audit trail (that is what ``--full`` is
for).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .model import Element, Snapshot
from .render import preview_text

__all__ = ["Change", "diff_snapshots", "format_diff", "verify_diff"]

#: How many change lines a verify diff prints before summarising.
DEFAULT_LIMIT = 8


@dataclass
class Change:
    kind: str  # "+", "-", "~"
    text: str

    def __str__(self) -> str:
        return f"{self.kind} {self.text}" if self.kind in ("+", "-") else self.text


def _key(el: Element) -> tuple[str, str]:
    return (el.role, " ".join((el.name or "").split()).lower())


def _label(el: Element) -> str:
    name = " ".join((el.name or "").split())
    text = f"{name} {el.ref}" if name else f"{el.role} {el.ref}"
    return text


def _text_of(el: Element) -> str:
    if el.value is not None:
        return str(el.value)
    return ""


def diff_snapshots(old: Optional[Snapshot], new: Snapshot,
                   limit: int = DEFAULT_LIMIT) -> list[Change]:
    """Compare two snapshots by (role, name) identity."""
    if old is None or old.root is None:
        return [Change("+", "window " + (new.title or new.app or "?"))]
    old_els = old.elements()
    new_els = new.elements()
    old_by_key = {_key(el): el for el in old_els}
    new_by_key = {_key(el): el for el in new_els}

    changes: list[Change] = []

    # the window title itself
    if (old.title or "") != (new.title or ""):
        changes.append(Change("~", f'title: "{old.title}" -> "{new.title}"'))

    if (old.dialog is not None) != (new.dialog is not None):
        if new.dialog is not None:
            changes.append(Change("+", "Save dialog " + new.dialog.ref
                                  if new.dialog.name.lower().startswith("save")
                                  else "+ dialog " + new.dialog.ref))
        else:
            changes.append(Change("-", "dialog " + (old.dialog.ref if old.dialog else "")))

    for key, el in new_by_key.items():
        if key not in old_by_key:
            changes.append(Change("+", _label(el)))

    for key, el in old_by_key.items():
        if key not in new_by_key:
            changes.append(Change("-", _label(el)))

    for key, new_el in new_by_key.items():
        old_el = old_by_key.get(key)
        if old_el is None:
            continue
        old_text, new_text = _text_of(old_el), _text_of(new_el)
        if old_text != new_text:
            label = " ".join((new_el.name or new_el.role).split())
            if new_text:
                changes.append(Change("~", f'{label}: starts "{preview_text(new_text, 40)}"'))
            else:
                changes.append(Change("~", f"{label}: cleared"))
        elif old_el.state != new_el.state:
            old_words = set(old_el.state)
            new_words = set(new_el.state)
            added = sorted(new_words - old_words)
            removed = sorted(old_words - new_words)
            bits = []
            if added:
                bits.append("+".join(added))
            if removed:
                bits.append("-".join(removed))
            changes.append(Change("~", f"{_label(new_el)}: {', '.join(bits)}"))
        elif old_el.name != new_el.name:
            changes.append(Change("~", f'{new_el.role} {old_el.ref}: name -> "{new_el.name}"'))

    return changes


def format_diff(changes: list[Change], limit: int = DEFAULT_LIMIT) -> str:
    """Render a diff, capped, with a count of what was left out."""
    if not changes:
        return "no change"
    shown = changes[:limit]
    lines = [str(change) for change in shown]
    hidden = len(changes) - len(shown)
    if hidden > 0:
        lines.append(f"... {hidden} more changes")
    return "\n".join(lines)


def verify_diff(old: Optional[Snapshot], new: Snapshot,
                limit: int = DEFAULT_LIMIT) -> str:
    return format_diff(diff_snapshots(old, new, limit=limit), limit=limit)
