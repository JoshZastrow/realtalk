"""Tests for replay: determinism, reconstruction, and validation."""

import pytest

from realtalk.session import (
    FeedbackSource,
    MessageRole,
    ReplayError,
    Session,
    SessionStarted,
    SessionValidationError,
    TurnStatus,
    add_assistant_text,
    add_user_text,
    end_turn,
    record_feedback,
    record_tool_call,
    record_tool_result,
    replay,
    new_session,
    start_turn,
    EventEnvelope,
    TurnEnded,
    append_event,
)


def _build_simple_session() -> tuple[Session, str]:
    s = new_session("/tmp", "realtalk")
    s, turn_id = start_turn(s)
    s, _ = add_user_text(s, turn_id, "go north")
    s, msg_id = add_assistant_text(s, turn_id, "You walk north.")
    s = end_turn(s, turn_id, TurnStatus.COMPLETED, final_message_id=msg_id)
    return s, turn_id


def test_replay_reconstructs_messages():
    s, _ = _build_simple_session()
    view = replay(s)
    assert len(view.messages) == 2
    assert view.messages[0].role == MessageRole.USER
    assert view.messages[1].role == MessageRole.ASSISTANT


def test_replay_reconstructs_turns():
    s, turn_id = _build_simple_session()
    view = replay(s)
    assert len(view.turns) == 1
    assert view.turns[0].turn_id == turn_id
    assert view.turns[0].status == TurnStatus.COMPLETED


def test_replay_is_deterministic():
    s, _ = _build_simple_session()
    view1 = replay(s)
    view2 = replay(s)
    assert view1 == view2


def test_replay_tool_call_and_result():
    s = new_session("/tmp", "game")
    s, turn_id = start_turn(s)
    s, call_id = record_tool_call(s, turn_id, "Bash", '{"command": "pwd"}')
    s, res_id = record_tool_result(s, turn_id, call_id, "/workspace")
    s = end_turn(s, turn_id, TurnStatus.COMPLETED)

    view = replay(s)
    assert call_id in view.tool_calls
    assert res_id in view.tool_results
    assert view.tool_results[res_id].tool_call_id == call_id


def test_replay_feedback():
    s = new_session("/tmp", "game")
    s, turn_id = start_turn(s)
    s, _ = add_user_text(s, turn_id, "hi")
    s, msg_id = add_assistant_text(s, turn_id, "hello")
    s, fb_id = record_feedback(
        s, turn_id, FeedbackSource.USER, target_message_id=msg_id, rating=5
    )
    s = end_turn(s, turn_id, TurnStatus.COMPLETED)

    view = replay(s)
    assert len(view.feedback) == 1
    assert view.feedback[0].feedback_id == fb_id
    assert view.feedback[0].rating == 5


def test_replay_rejects_duplicate_turn_id():
    s = new_session("/tmp", "game")
    s, turn_id = start_turn(s)

    # Manually craft a duplicate TurnEnded that closes the turn twice
    env = EventEnvelope(
        event_id="evt_dup",
        session_id=s.session_id,
        event_type="turn_ended",
        timestamp="2026-01-01T00:00:00Z",
        sequence=len(s.events),
    )
    bad_event = TurnEnded(
        envelope=env,
        turn_id=turn_id,
        status=TurnStatus.COMPLETED,
        final_message_id=None,
        metadata={},
    )
    s = end_turn(s, turn_id, TurnStatus.COMPLETED)

    # Now append a second TurnEnded for the same turn
    s2_events = s.events + (bad_event,)
    bad_session = Session(
        session_id=s.session_id,
        created_at=s.created_at,
        workspace_root=s.workspace_root,
        game_name=s.game_name,
        model_hint=s.model_hint,
        events=s2_events,
        metadata={},
    )
    with pytest.raises(SessionValidationError):
        replay(bad_session)


def test_replay_multi_turn_message_ordering():
    s = new_session("/tmp", "game")
    s, t1 = start_turn(s)
    s, _ = add_user_text(s, t1, "first")
    s, _ = add_assistant_text(s, t1, "response one")
    s = end_turn(s, t1, TurnStatus.COMPLETED)

    s, t2 = start_turn(s)
    s, _ = add_user_text(s, t2, "second")
    s, _ = add_assistant_text(s, t2, "response two")
    s = end_turn(s, t2, TurnStatus.COMPLETED)

    view = replay(s)
    assert len(view.messages) == 4
    texts = [
        p.text
        for msg in view.messages
        for p in msg.parts
        if hasattr(p, "text")
    ]
    assert texts == ["first", "response one", "second", "response two"]


def test_replay_open_turn_has_open_status():
    s = new_session("/tmp", "game")
    s, turn_id = start_turn(s)
    s, _ = add_user_text(s, turn_id, "go")
    view = replay(s)
    assert view.turns[0].status == TurnStatus.OPEN
