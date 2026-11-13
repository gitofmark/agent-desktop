"""Every user-visible name lives here, so a rename is one file.

The project used to be installed as ``agent-desktop``, which collided with an
unrelated npm package of the same name (``github.com/vercel-labs/agent-desktop``
- a Node CLI that also installs a binary called ``agent-desktop``).  Two package
managers writing the same bin path is a silent last-writer-wins race, so the
command was renamed to :data:`PROG`.

The Python *module* is ``snapshot_parser`` and is not part of the rename: no
``import`` line mentions the product name.

Keeping the strings here rather than inline also means the next rename does not
have to touch error messages, help text and docs one literal at a time.
"""

from __future__ import annotations

import os
import re
import sys

#: The command users type. Also the distribution name on PyPI.
PROG = "echo-desktop"

#: Old name, still installed for one release so existing scripts keep working.
#: Deliberately *not* plain ``agent-desktop``: that name is contested by the npm
#: package, and re-installing a script under it would re-create the very
#: collision the rename exists to avoid.
ALIAS = "agent-desktop-py"

#: Version in which :data:`ALIAS` goes away. Shown in the deprecation notice.
ALIAS_REMOVED_IN = "1.1"

#: Lowercase, dot-prefixed directory for session state in the working dir.
STATE_DIR_NAME = ".echo-desktop"

#: Environment variables. Public interface: CI scripts and test runners set these.
ENV_STATE = "ECHO_STATE"
ENV_FIXTURE = "ECHO_FIXTURE"
ENV_LIVE = "ECHO_LIVE"

#: Pre-rename names, still honoured when the new ones are unset.
LEGACY_ENV_STATE = "AGENT_DESKTOP_STATE"
LEGACY_ENV_FIXTURE = "AGENT_DESKTOP_FIXTURE"
LEGACY_ENV_LIVE = "AGENT_DESKTOP_LIVE"

#: Pre-rename state directory, migrated into :data:`STATE_DIR_NAME` on first run.
LEGACY_STATE_DIR_NAME = ".agent-desktop"

#: How this process was invoked, as the path/name the shell resolved. Used so the
#: deprecation notice can name the command the user actually typed.
_EXECUTABLE = ""


def set_executable(name: str) -> None:
    """Record how we were invoked (``argv[0]``); called from the CLI entry points."""
    global _EXECUTABLE
    _EXECUTABLE = name


def invoked_as_alias() -> bool:
    """True when the running process was started through the deprecated name.

    Splits on both separators rather than using :func:`os.path.basename`: a
    ``Windows``-style path is still recognisable when the code is exercised on
    POSIX (and vice versa), which is what the tests do.
    """
    if not _EXECUTABLE:
        return False
    name = re.split(r"[\\/]", _EXECUTABLE)[-1]
    if name.lower().endswith(".exe"):
        name = name[:-4]
    return name == ALIAS


def alias_notice() -> str:
    """The one line printed to stderr when the deprecated alias is used."""
    return (f"note: `{ALIAS}` is now `{PROG}` "
            f"(this alias is removed in {ALIAS_REMOVED_IN})")


def warn_alias() -> None:
    """Print :func:`alias_notice` once, if we were started through the alias."""
    if invoked_as_alias():
        sys.stderr.write(alias_notice() + "\n")


def env(name: str, legacy: str, default: str = "") -> str:
    """Read ``name``, falling back to the pre-rename ``legacy`` name."""
    value = os.environ.get(name, "").strip()
    if value:
        return value
    return os.environ.get(legacy, "").strip() or default


def command() -> str:
    """The invocation string to put in error hints: `echo-desktop`, never the alias.

    Hints always point at the new name, so a user following one is already on the
    supported command rather than the deprecated one.
    """
    return PROG


__all__ = [
    "PROG",
    "ALIAS",
    "ALIAS_REMOVED_IN",
    "STATE_DIR_NAME",
    "LEGACY_STATE_DIR_NAME",
    "ENV_STATE",
    "ENV_FIXTURE",
    "ENV_LIVE",
    "LEGACY_ENV_STATE",
    "LEGACY_ENV_FIXTURE",
    "LEGACY_ENV_LIVE",
    "set_executable",
    "invoked_as_alias",
    "alias_notice",
    "warn_alias",
    "env",
    "command",
]
