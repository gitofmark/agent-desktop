#!/usr/bin/env python3
"""Size check: the compact snapshot must be much smaller than the raw dump.

Measured with two metrics that are both fair to the raw format:

* characters - the primary metric: what the model pays for, before tokenising
* ~tokens    - ``len(text) / 4``, the usual rough estimate

Both must clear the 50% target on every fixture.  Word counts are printed for
information only: the raw dump is pipe-delimited, so counting words would
flatter the wrong format (a whole element is one "word").

Usage::

    python tools/token_count.py                 # every fixture
    python tools/token_count.py --files a b     # specific captures
    python tools/token_count.py --json          # machine output

Exit code is 0 when every file is at least ``--target`` (default 0.50) smaller,
1 otherwise, so it can be wired into CI.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from snapshot_parser.reader import read_snapshot  # noqa: E402
from snapshot_parser.render import RenderOptions, render_snapshot  # noqa: E402

DEFAULT_TARGET = 0.50


def approx_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))


def measure(raw_text: str, compact: str) -> dict:
    raw_chars, compact_chars = len(raw_text), len(compact)
    raw_tokens, compact_tokens = approx_tokens(raw_text), approx_tokens(compact)
    raw_words, compact_words = word_count(raw_text), word_count(compact)
    return {
        "raw_chars": raw_chars,
        "compact_chars": compact_chars,
        "char_reduction": round(1 - compact_chars / raw_chars, 4) if raw_chars else 0.0,
        "raw_words": raw_words,
        "compact_words": compact_words,
        "word_reduction": round(1 - compact_words / raw_words, 4) if raw_words else 0.0,
        "raw_tokens": raw_tokens,
        "compact_tokens": compact_tokens,
        "token_reduction": round(1 - compact_tokens / raw_tokens, 4) if raw_tokens else 0.0,
        "raw_lines": len(raw_text.splitlines()),
        "compact_lines": len(compact.splitlines()),
    }


PASS_KEYS = ("char_reduction", "token_reduction")


def report_row(name: str, data: dict, target: float) -> str:
    ok = all(data[key] >= target for key in PASS_KEYS)
    mark = "ok  " if ok else "FAIL"
    return (
        f"{mark} {name:<28}"
        f"{data['raw_chars']:>7} -> {data['compact_chars']:>5} chars "
        f"({data['char_reduction'] * 100:5.1f}% smaller)  "
        f"~{data['raw_tokens']:>5} -> {data['compact_tokens']:>4} tokens "
        f"({data['token_reduction'] * 100:5.1f}%)  "
        f"{data['raw_words']:>3} -> {data['compact_words']:>3} words"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser.add_argument("--files", nargs="*", default=None,
                        help="raw captures to check (default: tests/fixtures/*.txt)")
    parser.add_argument("--target", type=float, default=DEFAULT_TARGET,
                        help="minimum reduction, 0.5 = 50%% smaller (default)")
    parser.add_argument("--json", action="store_true", help="machine readable output")
    parser.add_argument("--ascii", action="store_true", help="plain separators")
    args = parser.parse_args(argv)

    files = args.files
    if not files:
        fixture_dir = os.path.join(root, "tests", "fixtures")
        files = sorted(
            os.path.join(fixture_dir, name)
            for name in os.listdir(fixture_dir)
            if name.endswith(".txt")
        )
    if not files:
        print("no raw captures found", file=sys.stderr)
        return 1

    rows = []
    ok_all = True
    for path in files:
        with open(path, "r", encoding="utf-8") as handle:
            raw_text = handle.read()
        try:
            snap = read_snapshot(path)
        except Exception as exc:  # readable, never a traceback
            print(f"FAIL {os.path.basename(path)}: {exc}", file=sys.stderr)
            ok_all = False
            continue
        compact = render_snapshot(snap, RenderOptions(ascii=args.ascii))
        data = measure(raw_text, compact)
        data["file"] = path
        data["ok"] = all(data[key] >= args.target for key in PASS_KEYS)
        ok_all = ok_all and data["ok"]
        rows.append((os.path.basename(path), data))

    if args.json:
        print(json.dumps({"target": args.target, "ok": ok_all,
                          "results": [dict(name=name, **data) for name, data in rows]},
                         indent=2))
        return 0 if ok_all else 1

    print(f"compact snapshot size check (target: >= {args.target * 100:.0f}% smaller)")
    for name, data in rows:
        print(report_row(name, data, args.target))
    print()
    if ok_all:
        print("PASS: every capture is at least half the size after parsing")
        return 0
    print("FAIL: at least one capture did not shrink enough", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
