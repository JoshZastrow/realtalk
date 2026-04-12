"""Tests for training export functions."""

from realtalk.session import (
    FeedbackSource,
    TurnStatus,
    add_assistant_text,
    add_user_text,
    end_turn,
    new_session,
    record_feedback,
    start_turn,
    to_preference_examples,
    to_sft_examples,
    to_trajectory_examples,
    record_tool_call,
    record_tool_result,
)


def test_sft_export_from_simple_turn():
    s = new_session("/tmp", "realtalk")
    s, turn_id = start_turn(s)
    s, _ = add_user_text(s, turn_id, "go west")
    s, _ = add_assistant_text(s, turn_id, "You head into the plains.")
    s = end_turn(s, turn_id, TurnStatus.COMPLETED)

    rows = to_sft_examples(s)
    assert len(rows) == 1
    assert rows[0].messages[-1]["role"] == "assistant"
    assert rows[0].session_id == s.session_id


def test_sft_skips_non_completed_turns():
    s = new_session("/tmp", "realtalk")
    s, turn_id = start_turn(s)
    s, _ = add_user_text(s, turn_id, "go")
    s, _ = add_assistant_text(s, turn_id, "You go.")
    s = end_turn(s, turn_id, TurnStatus.FAILED)

    rows = to_sft_examples(s)
    assert len(rows) == 0


def test_sft_skips_turns_without_assistant_response():
    s = new_session("/tmp", "realtalk")
    s, turn_id = start_turn(s)
    s, _ = add_user_text(s, turn_id, "hello")
    s = end_turn(s, turn_id, TurnStatus.COMPLETED)

    rows = to_sft_examples(s)
    assert len(rows) == 0


def test_sft_multi_turn_context():
    s = new_session("/tmp", "realtalk")
    s, t1 = start_turn(s)
    s, _ = add_user_text(s, t1, "first")
    s, _ = add_assistant_text(s, t1, "answer one")
    s = end_turn(s, t1, TurnStatus.COMPLETED)

    s, t2 = start_turn(s)
    s, _ = add_user_text(s, t2, "second")
    s, _ = add_assistant_text(s, t2, "answer two")
    s = end_turn(s, t2, TurnStatus.COMPLETED)

    rows = to_sft_examples(s)
    assert len(rows) == 2
    # Second row should have context from first turn
    second_row_roles = [m["role"] for m in rows[1].messages]
    assert "user" in second_row_roles
    assert "assistant" in second_row_roles


def test_preference_export_requires_two_rated_feedbacks():
    s = new_session("/tmp", "realtalk")
    s, tid = start_turn(s)
    s, _ = add_user_text(s, tid, "go")
    s, msg_id = add_assistant_text(s, tid, "You go.")
    # Only one feedback — not enough for a preference pair
    s, _ = record_feedback(s, tid, FeedbackSource.USER, target_message_id=msg_id, rating=5)
    s = end_turn(s, tid, TurnStatus.COMPLETED)

    examples = to_preference_examples(s)
    assert len(examples) == 0


def test_preference_export_with_two_feedbacks():
    s = new_session("/tmp", "realtalk")
    s, tid = start_turn(s)
    s, _ = add_user_text(s, tid, "go")
    s, msg1 = add_assistant_text(s, tid, "You go north.")
    s, _ = record_feedback(s, tid, FeedbackSource.USER, target_message_id=msg1, rating=2)
    s, msg2 = add_assistant_text(s, tid, "You go south.")
    s, _ = record_feedback(s, tid, FeedbackSource.USER, target_message_id=msg2, rating=5)
    s = end_turn(s, tid, TurnStatus.COMPLETED)

    examples = to_preference_examples(s)
    assert len(examples) == 1
    ex = examples[0]
    assert ex.chosen["content"] != ex.rejected["content"]
    assert ex.metadata["chosen_rating"] == 5
    assert ex.metadata["rejected_rating"] == 2


def test_trajectory_export():
    s = new_session("/tmp", "realtalk")
    s, tid = start_turn(s)
    s, _ = add_user_text(s, tid, "hunt")
    s, call_id = record_tool_call(s, tid, "Bash", '{"command": "hunt"}')
    s, _ = record_tool_result(s, tid, call_id, "You found food.")
    s, _ = add_assistant_text(s, tid, "You search for food.")
    s = end_turn(s, tid, TurnStatus.COMPLETED)

    rows = to_trajectory_examples(s)
    assert len(rows) == 1
    row = rows[0]
    assert row.final_outcome["status"] == "completed"

    step_types = [step["type"] for step in row.steps]
    assert "message" in step_types
    assert "tool_call" in step_types
    assert "tool_result" in step_types


def test_trajectory_reward_signals_from_feedback():
    s = new_session("/tmp", "realtalk")
    s, tid = start_turn(s)
    s, _ = add_user_text(s, tid, "go")
    s, msg_id = add_assistant_text(s, tid, "You go.")
    s, _ = record_feedback(s, tid, FeedbackSource.USER, target_message_id=msg_id, rating=4, label="good")
    s = end_turn(s, tid, TurnStatus.COMPLETED)

    rows = to_trajectory_examples(s)
    assert len(rows) == 1
    assert len(rows[0].reward_signals) == 1
    assert rows[0].reward_signals[0]["rating"] == 4
    assert rows[0].reward_signals[0]["label"] == "good"
