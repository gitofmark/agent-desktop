# Migrating from `agent-desktop` to `echo-desktop`

The command was renamed in 1.0.0. Nothing about the snapshot format, the refs, the
exit codes or the JSON payloads changed - only names.

## Why

`agent-desktop` is taken on npm by an unrelated Node package
([`vercel-labs/agent-desktop`](https://github.com/vercel-labs/agent-desktop)) that
installs a binary with the same name:

```
bin: { "agent-desktop": "bin/agent-desktop" }     # npm package
[project.scripts] agent-desktop = "..."            # this package, before the rename
```

Both package managers install into the same `bin` directory and both overwrite
without warning. I reproduced it: installing the second one replaces the first's
launcher outright, exits 0, and prints nothing. Which tool you get depends on
install order - so the same machine can silently switch which `agent-desktop`
runs. Renaming is the only reliable fix; a deprecation *notice* cannot help,
because the notice only prints if our script is the one that got to run.

## What changed

| Before | After |
| --- | --- |
| `pip install agent-desktop` | `pip install echo-desktop` |
| `agent-desktop snapshot` | `echo-desktop snapshot` |
| `$AGENT_DESKTOP_STATE` | `$ECHO_STATE` |
| `$AGENT_DESKTOP_FIXTURE` | `$ECHO_FIXTURE` |
| `$AGENT_DESKTOP_LIVE` | `$ECHO_LIVE` |
| `.agent-desktop/` state dir | `.echo-desktop/` |

Unchanged: the Python module (`snapshot_parser`), every `import`, all commands and
flags, the snapshot text, refs, goldens, exit codes and JSON keys.

## Upgrading

```bash
pip uninstall agent-desktop
pip install echo-desktop
```

For **one release**, the old command name is still installed:

```bash
$ agent-desktop-py snapshot
note: `agent-desktop-py` is now `echo-desktop` (this alias is removed in 1.1)
```

The notice goes to **stderr**; stdout is untouched, so `--json` consumers keep
parsing. Point your scripts at `echo-desktop` before 1.1, when
`agent-desktop-py` is removed.

## Your environment variables

The new names win when both are set; the old names are still read when the new
ones are unset. So an existing CI config keeps working without edits, and moving
to `ECHO_*` is not urgent. Nothing warns you in either direction - if you want to
be sure which one is in effect, check the state directory the tool prints in
`--json` output (`"state_dir"` from `echo-desktop doctor --json`).

## Your state directory

An existing `./.agent-desktop/` is **moved** to `./.echo-desktop/` on first run, so
ref history and the `diff` baseline survive the upgrade. The move only happens
when:

* the new directory does not already exist (never clobbers real state), and
* you have not set `ECHO_STATE`/`AGENT_DESKTOP_STATE` explicitly (an explicit
  path is left exactly as given).

If the move fails - a file lock, a read-only spot - the old directory is left
alone and a fresh `.echo-desktop/` is used. You lose the ref history, not data:
the worst case is `echo-desktop snapshot` again.

## Windows note

The Windows-specific parts did not change: the UIA backend, the `--bounds`
geometry, and the elevated-window message (which now says `echo-desktop`). If you
run the tool elevated, the old name in that message was the only place the rename
touched anything user-visible during an action.
