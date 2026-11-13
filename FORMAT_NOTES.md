# Raw format notes

Everything the parser reads, and how each raw field becomes part of the
snapshot. Written after inspecting the captures (step 4) and kept in sync with
the code; if a provider surprises us, this file is updated **before** the code.

There are two raw inputs:

| Input | Who writes it | Parser entry point |
| --- | --- | --- |
| `agdesk-raw 1` text | `tools/capture_raw.py`, `echo-desktop` state dir | `reader.parse_raw_text` |
| UIA JSON (legacy) | the first Windows dumper | `reader.parse_raw_json` |

The text format is the primary one: a fixture diff is readable in a PR review,
where a JSON tree is not.

---

## 1. `agdesk-raw 1` text format

```
agdesk-raw 1
app: Notepad                       # app name, shown in the header
title: snap.js - Notepad           # window title, shown in the header
number: 14                         # snapshot number, shown in the header
session: syzqsppsg5kob             # session id, makes refs global
backend: capture-windows           # where the tree came from (informational)
error: the window closed           # optional: a whole-snapshot read error
---
e1|Window|snap.js - Notepad|0,0,1920,1080|SetFocus|Maximized||0x00012A|visible|chars=0
 e2|Pane|||||||visible|
  e3|Button|Close|1764,8,46,32|Invoke||||visible|
---
e40|Dialog|Save As|560,300,800,560|SetFocus|modal||0x0002B1|visible|
 e45|Button|Save|1180,820,88,32|Invoke|default|||visible|
```

* Lines starting with whitespace (and containing `|`) are elements.
* Indentation with spaces is the depth; two spaces per level in the captures.
* `---` separates the header from the window tree. A **second** `---` block is
  the active modal dialog (R9); it is kept as `snapshot.dialog`, not mixed into
  the window tree, so refs stay unambiguous.
* `key: value` lines before the first `---` are header fields.
* `\` escapes `|`, `\n`, `\t` and `\\` inside any field.

### Element fields (10, in order)

| # | field | example | becomes | notes |
| --- | --- | --- | --- | --- |
| 1 | `ref` | `e10` | `Element.ref` | **copied verbatim** (R8), never renumbered by the renderer |
| 2 | `role` | `Button` | `Element.role` via `roles.normalize_role`; raw kept in `raw_role` | UIA `ControlType`, MSAA `ROLE_SYSTEM_*`, AT-SPI `push button` and X11 class hints all normalise here |
| 3 | `name` | `Close` | `Element.name` | the accessible name; empty means "no name", which R3 treats as noise unless there is an action |
| 4 | `x,y,w,h` | `1764,8,46,32` | `Element.rect` (`Rect`) | screen pixels, top-left origin, exactly the space the executor clicks in (R10) |
| 5 | `actions` | `Invoke,SetFocus` | `Element.actions` (`click`, `focus`) and `Element.action_ids` (`{"click": "Invoke"}`) | comma separated raw ids; the reverse map lets an executor call the provider's own pattern |
| 6 | `state` | `Focused,Expanded` or `enabled=false` | `Element.state` via `state.extract_state` | one token per flag, or `key=value`; unknown keys are dropped from the snapshot and kept for `inspect` |
| 7 | `value` | *(text)* | `Element.value` | the full text/value of an editor. **Never printed in the snapshot** (R5) - only a <= 80 char preview plus length |
| 8 | `native_id` | `0x00012A` | `Element.native_id` | HWND / automation id / X11 window id. Printed only by `inspect` |
| 9 | `flags` | `visible` / `hidden` | `Element.visible` | `hidden`/`invisible` excludes the element from the default snapshot (R7) |
| 10 | `props` | `chars=2340;text=Ln 1, Col 1` | `Element.properties` | `key=value` pairs separated by `;`. Special keys: `chars`/`length` (length note), `text` (content when `value` is empty), `modal`, `scroll_amount` |

### Field-by-field mapping into the output

| Raw field | Snapshot line | `inspect` |
| --- | --- | --- |
| role | `[button]` | `role`, `raw_role` |
| name | `Close` | `name` |
| ref | `e10` | `ref`, `full_ref` = `@session:e10` |
| rect | only with `--bounds`: `@1764,8 46x32` | `x y w h cx cy rx ry offscreen` |
| actions | only when the role/state does not imply them | full list + raw ids |
| state | only non-default values, in `STATE_ORDER` | `state` dict + words |
| value | `2340 chars, starts "..."` | full `value` |
| native_id | never | `native_id` |
| flags | controls visibility | `visible` |
| props | `chars`, `text` feed the length/preview | `properties` |

### Element counts (step 6 assertions)

| fixture | elements | note |
| --- | --- | --- |
| `notepad.raw.txt` | 20 | 1 window + 15 nested + 4 status/scrollbar |
| `notepad_text.raw.txt` | 20 | same tree, editor holds 2340 chars |
| `notepad_menu_open.raw.txt` | 26 | File menu expanded with 11 items |
| `notepad_save_dialog.raw.txt` | 27 | 20 window + 7 dialog |
| `calculator.raw.txt` | 31 | second app (step 25) |
| `explorer_big.raw.txt` | 521 | 500-row list, overflow test |

`tests/test_reader.py::test_fixture_element_count` asserts these numbers.

---

## 2. Legacy UIA JSON

```json
{"app": "Calculator", "title": "Calculator",
 "root": {"ref": "e1", "role": "Window", "name": "Calculator",
          "rect": {"x": 0, "y": 0, "width": 400, "height": 600},
          "state": {"focused": true},
          "children": [...]}}
```

* `role` may also be `control_type` / `controlType` / `type` / `class_name`.
* `name` may also be `title` / `text` / `label`.
* `actions` may also be `patterns` / `action_ids`, as a list or a comma string.
* `rect` may also be `bounds` / `rectangle`, as `{x,y,width,height}` or `[x,y,w,h]`.
* `state` may also be `states` / `properties`; when absent, the booleans
  `focused`, `enabled`, `checked`, `expanded`, `selected`, `offscreen`,
  `modal`, `readonly` are read from the node itself. `enabled: false` becomes
  `disabled: true`.
* Refs missing from a node are generated as `e1..eN` in pre-order.

---

## 3. Provider quirks worth remembering

| Quirk | Handling |
| --- | --- |
| UIA reports `IsEnabled=false` | normalised to `disabled` (the thing worth printing) |
| UIA `ToggleState` is `Off/On/Indeterminate` | `checked` + `mixed` |
| `ExpandCollapseState` is an enum | `expanded: true/false`; `collapsed` is the default so it is never printed |
| MSAA `ROLE_SYSTEM_PUSHBUTTON` | `button` (prefix stripping in `roles._norm_key`) |
| X11 class hints are app names (`gedit`) | no blind substring matching: `gedit` must not become `edit`. Only explicit prefixes (`GtkButton`) and suffixes (`...Control`) map |
| xwininfo `(has no name)` | treated as an empty name, not as the text "(has no name)" |
| A dialog's children inherit `modal` | only the dialog root carries `modal`; children do not repeat it |
| A window can close mid-read | every provider call is wrapped; the result is one readable error line, never a traceback |

## 4. What the parser never puts in the default snapshot

bounds, native ids, `runtime_id`, empty properties, full document text, hidden
or offscreen elements, unnamed containers (their children are hoisted instead),
separators with no name.

These are all reachable: geometry and native ids through `inspect`/`bounds`,
text through `read`, hidden elements through `snapshot --full`.
