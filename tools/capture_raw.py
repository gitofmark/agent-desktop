#!/usr/bin/env python3
"""Capture the raw accessibility tree of a window into the native raw format.

Usage::

    python tools/capture_raw.py                        # foreground window
    python tools/capture_raw.py --window "Notepad"     # by title
    python tools/capture_raw.py --out tests/fixtures/notepad.raw.txt
    python tools/capture_raw.py --list                 # what is open
    python tools/capture_raw.py --backend linux-x11 --window gedit

On Windows it uses UI Automation (``pywinauto``); on Linux it uses X11
(``xwininfo``/``xprop``/``wmctrl``).  The output is the ``agdesk-raw 1`` format
that :mod:`snapshot_parser.reader` reads back, and it is exactly what
``agent-desktop`` itself reads, so a capture plus ``--fixture <file>`` gives a
complete offline reproduction of a live problem.

Nothing here needs the parser to succeed: a window with no accessibility
support still produces a valid capture of the window itself.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from snapshot_parser.backends import BackendUnavailable, resolve_backend  # noqa: E402
from snapshot_parser.reader import render_raw_text  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--window", "-w", metavar="TITLE|HWND",
                        help="window to capture (default: the foreground window)")
    parser.add_argument("--out", "-o", metavar="FILE",
                        help="write here (default: ./<app>.raw.txt)")
    parser.add_argument("--backend", default="auto",
                        help="auto, windows-uia, linux-x11 (default: auto)")
    parser.add_argument("--list", action="store_true", help="list windows and exit")
    parser.add_argument("--session", default="capture", help="session id to record")
    parser.add_argument("--number", type=int, default=1, help="snapshot number")
    parser.add_argument("--stdout", action="store_true", help="print instead of writing")
    parser.add_argument("--quiet", "-q", action="store_true", help="print only the path")
    args = parser.parse_args(argv)

    try:
        backend = resolve_backend(args.backend)
    except BackendUnavailable as exc:
        print(f"! {exc}", file=sys.stderr)
        return 1
    ok, why = backend.available()
    if not ok:
        print(f"! {why}", file=sys.stderr)
        return 1

    if args.list:
        for info in backend.windows():
            print(info.line())
        return 0

    try:
        snapshot = backend.snapshot(args.window, session=args.session,
                                    number=args.number)
    except BackendUnavailable as exc:
        print(f"! {exc}", file=sys.stderr)
        return 1
    if snapshot.error:
        print(f"! {snapshot.error}", file=sys.stderr)
        return 1
    if snapshot.root is None:
        print("! the window produced no accessibility tree", file=sys.stderr)
        return 1

    text = render_raw_text(snapshot, include_dialog=snapshot.dialog is not None)
    nodes = snapshot.count() + (snapshot.dialog.count() if snapshot.dialog else 0)
    if nodes <= 5 and not args.quiet:
        print(f"warning: only {nodes} nodes captured; the window may expose no "
              "accessibility information", file=sys.stderr)

    if args.stdout:
        sys.stdout.write(text)
        return 0

    out = args.out
    if not out:
        name = (snapshot.app or "window").lower().replace(" ", "_")
        out = f"{name}.raw.txt"
    directory = os.path.dirname(os.path.abspath(out))
    os.makedirs(directory, exist_ok=True)
    with open(out, "w", encoding="utf-8") as handle:
        handle.write(text)
    if args.quiet:
        print(out)
    else:
        print(f"captured {nodes} nodes from {snapshot.app or '?'} "
              f'"{snapshot.title}" -> {out}')
        print(f"  backend {snapshot.backend}, {len(text.splitlines())} lines, "
              f"{len(text)} chars")
    return 0


if __name__ == "__main__":
    sys.exit(main())
