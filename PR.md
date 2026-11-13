# Pull request

**Branch:** `arena/3c537f53-agent-desktop` → `main`
**Title:** `snapshot_parser` + `agent-desktop`: minimal natural snapshots for ECHO

---

## What changed

A new package, `snapshot_parser`, and a CLI, `agent-desktop`, that turn a
window's accessibility tree into the minimal natural snapshot described in the
plan, plus actions on the refs that snapshot prints.

```
snapshot_parser/
  model.py       Element / Snapshot / Rect (no I/O)
  reader.py      raw text (agdesk-raw 1) or legacy UIA JSON -> Element tree
  roles.py       provider roles and actions -> a 36-word vocabulary, with reverse lookup
  state.py       raw provider state -> the non-default state worth printing
  render.py      Element tree -> the snapshot text (R1-R10)
  budget.py      relevance filter, line cap, overflow line
  refs.py        short <-> full ref, stale-ref rejection with a recovery hint
  geometry.py    centre point, window-relative box, offscreen, DPI
  inspect.py     the detail the snapshot deliberately omits
  diff.py        verify-after-action diff
  session.py     session id, snapshot counter, last capture on disk
  cli.py         the agent-desktop command (20 commands)
  textparse.py   xwininfo / xprop / xdotool / wmctrl output parsers
  backends/      fixture | windows-uia (pywinauto) | linux-x11 (Xlib + tools)

tools/capture_raw.py     record any window to the raw format
tools/token_count.py     the size check (exits non-zero when the target is missed)
tests/                   407 tests: unit, golden, property, CLI, robustness, live
tests/fixtures/          6 raw captures, including Calculator and a 521-node Explorer
tests/golden/            5 locked renderings
tests/samples/           captured xwininfo/xprop output for the Linux parsers
FORMAT_NOTES.md          raw field -> snapshot field mapping
walkthrough.md           what was built, and the evidence for each acceptance check
README.md                usage, contract, ref rules, limits
```

Nothing in the existing repo was modified except `README.md` (replaced with the
project's) and `pyproject.toml` (new).

## The contract, in one block

```
Notepad — "snap.js - Notepad"  (snapshot 4, session syzqsppsg5kob)

[window] snap.js - Notepad e1 — maximized
[window_button] Minimize e4 · Maximize e5 · Close e6
[menu] File e8 · Edit e9 · Format e10 · View e11 · Help e12
[edit] Text Editor e13 — focused, 2340 chars, starts "const snap = render(snapshot); const lin..."
[scrollbar] Vertical e14 — line up e15, page up e16, page down e17, line down e18
[text] Ln 62, Col 45 e20
```

70-87% smaller than the raw tree on every capture, refs preserved, no bounds or
native ids (available on demand), modal dialog first, overflow collapsed to one
inspectable ref.

## How it was tested

```
$ python -m pytest -q
418 passed, 7 skipped in 11.74s

$ python tools/token_count.py
ok notepad.raw.txt            1223 ->   363 chars (70.3% smaller)
ok notepad_menu_open.raw.txt  4022 ->   515 chars (87.2% smaller)
ok notepad_save_dialog.raw.txt 4161 ->  698 chars (83.2% smaller)
ok notepad_text.raw.txt       3666 ->   470 chars (87.2% smaller)
ok calculator.raw.txt         1634 ->   338 chars (79.3% smaller)
ok explorer_big.raw.txt      32327 ->  9616 chars (70.2% smaller)
PASS: every capture is at least half the size after parsing
```

* **Contract validator** (`tools/check_contract.py`) checks R1-R10 on every
  capture, and `tests/test_contract.py` proves it *rejects* tampered output
  (leaked native id, leaked coordinates, duplicate ref, unknown role, broken
  header, cap overrun, dialog not first): a validator that cannot fail is not a
  validator.
* **Golden tests** lock the exact text of all four Notepad captures plus the
  `--bounds` rendering.
* **Property tests** (hypothesis, 250 examples each) generate random trees and
  assert: every printed ref exists, refs are unique, the cap holds, no bounds or
  native ids leak, rendering is deterministic, every line starts with a role.
* **CLI tests** run every command with `--json` against fixtures and against a
  mutable fake desktop (`tests/fake_backend.py`), including `--dry-run`,
  destructured exit codes (0/1/2/3/4/5) and the verify diff.
* **Robustness tests** cover a window closing mid-read, access denied, an
  unexpected backend exception, a corrupt capture: one readable line, never a
  traceback.
* **Performance**: 3000-element tree renders in under 3 s; the Notepad fixture
  in under 1 s.
* **The 7 skips** are live UI tests. They start their own throwaway editor and
  close it in teardown; run them on a desktop with `AGENT_DESKTOP_LIVE=1 python
  -m pytest tests/test_live_notepad.py`.

## Known gaps

* **Live Windows validation has not run in this environment** (Linux sandbox, no
  display, `pywinauto` is Windows-only). The UIA backend is written and
  reviewed, and the whole session is driven in tests against fake pywinauto
  controls (which found three real bugs: false capability detection, wrong state
  probe names, and `enabled: true` leaking into state). The live tests are ready
  to run on a Windows box. This is the one acceptance check that still needs
  hardware.
* Linux element-level detail depends on `xwininfo`/`xdotool`/`wmctrl`; a Wayland
  app without XWayland or AT-SPI exposes only the window itself. The snapshot
  says so rather than inventing children.
* The line cap counts lines, not tokens.
* Multi-monitor with a negative virtual-desktop origin is handled in
  `geometry.DpiInfo` but not exercised on real hardware here.
* Two deliberate deviations from the literal example in the plan
  (`[window_button]`, and all four scrollbar buttons) are documented in
  `walkthrough.md` with their reasoning.

## Review notes

* **Branch.** The session is pinned to `arena/3c537f53-agent-desktop`, so the
  commits are there rather than on `feature/snapshot-parser`.
* **Commit history** is one commit per validated phase, `step N: <summary>`, so
  it can be reviewed incrementally or squashed.
* **No new runtime dependencies.** `pywinauto` and the X11 tools are optional
  extras; the parser, renderer, refs, geometry, inspect and CLI import nothing
  outside the standard library.
* **Nothing is committed from `.agent-desktop/`** (session state and the last
  capture): it is in `.gitignore`, because it describes one machine's screen.

## Try it in 60 seconds

```bash
pip install -e .
python -m pytest -q                                    # 418 passed, 7 skipped
python tools/check_contract.py                         # R1-R10 on every capture
python tools/token_count.py                            # size check passes

agent-desktop snapshot --fixture tests/fixtures/notepad_save_dialog.raw.txt
agent-desktop inspect e45 --fixture tests/fixtures/notepad_save_dialog.raw.txt
agent-desktop click e45 --dry-run --fixture tests/fixtures/notepad_save_dialog.raw.txt
```

Expected output of the second command:

```
[button] Save e45  @<session>:e45  (snapshot 1)
  state       default
  actions     click
  native id   0x002D
  geometry    x=1180 y=820 w=88 h=32 center=1224,836 window=1180,820
```

On a real desktop: `agent-desktop doctor` → `agent-desktop windows` →
`agent-desktop snapshot` → `agent-desktop click <ref>`.
