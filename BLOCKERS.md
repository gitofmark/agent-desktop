# Blockers

None open. Every step is green; this file records how it got there so a future
failure has somewhere to go.

**Current state:** `pytest -q` → 418 passed, 7 skipped. The 7 skipped are the
live-UI tests (`tests/test_live_notepad.py`), which need a real desktop and
`AGENT_DESKTOP_LIVE=1`. `tools/token_count.py` → PASS on all six captures.

## How to use this file

When a step fails three times in a row, write it here in this shape and stop:

```
## Step N - <one line>
- error:   <the actual failure, verbatim>
- tried:   <what was attempted>
- suspect: <where you would look next>
- options: <choices for the user, with the trade-off of each>
```

## Environment limitations found (worked around, not blocking)

| Limitation | Effect | Workaround |
| --- | --- | --- |
| No X display, no tkinter, no root, no `apt` | live UI tests cannot run in this sandbox | fixtures + a mutable fake backend; captured X11 tool output for the Linux parsers |
| `pywinauto` cannot be installed here (Windows only) | the UIA backend is imported lazily and its provider calls are untested here | `test_live_notepad.py` runs it on a Windows box with one env var; everything above the provider is tested |
| Session branch is pinned | no `feature/snapshot-parser` branch, no push | all commits on `arena/3c537f53-agent-desktop`; `PR.md` holds the pull-request text |

## Fixed during the build

Recorded because each one was a real failure that the tests now guard:

1. grouping broke when an unnamed container was spliced out (title bar)
2. a 500-button run produced one ~9000-character line (now `MAX_RUN=8`, `MAX_LINE=240`)
3. `gedit` was normalised to role `edit` by a blind substring match
4. `(has no name)` from xwininfo leaked into the output as a name
5. `xprop` keys carry a `(TYPE)` suffix, so `WM_CLASS` was never found
6. `modal` state spread from a dialog to all of its children
7. the CLI's stored snapshot dropped the modal-dialog block, so dialog refs
   were rejected on the next command
8. the fixture backend ignored `--window`, so an unknown window returned exit 0
9. a bare ref could silently point at a different element after the tree
   changed (now identity-checked per snapshot number)
10. every UIA control claimed type/select/scroll, because `hasattr` always
    succeeds on a pywinauto wrapper (now the control type decides)
11. the UIA state probe used method names the wrapper does not have
12. `enabled: true` was stored as `disabled: false` in every element's state
