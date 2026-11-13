"""Step 9: non-default state only (R4)."""

from __future__ import annotations

import pytest

from snapshot_parser.state import (
    DEFAULT_STATE,
    STATE_ORDER,
    extract_state,
    state_suffix,
    visible_state,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ({"focused": True}, {"focused": True}),
        ({"IsKeyboardFocusable": False}, {}),
        ({"HasKeyboardFocus": True}, {"focused": True}),
        ({"checked": "On"}, {"checked": True}),
        ({"ToggleState": "Off"}, {"checked": False}),
        ({"IsChecked": "Indeterminate"}, {"checked": True, "mixed": True}),
        ({"expand_collapse_state": "Expanded"}, {"expanded": True}),
        ({"ExpandCollapseState": "Collapsed"}, {"expanded": False}),
        ({"IsEnabled": False}, {"disabled": True}),
        ({"disabled": "true"}, {"disabled": True}),
        ({"IsSelected": True}, {"selected": True}),
        ({"IsOffscreen": True}, {"offscreen": True}),
        ({"IsReadOnly": True}, {"read-only": True}),
        (["focused", "selected"], {"focused": True, "selected": True}),
        ({}, {}),
        (None, {}),
    ],
)
def test_extract_state(raw, expected):
    assert extract_state(raw) == expected


def test_unknown_state_keys_are_dropped_not_printed():
    assert extract_state({"SomeWeirdProviderFlag": True}) == {}


@pytest.mark.parametrize(
    "state,words",
    [
        ({}, []),
        ({"focused": False}, []),
        ({"focused": True}, ["focused"]),
        ({"disabled": False}, []),
        ({"disabled": True}, ["disabled"]),
        ({"checked": False}, []),          # unchecked is the default: print nothing
        ({"checked": True}, ["checked"]),
        ({"mixed": True, "checked": True}, ["checked", "mixed"]),
        ({"expanded": True}, ["expanded"]),
        ({"expanded": False}, []),         # collapsed is the default
        ({"maximized": True}, ["maximized"]),
        ({"selected": True}, ["selected"]),
        ({"modal": True}, ["modal"]),
        ({"offscreen": True}, []),         # not printed by default (R7)
        ({"offscreen": True, "focused": True}, ["focused"]),
    ],
)
def test_visible_state(state, words):
    assert visible_state(state) == words


def test_state_order_is_canonical():
    state = {"disabled": True, "focused": True, "checked": True}
    assert visible_state(state) == ["focused", "checked", "disabled"]


def test_state_order_covers_every_default():
    for name in DEFAULT_STATE:
        assert name in STATE_ORDER, f"{name} has no print order"


def test_state_suffix_includes_extras():
    assert state_suffix({"focused": True}, ["2340 chars"]) == " - focused, 2340 chars"
    assert state_suffix({}, ["click"]) == " - click"
    assert state_suffix({}) == ""


def test_state_suffix_offscreen_is_opt_in():
    assert state_suffix({"offscreen": True}) == ""
    assert state_suffix({"offscreen": True}, include_offscreen=True) == " - offscreen"


def test_disabled_save_shows_disabled():
    """Step 9 acceptance: a disabled Save shows ``disabled``."""
    from snapshot_parser.model import Element
    from snapshot_parser.roles import actions_of

    save = Element(ref="e1", role="button", name="Save", actions=["click"],
                   state=extract_state({"IsEnabled": False}))
    assert visible_state(save.state) == ["disabled"]
    # nothing else is left to print - the action is unusable and implied
    assert actions_of(save) == []


def test_unchecked_default_shows_nothing():
    from snapshot_parser.model import Element

    box = Element(ref="e1", role="checkbox", name="Word wrap", actions=["click"],
                  state=extract_state({"ToggleState": "Off"}))
    assert visible_state(box.state) == []
    assert state_suffix(box.state) == ""


def test_enabled_true_stores_nothing_and_enabled_false_means_disabled():
    assert extract_state({"enabled": True}) == {}
    assert extract_state({"IsEnabled": True}) == {}
    assert extract_state({"enabled": False}) == {"disabled": True}
