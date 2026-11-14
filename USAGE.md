# Using `echo-desktop`

Everything below was run against the installed package, so the output is real.
Line-wrapped for reading; your terminal shows it unwrapped.

## Install

```bash
pip install echo-desktop            # parser, fixture backend, CLI - no dependencies
pip install "echo-desktop[windows]" # + pywinauto/uiautomation for live Windows reading
```

Check what your machine can actually do before anything else:

```bash
$ echo-desktop doctor
echo-desktop 1.0.0 on win32
backend in use: windows-uia
  ok  fixture      ready
  ok  windows-uia  ready
  no  linux-x11    needs Linux
```

If a backend says `no`, the detail line says exactly what to install. `doctor`
works even when nothing is usable - that is the point of it.

## The three things you do

### 1. Look: `snapshot`

```bash
$ echo-desktop snapshot
Notepad — "Untitled - Notepad"  (snapshot 1, session voj2hl4gxa)

[window] Untitled - Notepad e1 — maximized
[window_button] Minimize e4 · Maximize e5 · Close e6
[menu] File e8 · Edit e9 · Format e10 · View e11 · Help e12
[edit] Text Editor e13 — focused, 0 chars
[scrollbar] Vertical e14 — line up e15, page up e16, page down e17, line down e18
[text] Ln 1, Col 1 e20
```

That is the whole interface. Each line is a run of siblings; `e<n>` is a **ref**.
No window title? Pick one:

```bash
echo-desktop windows                          # list them
echo-desktop snapshot --window "Notepad"      # snapshot a specific one
```

`--bounds` adds screen geometry when you need it:

```bash
$ echo-desktop snapshot --bounds
[window_button] Minimize e4 @1660,8 46x32 · Maximize e5 @1712,8 46x32 · Close e6 @1764,8 46x32
```

### 2. Act: use a ref

```bash
$ echo-desktop click e6 --dry-run
dry run: would click e6 at 1787,24 - nothing was changed

$ echo-desktop click e6
Close e6 clicked at 1787,24

no change
```

Every action command has `--dry-run` (prints what it would do, touches nothing) and
runs a **verify snapshot afterwards**, printing `no change` or a diff. Silence here
means "it worked", not "it was skipped".

| Want | Command |
| --- | --- |
| Click | `click e6`, `click e6 --double`, `click e6 --right` |
| Click raw pixels | `click-at 500 300` |
| Type into a field | `type e13 "hello"` |
| Replace content | `set-value e13 "hello"` |
| Keys / shortcuts | `key "ctrl+s"`, `key "{ENTER}"` |
| Scroll | `scroll e14 --down 3` |
| Menus, tree items | `expand e8` / `collapse e8` |
| Move / resize | `move-window 100 100`, `resize-window 800 600` |
| Wait for something | `wait "Saved" --timeout 5` |

### 3. Ask questions: `inspect`, `bounds`, `find`, `read`, `diff`

```bash
$ echo-desktop inspect e6
[button] Close e6  @voj2hl4gxa:e6  (snapshot 1)
  actions     click
  native id   0x0006
  geometry    x=1764 y=8 w=46 h=32 center=1787,24 window=1764,8

$ echo-desktop bounds e6
Close e6 @ 1764,8 46x32  center 1787,24  win 1764,8

$ echo-desktop find "File"
e8 [menuitem] File

$ echo-desktop diff
no change
```

`read` gives the full text of one element - useful because the snapshot truncates
long values on purpose.

## Notepad, end to end

`notepad` groups the operations that a raw click sequence gets wrong. Each one
re-reads the tree, resolves the control by its stable identity, and reports what
it did.

```bash
echo-desktop launch notepad --wait "Notepad"     # start it, wait for the window
echo-desktop notepad type "hello world" --window "Notepad"
echo-desktop notepad read --window "Notepad"     # the full document
echo-desktop notepad set-text "replaced" --window "Notepad"
echo-desktop notepad save-as "C:\notes.txt" --window "Notepad"
echo-desktop notepad font --window "Notepad"     # open Format > Font
echo-desktop notepad set-font --family Consolas --style Bold --size 14
echo-desktop notepad close --window "Notepad"     # answers "Don't save"
echo-desktop notepad close --save --window "Notepad"
```

`focus-window` brings a window forward, which matters because **typed keys go to
whatever has focus** - not to the window you snapshotted:

```bash
echo-desktop focus-window --window "Notepad"
echo-desktop key "ctrl+a"
```

### Why the Font dialog is hard, and what it proves

Notepad's Font dialog has three combo boxes whose children are almost identical:

```
[combobox] Font:        id 1136  → [listbox 'Font:' id 1000] [edit 'Font:' id 1001]
[combobox] Font style:  id 1137  → [listbox 'Font style:' id 1000] [edit 'Font style:' id 1001]
[combobox] Size:        id 1138  → [listbox 'Size:' id 1000] [edit 'Size:' id 1001]
```

The ids **repeat** and the names are **identical**. Matching on name picks one at
random; matching on id alone matches three. So resolution is: automation id, then
role, then the remembered ref - and if that still leaves more than one candidate it
returns nothing rather than guessing. `notepad set-font` drives each field by its
own combo, so "Consolas" cannot land in the Size box.

That is also why the npm tool's `TIMEOUT: strict resolution could not determine
candidate identity` happened, and why the workaround in that session was raw
`mouse-click --xy 176,464` on screen coordinates - which breaks the moment the
window moves.

## The rule that matters: refs are handles, not names

A ref is valid **for the snapshot and session that printed it**. Nothing else.

```bash
$ echo-desktop click e999
! unknown ref 'e999' in snapshot 1 (session ju3r9w2hst); it may have been
  collapsed by the line cap - try `echo-desktop snapshot --full` or
  `echo-desktop find "<name>"`
exit 3
```

That rejection is the feature. A ref that quietly pointed at a different element -
after a window redrew, or on a different machine - would be the worst possible
bug for an agent, so:

* refs are checked for **identity** (role + name), not just existence;
* a ref from another session is refused;
* a ref from an older snapshot is refused with exit code 3.

**Always `snapshot` again after the UI changes.** Do not cache refs across actions
that alter the tree (opening a dialog, typing into a field that filters a list).

## For an agent: `--json` + exit codes

Every command takes `--json`; stdout is pure JSON, so you never parse prose.

```json
{
  "snapshot": 1,
  "session": "voj2hl4gxa",
  "app": "Notepad",
  "title": "Untitled - Notepad",
  "text": "Notepad — \"Untitled - Notepad\"  (snapshot 1, session voj2hl4gxa)\n\n...",
  "elements": 20,
  "lines": 8
}
```

Branch on exit codes instead of string-matching errors:

| Code | Meaning | What to do |
| --- | --- | --- |
| 0 | ok | continue |
| 1 | general error | read stderr; often "no desktop" |
| 2 | bad arguments | fix the call, do not retry |
| 3 | stale or unknown ref | **`snapshot` again**, then re-resolve |
| 4 | window not found | `windows`, pick another title |
| 5 | action failed | element exists but the action did not land |

A typical loop:

```bash
echo-desktop snapshot --json            # read .text, pick a ref
echo-desktop click e6 --dry-run --json  # sanity check the target
echo-desktop click e6 --json            # act
echo-desktop diff --json                # did anything change?
```

Exit 3 is not an error to retry blindly - it means your view of the screen is out
of date, so take a new snapshot.

## From Python

The parser is usable as a library; no CLI involved.

```python
from snapshot_parser import read_snapshot, render_snapshot, inspect_to_dict, find_elements

snap = read_snapshot(r"C:\captures\notepad.raw.txt")
print(render_snapshot(snap))            # the text form

print(inspect_to_dict(snap, "e6"))      # one element, as a dict
# {'ref': 'e6', 'role': 'button', 'name': 'Close', 'actions': ['click'],
#  'geometry': {'x': 1764, 'y': 8, 'width': 46, 'height': 32, 'cx': 1787, ...}}

print(find_elements(snap, "File"))      # [{'ref': 'e8', 'role': 'menuitem', ...}]
```

`find_elements` returns **dicts**, not objects. For a live tree instead of a file,
use the backends directly:

```python
from snapshot_parser.backends.windows_uia import UiaSession

session = UiaSession()
snap = session.snapshot()               # same Snapshot object
print(render_snapshot(snap))
session.click("e6")
session.close()
```

## Working without a desktop (how to develop against it)

This is also how the test suite avoids touching anyone's real windows: capture a
tree once, then replay it forever.

```bash
python tools/capture_raw.py --out notepad.raw.txt     # on a real desktop
echo-desktop snapshot --fixture notepad.raw.txt       # anywhere, any OS
echo-desktop click e6 --fixture notepad.raw.txt       # simulated, nothing real is touched
```

The fixture backend answers `windows`, `snapshot` and every action with a
simulated result, so an agent loop can be developed and tested on CI with no
display at all.

## Browsers and Electron apps (VS Code, Edge, WhatsApp, Slack)

These build their UI with Chromium. Chromium keeps its accessibility tree
**switched off until something asks for it**, so by default the window exposes its
frame and nothing else - you get a snapshot with no useful lines.

Restart the app with accessibility enabled. It has to be at launch: a window that
is already open will not start exposing a tree later.

```bash
code   --force-renderer-accessibility        # VS Code
msedge --force-renderer-accessibility        # Edge
chrome --force-renderer-accessibility        # Chrome
```

For a browser you can instead enable it for the current pages at
`chrome://accessibility` or `edge://accessibility` (no restart, per page).

When the tree is empty for a window that looks like a browser, the snapshot says
so instead of printing a blank header:

```
Code — "README.md - Visual Studio Code"  (snapshot 1, session local)

! no accessibility tree: this is a Chromium/Electron window and renderer
  accessibility is off. Restart it with `code --force-renderer-accessibility`
  (existing windows never enable it later), or turn it on for one page via
  chrome://accessibility / edge://accessibility
```

Windows that work with no extra flags: anything native - Notepad, cmd, Explorer,
Calculator, WinForms/WPF/WinUI apps, Office.

## Big windows (File Explorer, deep trees)

Two things keep a huge tree from making the tool look empty:

* **Zero-size nodes are real.** Windows 11's Explorer reports no area for its
  navigator, command bar and virtualised list rows. Those were being discarded as
  "offscreen", which is why Explorer came back as a header and almost nothing else.
  They are now kept, and `bounds` labels them `no-area` instead of `offscreen`.
* **The read is capped at 4000 nodes**, and says so when it hits the cap:

  ```
  ! tree truncated at 4000 elements - the snapshot shows the first part;
    use `find` or `--window` on a smaller window for the rest
  ```

  Use `find "<name>"` to reach something the cap cut off.

## Common mistakes

| Symptom | Cause | Fix |
| --- | --- | --- |
| `! no fixture selected...` exit 1 | no desktop and no `--fixture` | run on a desktop, or pass `--fixture` |
| `! unknown ref 'e12'` exit 3 | ref collapsed by the 80-line cap | `snapshot --full`, or `find "<name>"` |
| `! ref 'e6' is stale: it was taken at snapshot 1...` | you acted after the tree changed | `snapshot` again, use the new refs |
| `! that window closed` | title matched, then the window went away | `windows`, re-check the title |
| `access denied: ... restart echo-desktop as administrator` | target window is elevated | run elevated too |
| Action prints a diff it did not expect | the action worked but the UI also changed | read the diff; usually fine |
| `echo-desktop` not found after install | scripts dir not on `PATH` | `python -m snapshot_parser.cli ...` works identically |

## Limits worth knowing

* **80 lines** per snapshot; beyond that you get `... 12 more buttons (inspect e40)`.
  Use `find` or `inspect` to reach the collapsed ones.
* Only **non-default state** is printed (`focused`, `disabled`, `checked`...) - absence
  means default, not "unknown". `inspect` shows everything.
* Bounds and native ids stay out of the default output to save tokens; `--bounds`,
  `bounds` and `inspect` are how you ask for them.
* Windows UIA and Linux X11 both feed the same pipeline, so behaviour is the same;
  only which windows can be seen differs.
