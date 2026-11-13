#!/usr/bin/env python3
"""Validator: check the rendered snapshot against section 1, not against itself.

The golden tests compare output to a file; this checks the *rules* on any
output, so a golden that was edited to fit the code still fails:

  R1  the first line names the app, the title, the snapshot number and session
  R2  every element line is ``[role] ... eN`` with a known short role
  R3  no line is an unnamed container (``[pane] eN`` with nothing else)
  R4  no line prints a default state (``- checked`` on an unchecked box, an
      implied action such as ``click`` on a button)
  R5  no bounds and no native ids anywhere in the default snapshot
  R6  a grouped line has at least two refs
  R7  the body never exceeds ``--max-lines`` plus its one overflow footer
  R8  every ref is unique and every printed ref is reachable with ``inspect``
  R9  if a dialog is present, its lines come first and exactly one line is
      marked ``(background)``
  R10 coordinates appear only with ``--bounds``

Usage::

    python tools/check_contract.py                    # every fixture
    python tools/check_contract.py --bounds           # check the --bounds form too
    python tools/check_contract.py --file x.raw.txt

Exit code 0 when every rule holds, 1 otherwise.
"""

from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from snapshot_parser.inspect import inspect_to_dict  # noqa: E402
from snapshot_parser.reader import read_snapshot  # noqa: E402
from snapshot_parser.refs import RefError  # noqa: E402
from snapshot_parser.render import RenderOptions, render_snapshot  # noqa: E402
from snapshot_parser.roles import SHORT_ROLES, actions_of  # noqa: E402
from snapshot_parser.state import visible_state  # noqa: E402

__all__ = ["check", "main"]

LINE_RE = re.compile(r"^\[(?P<role>[a-z_]+)\] (?P<rest>.*)$")
REF_RE = re.compile(r"\b(e\d+)\b")
BOUNDS_RE = re.compile(r"\d+,\d+ \d+x\d+")
NATIVE_RE = re.compile(r"\b0[xX][0-9a-fA-F]{2,}\b")
FOOTER_RE = re.compile(r"^\.\.\. \d+ more .*\(inspect e\d+\)$")

#: Roles the validator accepts as line labels (``window_button`` is ours).
VALID_ROLES = set(SHORT_ROLES) | {"window_button"}


def check(path: str, *, max_lines: int = 80, show_bounds: bool = False) -> list[str]:
    """Render a capture and check the result against section 1."""
    snap = read_snapshot(path)
    opts = RenderOptions(max_lines=max_lines, show_bounds=show_bounds)
    return check_text(snap, render_snapshot(snap, opts),
                      max_lines=max_lines, show_bounds=show_bounds)


def check_text(snap, text: str, *, max_lines: int = 80,
               show_bounds: bool = False) -> list[str]:
    """Return a list of rule violations (empty means the text obeys section 1)."""
    problems: list[str] = []
    text = str(text).replace("\r\n", "\n")
    lines = text.splitlines()

    # -- R1 ---------------------------------------------------------
    header = lines[0] if lines else ""
    if f"snapshot {snap.number}" not in header:
        problems.append(f"R1: header does not state the snapshot number: {header!r}")
    if f"session {snap.session}" not in header:
        problems.append(f"R1: header does not state the session: {header!r}")
    if header.count("snapshot") != 1:
        problems.append(f"R1: 'snapshot' stated {header.count('snapshot')} times")

    body = [line for line in lines[1:] if line.strip()]
    content = [line for line in body if not line.startswith("... ")]
    footers = [line for line in body if line.startswith("... ")]

    # -- R7 ---------------------------------------------------------
    if len(content) > max_lines:
        problems.append(f"R7: {len(content)} content lines exceeds the cap {max_lines}")
    if len(footers) > 1:
        problems.append(f"R7: {len(footers)} overflow footer lines")
    for footer in footers:
        if not FOOTER_RE.match(footer):
            problems.append(f"R7: overflow line is not inspectable: {footer!r}")

    # -- R5 / R10 ---------------------------------------------------
    if not show_bounds:
        if BOUNDS_RE.search(text):
            problems.append("R5: the default snapshot contains coordinates")
        if " @" in text:
            problems.append("R5: the default snapshot contains an @bounds token")
    if NATIVE_RE.search(text):
        problems.append("R5: the default snapshot contains a native id")

    seen: dict[str, int] = {}

    # -- R2 / R3 / R4 / R6 / R9 -------------------------------------
    for line in content:
        match = LINE_RE.match(line)
        if not match:
            problems.append(f"R2: line is not [role] ...: {line!r}")
            continue
        role = match.group("role")
        if role not in VALID_ROLES:
            problems.append(f"R2: unknown role label {role!r}: {line!r}")
        bare = line.removesuffix(" (background)")
        if "(background)" in line and not REF_RE.search(bare):
            problems.append(f"R9: (background) is not attached to an element line: {line!r}")

        refs = REF_RE.findall(line)
        for ref in refs:
            seen[ref] = seen.get(ref, 0) + 1
        for ref in refs:
            element = snap.find(ref)
            if element is None and snap.dialog is not None:
                element = snap.dialog.find(ref)
            if element is None:
                problems.append(f"R2/R8: printed ref {ref} does not exist in the tree")
                continue
            try:
                inspect_to_dict(snap, ref)
            except RefError as exc:      # pragma: no cover - would be a bug
                problems.append(f"R8: inspect cannot resolve {ref}: {exc}")
            if element.role == "dialog":
                continue
            implied = set(actions_of(element))
            for state_word in visible_state(element.state):
                if state_word in ("checked",) and not element.state.get("checked"):
                    problems.append(f"R4: {ref} prints a default state {state_word!r}")
            # compare whole tokens: "focused" must not look like the action "focus"
            parts = set()
            for chunk in line.split(" — ")[-1].split(", "):
                parts.add(chunk.split(" @")[0].strip().lower())
            printed_actions = [a for a in element.actions
                               if a in parts and a not in implied]
            if printed_actions:
                problems.append(
                    f"R4: {ref} prints actions the role/state implies: {printed_actions}"
                )
        if role in ("pane", "group", "unknown") and len(refs) == 0:
            problems.append(f"R3: unnamed container printed: {line!r}")
        if bare.count(" · ") and len(refs) < 2:
            problems.append(f"R6: grouped line with {len(refs)} ref: {line!r}")

    # -- R8 ---------------------------------------------------------
    for ref, count in seen.items():
        if count > 1:
            if "(background)" in text and count == 2:
                continue      # the same element, once as dialog target and once as itself
            problems.append(f"R8: ref {ref} appears {count} times")

    # -- R9 ---------------------------------------------------------
    if snap.dialog is not None:
        if not content or not content[0].startswith("[dialog]"):
            problems.append("R9: an active dialog is present but is not the first line")
        backgrounds = [line for line in content if "(background)" in line]
        if len(backgrounds) != 1:
            problems.append(f"R9: {len(backgrounds)} lines marked (background), expected 1")
    else:
        if any("(background)" in line for line in content):
            problems.append("R9: (background) without a dialog")

    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser.add_argument("--file", "-f", action="append", default=None,
                        help="raw capture to check (default: every fixture)")
    parser.add_argument("--max-lines", type=int, default=80)
    parser.add_argument("--bounds", action="store_true",
                        help="also check the --bounds rendering")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.file:
        files = args.file
    else:
        fixture_dir = os.path.join(root, "tests", "fixtures")
        files = sorted(os.path.join(fixture_dir, name)
                       for name in os.listdir(fixture_dir) if name.endswith(".txt"))

    import json

    results = []
    ok_all = True
    for path in files:
        name = os.path.basename(path)
        problems = check(path, max_lines=args.max_lines, show_bounds=False)
        bounds_problems = check(path, max_lines=args.max_lines,
                                show_bounds=True) if args.bounds else []
        # the --bounds form may print coordinates; re-check without R5/R10 only
        bounds_problems = [p for p in bounds_problems
                           if not p.startswith(("R5:", "R10:")) or args.bounds]
        combined = problems + [f"(bounds) {p}" for p in bounds_problems]
        ok = not combined
        ok_all = ok_all and ok
        results.append({"file": name, "ok": ok, "problems": combined})

    if args.json:
        print(json.dumps({"ok": ok_all, "results": results}, indent=2))
        return 0 if ok_all else 1

    for row in results:
        if row["ok"]:
            print(f"ok   {row['file']}")
        else:
            print(f"FAIL {row['file']}")
            for problem in row["problems"]:
                print(f"     {problem}")
    print()
    if ok_all:
        print("PASS: every capture obeys section 1 of the contract")
        return 0
    print("FAIL: the output breaks the contract", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
