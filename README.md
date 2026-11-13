# agent-desktop

**The minimal natural snapshot of a window, and actions on the refs it prints.**

A screen is a bad interface for an agent: an accessibility tree is thousands of
nodes, most of them invisible, and a screenshot costs a fortune in tokens.
`agent-desktop` reads the tree once and prints a short, stable, human-readable
summary that keeps the one thing an agent needs - a **ref** it can act on:

```
$ agent-desktop snapshot

Notepad — "snap.js - Notepad"  (snapshot 4, session syzqsppsg5kob)

[window] snap.js - Notepad e1 — maximized
[window_button] Minimize e4 · Maximize e5 · Close e6
[menu] File e8 · Edit e9 · Format e10 · View e11 · Help e12
[edit] Text Editor e13 — focused, 2340 chars, starts "const snap = render(snapshot); const lines = snap..."
[scrollbar] Vertical e14 — line up e15, page up e16, page down e17, line down e18
[text] Ln 62, Col 45 e20
```

Eleven times smaller than the raw tree on Notepad, ~80% smaller on real apps,
and every ref in it is clickable:

```
$ agent-desktop bounds e6
Close e6 @ 1764,8 46x32  center 1787,24  win 1764,8

$ agent-desktop click e6 --dry-run
dry run: would click e6 at 1787,24 - nothing was changed

$ agent-desktop click e6
clicked Close e6
- Close e6
- snap.js - Notepad e1 (background)
```

Works on **Windows** (UI Automation) and **Linux** (X11), and offline against a
captured tree (`--fixture`) so the whole thing is testable in CI.

---

## Install

```bash
pip install -e .            # the parser, the CLI, no dependencies

# Windows: the real backend
pip install -e ".[windows]"     # pywinauto (uiautomation is the fallback)

# Linux: the real backend needs X tooling (Xvfb works for headless)
sudo apt install x11-utils xdotool wmctrl
```

Python 3.10+. No runtime dependencies for the parser, the renderer or the CLI;
`pywinauto` and the X11 tools are only needed by the live backends.

## Quick start

```bash
agent-desktop doctor          # what this machine can actually read
agent-desktop windows         # open windows, with position and size
agent-desktop snapshot        # the minimal snapshot of the foreground window
agent-desktop snapshot --bounds
agent-desktop inspect e13     # everything the snapshot leaves out
agent-desktop find "save" --role button
agent-desktop click e6
```

Offline, with a capture instead of a desktop:

```bash
python tools/capture_raw.py --window Notepad -o notepad.raw.txt
agent-desktop --fixture notepad.raw.txt snapshot
agent-desktop --fixture notepad.raw.txt click e5 --dry-run
```

## The output contract

| Rule | What it means |
| --- | --- |
| **R1 header** | `App — "title"  (snapshot N, session id)`, stated once |
| **R2 one line per element** | `[role] Name eN`, plus only what matters |
| **R3 relevance** | no name, no action, no value → dropped; children are hoisted so nothing interactive is lost |
| **R4 minimal state** | non-default state only (`focused`, `disabled`, `checked`, `expanded`, `selected`, `maximized`, `modal`); actions only when role/state does not imply them |
| **R5 no noise** | never bounds, native ids, empty properties or full document text; editors get `2340 chars, starts "..."` (≤ 80 chars) |
| **R6 grouping** | same-role siblings share a line: `Minimize e4 · Maximize e5 · Close e6` |
| **R7 budget** | visible elements only, capped (default 80 lines) with `... 12 more buttons (inspect e40)` |
| **R8 refs** | copied verbatim from the tree, unique, never invented |
| **R9 modals** | the active dialog first, the window behind it marked `(background)` |
| **R10 geometry on demand** | `x, y, width, height`, `cx, cy` and window-relative `rx, ry`, printed only with `--bounds` as `Close e10 @ 1842,12 46x32` |

### Ref rules

* A ref is **short** in the output (`e10`) and **full** in the protocol
  (`@syzqsppsg5kob:e10`); the executor rebuilds the full form from the session.
* A ref belongs to one snapshot and one session. After a new snapshot the old
  refs are **stale** and are rejected with exit code 3 and a message that says
  what to do:

  ```
  ! ref 'e6' is stale: it was taken at snapshot 1, the current snapshot is
    number 2; the tree changed since then - run `agent-desktop snapshot` again
  ```

## Commands

All commands accept `--json`, `--window <title|hwnd>`, `--backend <name>`,
`--fixture <file>`, `--no-state`.

**Read**

| Command | What it does |
| --- | --- |
| `windows` | open windows: number, app, title, position, size |
| `snapshot [--bounds] [--max-lines N] [--full] [--ascii]` | the minimal snapshot; increments the snapshot number |
| `inspect <ref>` | role, name, state, native id, all actions, geometry, properties |
| `bounds <ref>` | just the geometry: `x,y WxH center cx,cy win rx,ry offscreen?` |
| `find "<text>" [--role r]` | refs by name (or value) without printing the tree |
| `read <ref>` | the full text/value of one element |
| `diff` | what changed since the previous snapshot |
| `doctor` | which backends and tools this machine has |

**Act** — each checks the ref is current, performs the action, then prints a
short verify diff.

| Command | What it does |
| --- | --- |
| `click <ref> [--double] [--right]` | UIA invoke first, then a click on the center point |
| `click-at <x> <y> [--double] [--right]` | raw screen coordinates |
| `type <ref> "<text>"` | focus the element and type |
| `set-value <ref> "<text>"` | replace the content |
| `focus <ref>` | move focus |
| `scroll <ref> --down N` / `--up N` | scroll |
| `expand <ref>` / `collapse <ref>` | open or close menus and tree items |
| `key "<combo>"` | `ctrl+s`, `alt+f4` |
| `move-window <x> <y>` / `resize-window <w> <h>` | reposition / resize |
| `wait <ref-or-text> --timeout 5` | wait until an element appears |

**Safety**: every Act command takes `--dry-run` (prints the target ref and the
click point, changes nothing) and `--no-verify`. Closing a window, or a
destructive key such as `alt+f4`, prints a one-line warning unless `--yes` is
given.

### Exit codes

| Code | Meaning |
| --- | --- |
| 0 | ok |
| 1 | general error (unreadable window, bad capture) |
| 2 | bad arguments |
| 3 | stale or unknown ref |
| 4 | window not found |
| 5 | action failed |

Errors are always one readable line on stderr (or `{"ok": false, "error": ...}`
with `--json`) - never a traceback.

## Using it as a library

```python
from snapshot_parser import read_snapshot, render_snapshot, inspect_to_dict

snap = read_snapshot("notepad.raw.txt")
print(render_snapshot(snap))                     # the contract above
print(inspect_to_dict(snap, "e6")["geometry"])    # x, y, w, h, cx, cy, rx, ry

from snapshot_parser.backends import resolve_backend
backend = resolve_backend("auto")                 # live desktop, or fixture
snap = backend.snapshot(session="abc", number=1)
el = snap.find("e6")
backend.perform(el, "click", snap)
```

The pieces are independent, which is what makes them testable:

```
model.py     Element / Snapshot / Rect          (no I/O)
reader.py    raw text or JSON -> Element tree
roles.py     provider role/action -> short vocabulary, reverse lookup
state.py     raw state -> non-default state words
render.py    Element tree -> the text (R1-R10)
budget.py    relevance filter, line cap, overflow line
refs.py      short <-> full ref, stale-ref validation
geometry.py  centre point, window-relative box, offscreen, DPI
inspect.py   the details the snapshot omits
diff.py      verify-after-action diff
session.py   session id, snapshot counter, last capture on disk
cli.py       the agent-desktop command
backends/    fixture | windows-uia | linux-x11
```

## Backends

| Backend | Platform | What it gives |
| --- | --- | --- |
| `fixture` | anywhere | a captured tree; actions are simulated (`--dry-run` for free) |
| `windows-uia` | Windows | full UI Automation tree via `pywinauto`, invoke/click fallback through the center point |
| `linux-x11` | Linux | window geometry from Xlib, element tree from `xwininfo`, input from `xdotool`, pids from `wmctrl` |

`auto` (the default) picks the live desktop, then falls back to a fixture if
`--fixture`/`AGENT_DESKTOP_FIXTURE` is set. `agent-desktop doctor` explains what
is missing on this machine, e.g.:

```
backend in use: fixture
  ok  fixture      ready
  no  windows-uia  the UIA backend needs Windows; on Linux use `--backend linux-x11`
  no  linux-x11    no DISPLAY is set: there is no desktop to read
  hint: install xdotool, xwininfo, wmctrl for element-level detail
```

## Limits

* **Linux detail depends on the toolkit.** X11 exposes child windows and class
  hints, which is a good tree for GTK, Qt, tk and CDE/Motif apps, but a
  Wayland-native or fully custom-drawn app may expose only the window itself.
  The snapshot says so instead of pretending; install `pyatspi`/AT-SPI for
  accessible names when a toolkit draws its own controls.
* **Coordinates are physical screen pixels** with a top-left origin. On Windows
  with per-monitor DPI, `geometry.DpiInfo` converts between logical and
  physical; the snapshot always prints the space the click uses.
* **One window per snapshot.** Multi-window apps are snapshotted one window at
  a time; `windows` lists them all.
* **The cap is a line count, not a token count.** `--max-lines` defaults to 80;
  `--full` removes it and includes offscreen elements. Very long names and
  grouped runs are clamped at 240 characters per line.
* **Refs do not survive a new snapshot** by design (R8): an action always
  applies to the tree you last read, never to a guess.
* **Actions are best-effort per platform.** Windows prefers UIA invoke and
  falls back to a physical click on the centre point; X11 clicks. If an action
  cannot be performed, the message says which method failed and why.

## Tests

```bash
python -m pytest -q                      # 350+ tests, no desktop needed
python -m pytest tests/test_live_notepad.py -q   # needs AGENT_DESKTOP_LIVE=1
python tools/token_count.py              # size check, exits non-zero on failure
python tools/capture_raw.py --list       # what can be captured here
```

The live tests start **their own** throwaway editor and close it in teardown;
they never touch a window the user already has open.

## Layout

```
snapshot_parser/     the parser and the CLI
tests/               unit, property, golden, CLI, robustness, live tests
tests/fixtures/      raw captures (the parser's ground truth)
tests/golden/        the locked renderings the contract is checked against
tools/               capture_raw.py (record a window), token_count.py (size check)
FORMAT_NOTES.md      raw field -> snapshot field mapping
walkthrough.md       what was built, and the evidence for each acceptance check
BLOCKERS.md          open problems (empty when the build is green)
```
