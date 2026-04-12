"""Tests for event construction, append immutability, and structural correctness."""

import pytest

from realtalk.session import (
    FeedbackSource,
    MessageRole,
    SessionStarted,
    TurnStatus,
    add_assistant_text,
    add_user_text,
    end_turn,
    new_session,
    record_feedback,
    record_tool_call,
    record_tool_result,
    start_turn,
    SessionValidationError,
)


def test_first_event_is_session_started():
    s = new_session("/tmp", "game")
    assert isinstance(s.events[0], SessionStarted)
    assert s.events[0].envelope.event_type == "session_started"


def test_append_preserves_immutability():
    s = new_session("/tmp", "game")
    s2, _ = start_turn(s)
    assert s is not s2
    assert len(s.events) == 1
    assert len(s2.events) == 2


def test_new_session_fields():
    s = new_session("/workspace", "realtalk", model_hint="claude-opus-4-6")
    assert s.workspace_root == "/workspace"
    assert s.game_name == "realtalk"
    assert s.model_hint == "claude-opus-4-6"


def test_add_user_and_assistant_messages():
    s = new_session("/tmp", "game")
    s, turn_id = start_turn(s)
    s, uid = add_user_text(s, turn_id, "hello there")
    s, aid = add_assistant_text(s, turn_id, "Hi!")

    assert uid.startswith("msg_")
    assert aid.startswith("msg_")

    roles = [e.role for e in s.events if hasattr(e, "role")]
    assert roles == [MessageRole.USER, MessageRole.ASSISTANT]


def test_tool_call_and_result_are_linked():
    s = new_session("/tmp", "game")
    s, turn_id = start_turn(s)
    s, call_id = record_tool_call(s, turn_id, "Bash", '{"command": "ls"}')
    s, res_id = record_tool_result(s, turn_id, call_id, "file.txt")

    assert call_id.startswith("call_")
    assert res_id.startswith("res_")

    # The result event references the call
    result_events = [e for e in s.events if hasattr(e, "tool_call_id") and hasattr(e, "tool_result_id")]
    assert len(result_events) == 1
    assert result_events[0].tool_call_id == call_id


def test_tool_result_rejects_unknown_call_id():
    s = new_session("/tmp", "game")
    s, turn_id = start_turn(s)
    with pytest.raises(SessionValidationError):
        record_tool_result(s, turn_id, "call_missing", "output")


def test_feedback_attaches_to_message():
    s = new_session("/tmp", "game")
    s, turn_id = start_turn(s)
    s, _ = add_user_text(s, turn_id, "go")
    s, msg_id = add_assistant_text(s, turn_id, "You go north.")
    s, fb_id = record_feedback(
        s, turn_id, FeedbackSource.USER, target_message_id=msg_id, rating=4
    )
    assert fb_id.startswith("fb_")


def test_end_turn_sets_status():
    s = new_session("/tmp", "game")
    s, turn_id = start_turn(s)
    s, msg_id = add_assistant_text(s, turn_id, "Done.")
    s = end_turn(s, turn_id, TurnStatus.COMPLETED, final_message_id=msg_id)

    last = s.events[-1]
    assert last.envelope.event_type == "turn_ended"
    assert last.status == TurnStatus.COMPLETED


def test_multiple_turns():
    s = new_session("/tmp", "game")
    s, t1 = start_turn(s)
    s, _ = add_user_text(s, t1, "first")
    s = end_turn(s, t1, TurnStatus.COMPLETED)

    s, t2 = start_turn(s)
    s, _ = add_user_text(s, t2, "second")
    s = end_turn(s, t2, TurnStatus.COMPLETED)

    assert t1 != t2

    turn_started_events = [
        e for e in s.events if e.envelope.event_type == "turn_started"
    ]
    assert len(turn_started_events) == 2
