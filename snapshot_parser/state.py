"""Non-default state extraction (R4).

A snapshot only prints state that is *unusual*: ``focused``, ``checked``,
``disabled``, ``expanded``, ``selected``, ``maximized``, ``read-only``,
``mixed``, ``modal``.  Everything a control has by default is dropped, so the
reader never has to mentally subtract noise.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

__all__ = [
    "STATE_ALIASES",
    "STATE_ORDER",
    "DEFAULT_STATE",
    "extract_state",
    "visible_state",
    "state_suffix",
    "is_offstage",
]

#: canonical state name -> accepted raw spellings (normalised: lower, no _ / -)
STATE_ALIASES: dict[str, tuple[str, ...]] = {
    "focused": ("focused", "focus", "haskeyboardfocus", "keyboardfocus",
                "isfocused", "hasfocus", "active"),
    "checked": ("checked", "ischecked", "checkstate", "togglestate", "toggled", "on"),
    "mixed": ("mixed", "indeterminate", "partiallychecked"),
    "disabled": ("disabled", "isenabledfalse", "unavailable", "insensitive",
                 "notenabled"),
    "expanded": ("expanded", "isexpanded", "expandcollapsestate", "open"),
    "collapsed": ("collapsed", "isexpandedfalse"),
    "enabled": ("isenabled", "enabled"),
    "selected": ("selected", "isselected", "selection", "isselectedtrue"),
    "maximized": ("maximized", "ismaximized", "maximised"),
    "minimized": ("minimized", "minimised"),
    "read-only": ("readonly", "isreadonly", "protected"),
    "invalid": ("invalid", "isinvalid", "hasvalidationerror"),
    "required": ("required", "isrequired"),
    "modal": ("modal", "ismodal"),
    "busy": ("busy", "isbusy", "loading"),
    "pressed": ("pressed", "ispressed"),
    "default": ("default", "isdefault", "defaultbutton"),
    "offscreen": ("offscreen", "isoffscreen", "hidden", "invisible"),
}

#: Canonical print order inside the ``(...)`` blob.
STATE_ORDER: tuple[str, ...] = (
    "modal",
    "focused",
    "checked",
    "mixed",
    "selected",
    "expanded",
    "collapsed",
    "disabled",
    "invalid",
    "required",
    "read-only",
    "pressed",
    "default",
    "maximized",
    "minimized",
    "busy",
    "offscreen",
)

#: The value a state must have to be *worth printing*.
DEFAULT_STATE: dict[str, bool] = {
    "focused": False,
    "checked": False,
    "mixed": False,
    "selected": False,
    "expanded": False,
    "collapsed": True,
    "disabled": False,
    "invalid": False,
    "required": False,
    "read-only": False,
    "pressed": False,
    "default": False,
    "maximized": False,
    "minimized": False,
    "modal": False,
    "busy": False,
    "offscreen": False,
}

_TRUE = {"true", "yes", "1", "on", "checked", "expanded", "selected", "focused",
         "pressed", "disabled", "mixed", "indeterminate", "modal"}
_FALSE = {"false", "no", "0", "off", "unchecked", "collapsed", ""}


def _norm(key: str) -> str:
    return str(key).strip().lower().replace("_", "").replace("-", "").replace(" ", "")


_LOOKUP: dict[str, str] = {}
for _canon, _aliases in STATE_ALIASES.items():
    _LOOKUP[_norm(_canon)] = _canon
    for _alias in _aliases:
        _LOOKUP[_norm(_alias)] = _canon


def _coerce(value: Any) -> Any:
    """Normalise provider booleans/tri-state values."""
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    text = str(value).strip().lower()
    if text in _TRUE:
        if text == "mixed" or text == "indeterminate":
            return "mixed"
        return True
    if text in _FALSE:
        return False
    # UIA check states arrive as 0/1/2 or "On"/"Off" already handled above
    return True


def extract_state(raw: Mapping[str, Any] | Iterable[str] | None) -> dict[str, Any]:
    """Turn a raw state mapping (or list of state words) into canonical keys.

    Unknown keys are dropped: an unrecognised state is not printed as a
    ``foo=bar`` blob, it belongs in ``inspect`` properties instead.
    """
    out: dict[str, Any] = {}
    if not raw:
        return out
    if isinstance(raw, str):
        raw = [raw]
    items: Iterable[Any]
    if isinstance(raw, Mapping):
        items = raw.items()
    else:
        items = [(str(v), True) for v in raw]
    for key, value in items:
        raw_key = _norm(key)
        canon = _LOOKUP.get(raw_key)
        if canon is None:
            continue
        val = _coerce(value)
        if val is None:
            continue
        if canon == "enabled":
            # providers expose enabled-ness, the snapshot talks about disabled;
            # "enabled" is the silence case, so nothing is stored for it
            if not val:
                out["disabled"] = True
            continue
        if canon == "expand collapse state" or raw_key == "expandcollapsestate":
            out["expanded"] = str(value).strip().lower() in ("expanded", "true", "open")
            continue
        if canon == "checked" and val == "mixed":
            out["mixed"] = True
            out["checked"] = True
            continue
        if canon in ("expanded", "collapsed"):
            # collapsed is just !expanded; keep one canonical key
            out["expanded"] = bool(val) if canon == "expanded" else not bool(val)
            continue
        if canon == "read-only" or canon == "readonly":
            canon = "read-only"
        out[canon] = val
    return out


def is_offstage(state: Mapping[str, Any]) -> bool:
    return bool(state.get("offscreen"))


def visible_state(state: Mapping[str, Any], include_offscreen: bool = False) -> list[str]:
    """Return the non-default state words in canonical print order (R4)."""
    words: list[str] = []
    for name in STATE_ORDER:
        if name not in state:
            continue
        if name == "offscreen" and not include_offscreen:
            continue
        value = state[name]
        default = DEFAULT_STATE.get(name, False)
        if isinstance(value, bool):
            if value == default:
                continue
            if name == "collapsed":
                # a collapsed item is normal for menus; only say it when
                # something else implies it should be open, i.e. never here.
                continue
            words.append(name)
        else:
            # non boolean state (e.g. check tri-state) prints only if not default
            if str(value).lower() != str(default).lower():
                words.append(f"{name}={value}")
    return words


def state_suffix(state: Mapping[str, Any], extra: Iterable[str] = (),
                 include_offscreen: bool = False) -> str:
    """Render ``- focused, disabled`` or ``""`` (leading space included)."""
    parts = visible_state(state, include_offscreen=include_offscreen)
    for item in extra or ():
        if item and item not in parts:
            parts.append(item)
    if not parts:
        return ""
    return " - " + ", ".join(parts)
