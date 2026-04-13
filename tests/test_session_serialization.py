"""Tests for JSON serialization round-trips."""

import json

import pytest

from realtalk.session import (
    FeedbackSource,
    SerializationError,
    TurnStatus,
    add_assistant_text,
    add_user_text,
    end_turn,
    event_from_dict,
    event_to_dict,
    new_session,
    record_feedback,
    record_tool_call,
    record_tool_result,
    replay,
    session_from_jsonl,
    session_to_jsonl,
    start_turn,
)


def test_session_started_round_trip():
    s = new_session("/tmp", "realtalk", model_hint="claude-opus-4-6")
    orig = s.events[0]
    restored = event_from_dict(event_to_dict(orig))
    assert restored == orig


def test_turn_started_round_trip():
    s = new_session("/tmp", "game")
    s, _ = start_turn(s)
    orig = s.events[1]
    restored = event_from_dict(event_to_dict(orig))
    assert restored == orig


def test_message_added_round_trip():
    s = new_session("/tmp", "game")
    s, turn_id = start_turn(s)
    s, _ = add_user_text(s, turn_id, "hello world")
    orig = s.events[-1]
    restored = event_from_dict(event_to_dict(orig))
    assert restored == orig


def test_tool_call_round_trip():
    s = new_session("/tmp", "game")
    s, turn_id = start_turn(s)
    s, _ = record_tool_call(s, turn_id, "Bash", '{"command": "ls"}')
    orig = s.events[-1]
    restored = event_from_dict(event_to_dict(orig))
    assert restored == orig


def test_tool_result_round_trip():
    s = new_session("/tmp", "game")
    s, turn_id = start_turn(s)
    s, call_id = record_tool_call(s, turn_id, "Bash", '{"command": "ls"}')
    s, _ = record_tool_result(s, turn_id, call_id, "file.txt", is_error=False)
    orig = s.events[-1]
    restored = event_from_dict(event_to_dict(orig))
    assert restored == orig


def test_feedback_round_trip():
    s = new_session("/tmp", "game")
    s, turn_id = start_turn(s)
    s, msg_id = add_assistant_text(s, turn_id, "hello")
    s, _ = record_feedback(
        s, turn_id, FeedbackSource.USER, target_message_id=msg_id, rating=5
    )
    orig = s.events[-1]
    restored = event_from_dict(event_to_dict(orig))
    assert restored == orig


def test_turn_ended_round_trip():
    s = new_session("/tmp", "game")
    s, turn_id = start_turn(s)
    s, msg_id = add_assistant_text(s, turn_id, "done")
    s = end_turn(s, turn_id, TurnStatus.COMPLETED, final_message_id=msg_id)
    orig = s.events[-1]
    restored = event_from_dict(event_to_dict(orig))
    assert restored == orig


def test_session_jsonl_round_trip():
    s = new_session("/tmp", "realtalk")
    s, turn_id = start_turn(s)
    s, _ = add_user_text(s, turn_id, "go west")
    s, _ = add_assistant_text(s, turn_id, "You head west.")
    s = end_turn(s, turn_id, TurnStatus.COMPLETED)

    lines = list(session_to_jsonl(s))
    s2 = session_from_jsonl(lines)

    assert s2.session_id == s.session_id
    assert len(s2.events) == len(s.events)
    for orig, restored in zip(s.events, s2.events):
        assert orig == restored


def test_jsonl_lines_are_valid_json():
    s = new_session("/tmp", "game")
    s, turn_id = start_turn(s)
    s, _ = add_user_text(s, turn_id, "hello")
    for line in session_to_jsonl(s):
        obj = json.loads(line)
        assert "event_type" in obj
        assert "event_id" in obj
        assert "session_id" in obj


def test_replay_after_round_trip_is_identical():
    s = new_session("/tmp", "game")
    s, turn_id = start_turn(s)
    s, _ = add_user_text(s, turn_id, "hunt")
    s, call_id = record_tool_call(s, turn_id, "Bash", '{"command": "hunt"}')
    s, _ = record_tool_result(s, turn_id, call_id, "prey found")
    s, msg_id = add_assistant_text(s, turn_id, "You hunt successfully.")
    s = end_turn(s, turn_id, TurnStatus.COMPLETED, final_message_id=msg_id)

    s2 = session_from_jsonl(session_to_jsonl(s))
    assert replay(s) == replay(s2)


# ---------------------------------------------------------------------------
# skip_errors parameter
# ---------------------------------------------------------------------------


def test_session_from_jsonl_skip_errors():
    """skip_errors=True silently drops invalid JSON lines."""
    s = new_session("/tmp", "realtalk")
    good_lines = list(session_to_jsonl(s))
    bad_lines = good_lines + ['{"incomplete":']
    # Default behaviour: raises on bad JSON
    with pytest.raises(SerializationError):
        session_from_jsonl(bad_lines)
    # skip_errors: succeeds, bad line ignored
    result = session_from_jsonl(bad_lines, skip_errors=True)
    assert len(result.events) == len(s.events)
