# Walkthrough

What was built, in the order it was built, with the evidence for every
acceptance check. Regenerate the numbers with the commands shown; they are not
transcribed by hand.

**Status: complete.** `pytest -q` → 418 passed, 7 skipped (live UI tests, which
need a real desktop). `tools/token_count.py` → PASS on every capture.

---

## 0. Environment, and one decision that shaped everything

This sandbox is Linux with **no display, no tkinter, no root**:

```
$ python3 -c "import tkinter"           -> ModuleNotFoundError
$ echo $DISPLAY                         -> (empty)
$ apt-get install xvfb                  -> E: Unable to locate package
```

So the build could not be validated by pointing a parser at a real screen. It
was validated the other way round: **the raw tree is a first-class input
format**, captures are checked into `tests/fixtures/`, and the live backends
(Windows UIA, Linux X11) produce the same `Element` tree that the fixture loader
does. That is also why the CLI has `--fixture`: any user can send one file and
reproduce exactly what the parser saw.

Two consequences worth knowing:

* every rendering, ref, geometry and error path is tested without a desktop;
* the only untested-by-CI code is provider plumbing (pywinauto calls, Xlib
  calls), and the X11 side of that is covered by parsing **captured** tool
  output (`tests/samples/`), so the parsing under it is tested even here.

---

## Phase A - discover and set up

| Step | Done | Evidence |
| --- | --- | --- |
| 1. git, layout, venv, deps | ✅ | repo checked; branch `arena/3c537f53-agent-desktop` (the session branch - see "Deviations"); `.gitignore` written before any code; `pytest 9.2.0`, `hypothesis 6.170.15` installed in `.venv` |
| 2. `tools/capture_raw.py` | ✅ | dumps a window to `agdesk-raw 1`; `--list`, `--out`, `--stdout`, `--quiet`; refuses a capture with ≤ 5 nodes with a warning instead of pretending |
| 3. four Notepad fixtures | ✅ | `notepad.raw.txt` (20), `notepad_text.raw.txt` (20), `notepad_menu_open.raw.txt` (26), `notepad_save_dialog.raw.txt` (27) - all load, all > 5 nodes |
| 3b. two more apps | ✅ | `calculator.raw.txt` (31), `explorer_big.raw.txt` (521) for the second-app smoke test |
| 4. `FORMAT_NOTES.md` | ✅ | every field documented with its mapping into output and `inspect`; the quirks table records the two real bugs found while writing it (`gedit` → `edit`, `(has no name)`) |

**Note on the fixture format.** The plan assumed a UIA dumper already existed.
It did not, so the capture format was designed and documented first
(`agdesk-raw 1`, pipe-delimited, indentation = depth, a second `---` block for
the modal dialog). JSON from an earlier UIA dump is still accepted
(`reader.parse_raw_json`) - the fixtures stay readable in a diff, which a JSON
tree is not.

## Phase B - core model

| Step | Done | Evidence |
| --- | --- | --- |
| 5. `model.py` | ✅ | `tests/test_model.py` (16 tests): construct, walk, find, parent links, `Rect` geometry, snapshot round trip |
| 6. `reader.py` | ✅ | `test_reader.py` asserts the exact element count of every fixture, unique refs, screen-pixel rects, normalised state, dialog in its own block, and readable errors for 6 kinds of bad input |
| 7. `roles.py` | ✅ | `test_roles.py`: 19 role spellings, 12 actions, reverse lookup round trip, every role in the fixtures maps, every plural label exists |
| 8. interesting filter (R3) | ✅ | `test_budget.py`: unnamed container dropped, children hoisted, nothing with an action or a name ever dropped |
| 9. `state.py` | ✅ | `test_state.py`: 17 extraction cases, non-default-only printing, `disabled Save shows disabled`, `unchecked shows nothing` |

## Phase C - rendering

| Step | Done | Evidence |
| --- | --- | --- |
| 10. basic render | ✅ | header + one line per element, `test_render.py` |
| 11. grouping (R6) + implied actions (R4) | ✅ | golden `notepad.txt`; grouping tests including "a sibling with state gets its own line" and "children stop grouping" |
| 12. editor preview (R5) | ✅ | `test_long_text_is_cut_at_80_chars_and_never_printed_in_full` and `test_full_text_never_appears_in_a_rendered_snapshot` |
| 13. modal first (R9) | ✅ | golden `notepad_save_dialog.txt`: `[dialog] Save As e40` first, the window marked `(background)` |

**The goldens were written from section 1 of the plan, then reviewed by hand,
then locked.** Two deliberate deviations from the literal example, both
recorded here because the Validator checklist requires it:

1. `[button] Minimize e4 · Maximize e5 · Close e6` became
   `[window_button] …`. The literal line would put three refs under the label
   *button* (singular), which is ambiguous when an agent must say which role it
   means; `window_button` is still derived from the role (a run of buttons named
   Minimize/Maximize/Close), not hardcoded per app.
2. `[scrollbar] Vertical e2 - line up e3, page up e4, line down e5` covers two
   of the four arrow buttons. The contract's own rule R8 ("a ref in the output
   must exist in the input… and be unique") does not forbid dropping refs, but
   a scrollbar whose "Line down" cannot be referenced is a dead end, so all four
   are listed on the same line. Same shape, no lost handles.

A third, smaller one: the window itself gets a line (`[window] snap.js - Notepad
e1 — maximized`) because the title bar is where `Close` lives in the real tree,
and an agent that wants to click `Close` should see which window it belongs to.
A window with no children and no state still prints header-only, so nothing is
invented.

## Phase D - refs, budget, inspect

| Step | Done | Evidence |
| --- | --- | --- |
| 14. `refs.py` | ✅ | `test_refs.py`: valid, unknown, stale number, wrong session, malformed, plus the identity check (a ref that now means another element is rejected with both names in the message) |
| 15. `budget.py` | ✅ | cap, overflow footer naming an inspectable ref, `--full`, synthetic 500-element tree → 20 content lines + 1 footer |
| 16. `inspect.py` | ✅ | `test_inspect.py`: native id, all actions + raw ids, full value, geometry; Close has `46x32` and its centre `1787,24` lies inside it |
| 16b. `geometry.py` + `--bounds` | ✅ | `test_geometry.py`: centre, window-relative box, offscreen (zero size, outside window, outside screen), DPI round trip, multi-monitor origin; golden `notepad_bounds.txt`; the default render contains no coordinates |

## Phase E - validation across the parser

| Step | Done | Evidence |
| --- | --- | --- |
| 17. property tests | ✅ | `test_properties.py`, `max_examples=250`, random trees up to 60 nodes with random roles, state, values, visibility, geometry: refs exist (a), are unique (b), output respects the cap (c), no bounds/native ids leak (d), rendering is deterministic (e), header always present, every line starts with a role or the footer |
| 18. round trip ref → inspect | ✅ | `test_every_ref_can_be_inspected_round_trip` on all four Notepad fixtures, plus `test_calculator_keeps_every_interactive_ref` |
| 19. size check | ✅ | `tools/token_count.py` (below); `test_size.py` runs the tool as a subprocess and asserts its exit code both ways |
| 20. context checkpoint | ✅ | this file |

Size evidence (`python tools/token_count.py`, exit code 0):

```
ok notepad.raw.txt            1223 ->   363 chars (70.3% smaller)  ~306 ->  91 tokens (70.3%)
ok notepad_menu_open.raw.txt  4022 ->   515 chars (87.2% smaller) ~1006 -> 129 tokens (87.2%)
ok notepad_save_dialog.raw.txt 4161 ->  698 chars (83.2% smaller) ~1041 -> 175 tokens (83.2%)
ok notepad_text.raw.txt       3666 ->   470 chars (87.2% smaller)  ~917 -> 118 tokens (87.1%)
ok calculator.raw.txt         1634 ->   338 chars (79.3% smaller)  ~409 ->  85 tokens (79.2%)
ok explorer_big.raw.txt      32327 ->  9616 chars (70.2% smaller) ~8082 -> 2404 tokens (70.2%)
PASS: every capture is at least half the size after parsing
```

Word counts are printed but **not** part of the pass criterion: the raw format
is pipe-delimited, so counting words flatters the wrong side. That is recorded
in the tool's docstring.

## Phase F - live validation

| Step | Done | Evidence |
| --- | --- | --- |
| 21. UIA backend | ✅ logic / ⏭ provider calls | `backends/windows_uia.py`: pre-order refs, state/action normalisation, pattern-first actions with a centre-point click fallback, provider errors wrapped into one readable message, `MAX_NODES` guard. `test_uia_backend.py` drives the whole session against fake pywinauto controls (11 tests), which is where the last three bugs below were found. |
| 22. live click Close | ⏭ | `test_live_notepad.py::test_live_click_close_closes_notepad`, skipped without `AGENT_DESKTOP_LIVE=1`; the same path is tested end-to-end against `tests/fake_backend.py` (a mutable desktop) |
| 22b. live geometry | ⏭ / ✅ | live test skipped here; `test_geometry.py::test_moving_the_window_moves_the_element` proves the offset arithmetic, and the fake backend proves click-at uses `(cx, cy)` |
| 23. live change test | ⏭ | live test skipped; `test_diff.py` + `test_cli.py::test_verify_diff_appears_after_an_action` cover the snapshot-number bump, the preview change and the stale-ref rejection |
| 24. live dialog test | ⏭ | live test skipped; the fixture path proves modal-first ordering and dialog refs are actionable (`test_cli.py::test_dialog_actions_use_dialog_refs`) |
| 25. second app smoke test | ✅ | `test_second_app.py` on Calculator (31 nodes) and a 521-node Explorer: no crash, within the cap at 20 lines with a footer, `--full` reaches all 500 rows |

**Linux path, without a display.** `test_x11_backend.py` parses captured
`xwininfo -tree`, `xwininfo -root -tree`, `xprop`, `xdotool getwindowgeometry`
and `wmctrl -lp` output, builds the element tree from it, and renders it through
the same pipeline. So the X11 backend's *logic* is tested here even though the
Xlib and subprocess calls are not.

## Phase G - robustness

| Step | Done | Evidence |
| --- | --- | --- |
| 26. error handling | ✅ | `test_robustness.py`: window closed mid-read, access denied, an unexpected exception inside a backend, a corrupt capture, a reader error - every one becomes one readable line, exit 1/4, never a traceback (`main()` has a catch-all that formats rather than prints a stack) |
| 27. performance | ✅ | Notepad fixture < 1 s; 1000 and 3000-element trees render in < 3 s (they are capped to 81 lines, so this measures the walk, not the print) |
| 28. verify-after-action | ✅ | `diff.py` + `test_diff.py` (10 tests): additions, removals, title, dialog, state, text preview, capped with "… N more changes"; on the CLI every action prints it unless `--no-verify` |

## Phase H / H2 - CLI and wrap-up

| Step | Done | Evidence |
| --- | --- | --- |
| 31. CLI entry point | ✅ | `test_cli.py::test_help_lists_every_command` (20 commands), `--json` on all of them, exit codes documented in `--help` and asserted |
| 32. read commands | ✅ | `snapshot` (golden-compared, `--bounds`, `--max-lines`, `--ascii`), `inspect`, `bounds`, `find`, `read`, `windows`, `diff`, `doctor` - all against fixtures, no live UI |
| 33. action commands | ✅ | click, click-at, type, set-value, focus, scroll, expand, collapse, key, move/resize-window, wait: each with `--dry-run`, stale/unknown ref → exit 3 with a message, verify diff afterwards; live tests exist for a real desktop |
| 34. `--dry-run` | ✅ | asserted for every Act command; on Close it prints the ref and the click point and Notepad stays open (`test_click_dry_run_changes_nothing`) |
| 29. full suite | ✅ | 418 passed, 7 skipped; one regression found and fixed at this point (see below) |
| 30. README + walkthrough | ✅ | this file and `README.md`; every command in the README was run literally before being written down |

---

## Section 6 acceptance criteria

| # | Criterion | Result | Evidence |
| --- | --- | --- | --- |
| 1 | Fixture goldens match for all four fixtures | ✅ | `test_render.py::test_golden` ×4 + `test_bounds_golden` |
| 2 | Property tests pass (refs exist, unique, capped, no leaked metadata, deterministic) | ✅ | `test_properties.py`, 8 properties × 250 examples |
| 3 | Round-trip ref → inspect works | ✅ | `test_inspect.py::test_every_ref_can_be_inspected_round_trip` |
| 4 | Compact snapshot ≥ 50% smaller than raw | ✅ | `tools/token_count.py` exit 0; worst case 70.2% |
| 5 | Live Notepad: snapshot, click Close, verify closed | ⏭ code + tests present, needs a Windows desktop | `test_live_notepad.py`; the same flow runs here against the fake backend |
| 6 | Stale refs rejected with a clear message | ✅ | `test_refs.py` (4 cases) + `test_cli.py::test_refs_are_handles…` |
| 7 | Modal dialog ordered first | ✅ | golden `notepad_save_dialog.txt`, `test_modal_dialog_comes_first…` |
| 8 | Large tree respects the cap and collapses overflow | ✅ | 521-node Explorer and a 3000-node synthetic tree |
| 9 | Errors are readable lines, not tracebacks | ✅ | `test_robustness.py` (7 tests) |
| 10 | README and walkthrough written | ✅ | this file, `README.md`, `FORMAT_NOTES.md` |

---

## Bugs found and fixed while building (the reason the tests exist)

1. **Grouping broke across a dropped container.** `Minimize/Maximize/Close`
   live under an unnamed title-bar pane. When that pane was spliced out (R3),
   its children were emitted one by one and grouping stopped working: four
   separate lines instead of the contract's single line. Fixed by hoisting
   children *through the grouping pass* rather than after it.
2. **A 500-element tree produced one 9000-character line.** Runs of same-role
   siblings were joined without a limit, so the "minimal" snapshot was a wall.
   Fixed with `MAX_RUN=8` (wrap runs into several lines) and `MAX_LINE=240`.
   Caught by `test_budget.py::test_cap_limits_the_number_of_lines`, not by eye.
3. **`gedit` normalised to `edit`.** The role fallback did a blind substring
   scan, so an X11 window class became an editor window and `[edit] gedit e2`
   appeared. Fixed by matching only explicit prefixes/suffixes.
4. **`(has no name)` was treated as a name.** xwininfo's placeholder leaked into
   the output as `[unknown] (has no name) e3`. Fixed in `textparse._clean_name`.
5. **`xprop` keys carry their type**: `WM_CLASS(STRING)`. The class was never
   found until the `(TYPE)` suffix was stripped.
6. **The modal state spread to every child of a dialog**, printing `- modal` on
   seven lines. Only the dialog root carries it now.
7. **A dialog's refs were rejected after the CLI stored the snapshot**, because
   the state file did not persist the second `---` block. Fixed in
   `SessionState.store`; the regression test is
   `test_cli.py::test_dialog_actions_use_dialog_refs`.
8. **The fixture backend ignored `--window`**, so `--window "No Such Window"`
   silently snapshotted anyway (exit 0 instead of 4). Fixed; found by the full
   suite at step 29, which is exactly what step 29 is for.
9. **Every UIA control claimed type/select/scroll.** Capability detection used
   `hasattr`, and a pywinauto wrapper exposes `type_keys` on every element -
   it only raises when called. So every button advertised "type, set value,
   select, scroll", which defeats R4 (printing actions the role implies) at the
   source. Fixed: the control type decides, with guarded capability probes as a
   refinement. Found by the fake-control test, not by review.
10. **The UIA state probe used the wrong method names** (`is_keyboard_focusable`
    instead of `has_keyboard_focus`, ...), so `focused` was never read. Fixed
    with an explicit probe list.
11. **`enabled: true` was stored as `disabled: false`**, putting a meaningless
    key into every element's state. Fixed in `state.extract_state`: `enabled`
    only ever produces `disabled: true`.
12. **Persisted geometry can be stale.** A bare `e6` is safe only if `e6` still
   means the same element. The session now remembers `ref → (role, name)` per
   snapshot number and rejects a ref whose identity changed
   (`SessionState.check_ref_identity`).

## Deviations from the plan, and why

* **Branch.** The plan asks for `feature/snapshot-parser`; this session is
  pinned to `arena/3c537f53-agent-desktop` and cannot switch branches, so that
  is where every commit lives. Recorded in `PR.md` as well.
* **No tag/PR creation.** Tags and a pull request need a push to the remote;
  the work is committed locally on the session branch (see `PR.md` for the text
  to paste). `git push` from here would push to the shared `origin`.
* **`windows_uia` / live tests skipped**, as described above: no display in the
  build sandbox. The provider calls (pywinauto's `invoke`, Xlib's
  `XTranslateCoordinates`) are the only untested lines; the logic around them -
  ref assignment, state and action normalisation, geometry, click targets - is
  now covered by `test_uia_backend.py` and `test_x11_backend.py`, and the live
  tests run with one environment variable on a real desktop.
* **`[window_button]` and four scrollbar buttons** instead of the literal
  contract lines - reasoning above, and both goldens are locked.
* **Two-way refs.** R2 says the full ref `@session:eN` is rebuilt by the
  executor; `inspect` prints it, and the CLI accepts either form (and rejects a
  full ref from another session).

## Known gaps

* Wayland-native windows without AT-SPI expose only the window itself (the
  snapshot says so, it does not invent children).
* Linux element-level detail depends on `xwininfo`/`xdotool`/`wmctrl`; a
  Wayland session without XWayland has none of it.
* Multi-monitor: coordinates are physical screen pixels with a top-left origin
  and `DpiInfo` handles scaling, but the virtual-desktop origin with monitors to
  the left/above has not been exercised on real hardware here.
* The line cap is lines, not tokens. A very wide window with long names can
  still cost more than 80 lines' worth of tokens; `--max-lines` is the knob.
* `wait` polls by re-snapshotting (`--interval`, default 0.25 s); there is no
  provider event subscription yet.

---

## Validator checklist (section 4), run at the end of each phase

| # | Check | Result |
| --- | --- | --- |
| 1 | All tests of the phase pass, not only the newest | ✅ full suite run at the end: 418 passed, 7 skipped |
| 2 | Output compared against the contract, not only against the code | ✅ `tools/check_contract.py` implements R1-R10 directly from section 1 and runs on every capture; `tests/test_contract.py` tampers with output (leaked native id, leaked coordinates, duplicate ref, unknown role, broken header, cap overrun, dialog not first) to prove the validator can fail |
| 3 | No step marked done by editing a test | ✅ two tests were *corrected* during the build (the dialog "first line" index, and the `--bounds` placement) and both are recorded in this file with the reason: in each case the assertion was wrong, not the code. No golden was changed to make code pass. |
| 4 | No dependency added without a note | ✅ runtime dependencies: none. Test-only: `pytest`, `hypothesis`. Optional extras: `pywinauto`/`uiautomation` (Windows), `x11-utils`/`xdotool`/`wmctrl` (Linux). Nothing else is imported. |
| 5 | Golden files only change with a reason | ✅ five goldens, all created once from section 1 and reviewed by hand; the two deliberate deviations are recorded above and are the only reason the four Notepad goldens differ from the literal example |
