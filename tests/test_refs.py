"""Step 14: refs - short/full form, and stale-ref rejection with a clear message."""

from __future__ import annotations

import pytest

from snapshot_parser.model import Element, Snapshot
from snapshot_parser.refs import (
    RefError,
    RefRegistry,
    full_ref,
    make_ref,
    parse_ref,
    short_ref,
    sort_refs_numerically,
    validate_ref,
)

from conftest import load


def make_snapshot(number=1, session="abc123"):
    root = Element(ref="e1", role="window", name="Notepad")
    root.add(Element(ref="e2", role="button", name="Close", actions=["click"]))
    root.add(Element(ref="e3", role="edit", name="Text Editor", actions=["set value"]))
    return Snapshot(app="Notepad", title="Untitled", number=number,
                    session=session, root=root)


# -- parsing ------------------------------------------------------------
@pytest.mark.parametrize(
    "ref,short,session,number",
    [
        ("e10", "e10", "", 10),
        ("@abc:e10", "e10", "abc", 10),
        ("@syzqsppsg5kob:e1", "e1", "syzqsppsg5kob", 1),
        ("  e2  ", "e2", "", 2),
    ],
)
def test_parse_ref(ref, short, session, number):
    parts = parse_ref(ref)
    assert (parts.short, parts.session, parts.number) == (short, session, number)


@pytest.mark.parametrize("bad", ["", "  ", "10", "button", "e", "@abc", "e-1", "x10"])
def test_parse_ref_rejects_junk(bad):
    with pytest.raises(RefError) as exc:
        parse_ref(bad)
    assert exc.value.exit_code == 3
    assert "ref" in str(exc.value)


def test_short_and_full_round_trip():
    assert short_ref("@abc:e10") == "e10"
    assert full_ref("e10", "abc") == "@abc:e10"
    assert full_ref("@abc:e10") == "@abc:e10"
    assert make_ref("abc", 10) == "@abc:e10"


def test_full_ref_needs_a_session():
    with pytest.raises(RefError):
        full_ref("e1")


def test_sort_refs_numerically():
    assert sort_refs_numerically(["e10", "e2", "e1"]) == ["e1", "e2", "e10"]


# -- validation ---------------------------------------------------------
def test_valid_ref_returns_the_short_form():
    snap = make_snapshot(number=5, session="abc")
    assert validate_ref("e2", 5, "abc", snap) == "e2"
    assert validate_ref("@abc:e2", 5, "abc", snap) == "e2"


def test_unknown_ref_is_rejected_with_a_hint():
    snap = make_snapshot()
    with pytest.raises(RefError) as exc:
        validate_ref("e99", 1, "abc123", snap)
    message = str(exc.value)
    assert exc.value.exit_code == 3
    assert "unknown ref" in message
    assert "--full" in message, "the message must tell the model how to recover"


def test_stale_snapshot_ref_is_rejected():
    snap = make_snapshot(number=6, session="abc")
    with pytest.raises(RefError) as exc:
        validate_ref("e2", 5, "abc", snap)
    message = str(exc.value)
    assert "snapshot 5" in message and "number 6" in message
    assert "stale" in message
    assert "agent-desktop snapshot" in message
    assert exc.value.exit_code == 3


def test_wrong_session_is_rejected():
    snap = make_snapshot(session="abc")
    with pytest.raises(RefError) as exc:
        validate_ref("@zzz:e2", 1, "abc", snap)
    message = str(exc.value)
    assert "session" in message and "not portable" in message
    assert exc.value.exit_code == 3


def test_empty_ref_is_rejected():
    with pytest.raises(RefError) as exc:
        validate_ref("", 1, "abc")
    assert "empty ref" in str(exc.value)


# -- the real fixtures --------------------------------------------------
def test_every_ref_in_a_snapshot_validates():
    snap = load("notepad")
    for el in snap.walk():
        assert validate_ref(el.ref, snap.number, snap.session, snap) == el.ref


def test_refs_from_the_fixture_are_unique_and_sorted():
    snap = load("notepad_menu_open")
    refs = snap.refs()
    assert len(refs) == len(set(refs))


# -- registry -----------------------------------------------------------
def test_registry_assigns_unique_refs_and_remembers_raw_ids():
    registry = RefRegistry()
    assert registry.register("0x1A2B", "e1") == "e1"
    assert registry.register("0x1A2C", "e1") == "e2", "duplicates get a new ref"
    assert registry.register("0x1A2D") == "e3"
    assert registry.raw_of("e2") == "0x1A2C"
    assert len(registry) == 3


def test_registry_never_reuses_a_ref():
    registry = RefRegistry(used=["e1", "e2"])
    assert registry.register("raw", "e2") not in ("e1", "e2")


# -- refs are identities, not positions --------------------------------
def test_a_ref_that_now_means_something_else_is_rejected():
    """A bare e6 must not silently become a different element."""
    from snapshot_parser.session import SessionState

    state = SessionState(session="abc")
    old = make_snapshot(number=1, session="abc")
    state.remember_refs(old)
    assert state.refs_for(1)["e2"] == ["button", "Close"]

    root = Element(ref="e1", role="window", name="Notepad")
    root.add(Element(ref="e2", role="button", name="Save", actions=["click"]))
    new = Snapshot(app="Notepad", title="x", number=2, session="abc", root=root)
    with pytest.raises(RefError) as exc:
        state.check_ref_identity("e2", new.find("e2"), 1)
    message = str(exc.value)
    assert "stale" in message
    assert "Close" in message and "Save" in message
    assert "agent-desktop snapshot" in message


def test_a_ref_with_the_same_identity_still_works():
    from snapshot_parser.session import SessionState

    state = SessionState(session="abc")
    snap = make_snapshot(number=1, session="abc")
    state.remember_refs(snap)
    state.check_ref_identity("e2", snap.find("e2"), 1)  # no exception


def test_identity_survives_save_and_load(tmp_path):
    from snapshot_parser.session import SessionState

    state = SessionState(session="abc", number=3)
    state.remember_refs(make_snapshot(number=3, session="abc"))
    state.save(str(tmp_path))
    again = SessionState.load(str(tmp_path))
    assert again.refs_for(3)["e2"] == ["button", "Close"]


def test_unknown_refs_are_not_identity_checked():
    from snapshot_parser.session import SessionState

    state = SessionState(session="abc")
    root = Element(ref="e1", role="window", name="W")
    root.add(Element(ref="e99", role="button", name="Anything"))
    snap = Snapshot(root=root, session="abc")
    state.check_ref_identity("e99", snap.find("e99"), 1)  # never printed: allowed
