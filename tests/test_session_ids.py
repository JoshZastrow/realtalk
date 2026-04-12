"""Tests for ID generation and sequence correctness."""

from realtalk.session import (
    _make_id,
    new_session,
    start_turn,
    add_user_text,
    add_assistant_text,
)


def test_prefixed_ids():
    for prefix in ("sess", "evt", "turn", "msg", "cnt", "call", "res", "fb"):
        assert _make_id(prefix).startswith(f"{prefix}_")


def test_ids_are_unique():
    ids = [_make_id("x") for _ in range(1000)]
    assert len(set(ids)) == 1000


def test_session_id_prefix():
    s = new_session("/tmp", "game")
    assert s.session_id.startswith("sess_")


def test_sequence_increments():
    s = new_session("/tmp", "game")
    assert s.events[0].envelope.sequence == 0

    s, turn_id = start_turn(s)
    assert s.events[1].envelope.sequence == 1

    s, _ = add_user_text(s, turn_id, "hi")
    assert s.events[2].envelope.sequence == 2

    s, _ = add_assistant_text(s, turn_id, "hello")
    assert s.events[3].envelope.sequence == 3


def test_all_events_share_session_id():
    s = new_session("/tmp", "game")
    s, turn_id = start_turn(s)
    s, _ = add_user_text(s, turn_id, "test")
    for event in s.events:
        assert event.envelope.session_id == s.session_id


def test_event_ids_are_unique_within_session():
    s = new_session("/tmp", "game")
    s, turn_id = start_turn(s)
    s, _ = add_user_text(s, turn_id, "a")
    s, _ = add_user_text(s, turn_id, "b")
    ids = [e.envelope.event_id for e in s.events]
    assert len(set(ids)) == len(ids)
