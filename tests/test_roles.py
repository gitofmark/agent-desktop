"""Step 7: role map, action map, reverse lookup, implied actions."""

from __future__ import annotations

import pytest

from snapshot_parser.model import Element
from snapshot_parser.roles import (
    ACTION_MAP,
    IMPLIED_ACTIONS,
    RAW_ROLE_MAP,
    ROLE_LABELS,
    SHORT_ROLES,
    action_for_raw,
    action_implied_by_role,
    actions_of,
    canonical_actions,
    normalize_action,
    normalize_role,
    raw_for_action,
    role_label,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("ControlType.Button", "button"),
        ("Button", "button"),
        ("PushButton", "button"),
        ("push button", "button"),
        ("ROLE_SYSTEM_PUSHBUTTON", "button"),
        ("Edit", "edit"),
        ("TextBox", "edit"),
        ("Document", "document"),
        ("MenuItem", "menuitem"),
        ("MenuBar", "menubar"),
        ("Menu", "menu"),
        ("Window", "window"),
        ("Pane", "pane"),
        ("ScrollBar", "scrollbar"),
        ("CheckBox", "checkbox"),
        ("RadioButton", "radio"),
        ("ComboBox", "combobox"),
        ("DataGrid", "table"),
        ("TreeItem", "treeitem"),
        ("Static", "unknown"),
        ("", "unknown"),
        (None, "unknown"),
    ],
)
def test_normalize_role(raw, expected):
    assert normalize_role(raw) == expected


def test_every_short_role_is_reachable_and_labelled():
    for role in SHORT_ROLES:
        assert normalize_role(role) == role
        assert role_label(role), f"missing plural label for {role}"
        assert role.startswith(role_label(role)[:3]) or role == "unknown"


def test_raw_role_map_values_are_short_roles():
    for raw, short in RAW_ROLE_MAP.items():
        assert short in SHORT_ROLES, f"{raw} -> {short} is not in the vocabulary"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Invoke", "click"),
        ("ControlPattern.Invoke", "click"),
        ("Click", "click"),
        ("SetFocus", "focus"),
        ("SetValue", "set value"),
        ("Scroll", "scroll"),
        ("Expand", "expand"),
        ("Collapse", "collapse"),
        ("Select", "select"),
        ("Toggle", "toggle"),
        ("Type", "type"),
        ("", ""),
    ],
)
def test_normalize_action(raw, expected):
    assert normalize_action(raw) == expected


def test_action_map_covers_every_role_action_used_by_the_parser():
    used = set()
    for actions in IMPLIED_ACTIONS.values():
        used |= actions
    used |= {"click", "focus", "set value", "scroll", "expand", "collapse"}
    assert used <= set(ACTION_MAP.values()), used - set(ACTION_MAP.values())


def test_reverse_lookup_round_trips():
    for raw, short in ACTION_MAP.items():
        assert action_for_raw(raw) == short
        assert normalize_action(raw_for_action(short)) == short


def test_reverse_lookup_prefers_the_elements_own_raw_id():
    el = Element(ref="e1", role="button", actions=["click"],
                 action_ids={"click": "ControlPattern.Invoke"})
    assert raw_for_action("click", el) == "ControlPattern.Invoke"


def test_canonical_actions_dedupes_and_orders():
    raw = ["SetFocus", "Invoke", "ControlPattern.Invoke", "SetValue"]
    assert canonical_actions(raw) == ["click", "focus", "set value"]


def test_canonical_actions_drops_empty():
    assert canonical_actions(["", None, "Invoke"]) == ["click"]


def test_actions_of_hides_what_the_role_implies():
    button = Element(ref="e1", role="button", actions=["click"])
    assert actions_of(button) == []
    scrollbar = Element(ref="e2", role="scrollbar", actions=["scroll", "focus"])
    assert actions_of(scrollbar) == []
    link = Element(ref="e3", role="link", actions=["click"])
    assert actions_of(link) == []


def test_actions_of_keeps_what_the_role_does_not_imply():
    button = Element(ref="e1", role="button", actions=["click", "expand"])
    assert actions_of(button) == ["expand"]


def test_actions_of_hides_actions_implied_by_state():
    el = Element(ref="e1", role="menuitem", actions=["expand", "click"],
                 state={"expanded": True})
    assert actions_of(el) == []


def test_action_implied_by_role():
    assert action_implied_by_role("button", "click")
    assert not action_implied_by_role("button", "scroll")


def test_role_label_pluralises():
    assert role_label("button") == "buttons"
    assert role_label("menuitem") == "menu items"
    assert role_label("scrollbar") == "scrollbars"
    for role in SHORT_ROLES:
        assert role_label(role).endswith("s")


@pytest.mark.parametrize(
    "raw",
    ["Button", "Edit", "MenuItem", "MenuBar", "Window", "Pane", "ScrollBar",
     "Text", "StatusBar", "ComboBox", "Dialog", "List", "TreeItem", "DataGrid",
     "ToolBar", "Tab", "TabItem", "Separator", "Hyperlink", "CheckBox"],
)
def test_every_role_in_the_fixtures_is_known(raw):
    """Step 7 acceptance: every role the fixtures use maps to something real."""
    assert normalize_role(raw) in SHORT_ROLES
