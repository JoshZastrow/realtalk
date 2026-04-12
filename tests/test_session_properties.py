"""Property-based tests using Hypothesis for session invariants."""

from hypothesis import given, settings
from hypothesis import strategies as st

from realtalk.session import (
    FeedbackSource,
    TurnStatus,
    SessionValidationError,
    add_assistant_text,
    add_user_text,
    end_turn,
    event_from_dict,
    event_to_dict,
    new_session,
    record_tool_call,
    record_tool_result,
    replay,
    session_from_jsonl,
    session_to_jsonl,
    start_turn,
    validate_session,
)


def _build_full_session(workspace: str, game: str, user_msg: str, assistant_msg: str):
    """Helper to build a simple complete session."""
    s = new_session(workspace, game)
    s, turn_id = start_turn(s)
    s, _ = add_user_text(s, turn_id, user_msg)
    s, msg_id = add_assistant_text(s, turn_id, assistant_msg)
    s = end_turn(s, turn_id, TurnStatus.COMPLETED, final_message_id=msg_id)
    return s


@given(
    workspace=st.text(min_size=1, max_size=50),
    game=st.text(min_size=1, max_size=30),
    user_msg=st.text(min_size=0, max_size=200),
    assistant_msg=st.text(min_size=0, max_size=200),
)
@settings(max_examples=50)
def test_property_session_round_trip(workspace, game, user_msg, assistant_msg):
    """A session serialized to JSONL and deserialized back is structurally identical."""
    s = _build_full_session(workspace, game, user_msg, assistant_msg)
    s2 = session_from_jsonl(session_to_jsonl(s))
    assert s.session_id == s2.session_id
    assert len(s.events) == len(s2.events)
    for orig, restored in zip(s.events, s2.events):
        assert orig == restored


@given(
    workspace=st.text(min_size=1, max_size=50),
    game=st.text(min_size=1, max_size=30),
    user_msg=st.text(min_size=0, max_size=200),
    assistant_msg=st.text(min_size=0, max_size=200),
)
@settings(max_examples=50)
def test_property_replay_is_deterministic(workspace, game, user_msg, assistant_msg):
    """Replaying the same session twice produces identical views."""
    s = _build_full_session(workspace, game, user_msg, assistant_msg)
    assert replay(s) == replay(s)


@given(
    text=st.text(min_size=0, max_size=300),
)
@settings(max_examples=50)
def test_property_append_monotonicity(text):
    """Appending a valid event always increases event count by exactly one."""
    s = new_session("/tmp", "game")
    count_before = len(s.events)
    s, turn_id = start_turn(s)
    assert len(s.events) == count_before + 1

    count_before = len(s.events)
    s, _ = add_user_text(s, turn_id, text)
    assert len(s.events) == count_before + 1


@given(
    user_msg=st.text(min_size=0, max_size=200),
    assistant_msg=st.text(min_size=0, max_size=200),
)
@settings(max_examples=50)
def test_property_replay_after_round_trip_is_stable(user_msg, assistant_msg):
    """replay(session_from_jsonl(session_to_jsonl(s))) == replay(s)."""
    s = _build_full_session("/workspace", "realtalk", user_msg, assistant_msg)
    s2 = session_from_jsonl(session_to_jsonl(s))
    assert replay(s) == replay(s2)


@given(
    tool_input=st.text(min_size=1, max_size=100),
    tool_output=st.text(min_size=1, max_size=100),
)
@settings(max_examples=30)
def test_property_tool_result_references_valid_call(tool_input, tool_output):
    """After record_tool_result, the result references a known call in the session."""
    s = new_session("/tmp", "game")
    s, turn_id = start_turn(s)
    s, call_id = record_tool_call(s, turn_id, "TestTool", tool_input)
    s, res_id = record_tool_result(s, turn_id, call_id, tool_output)

    view = replay(s)
    assert res_id in view.tool_results
    assert view.tool_results[res_id].tool_call_id == call_id
    assert call_id in view.tool_calls


@given(
    workspace=st.text(min_size=1, max_size=50),
    game=st.text(min_size=1, max_size=30),
)
@settings(max_examples=30)
def test_property_valid_session_always_passes_validation(workspace, game):
    """A session built through public constructors always passes validate_session."""
    s = new_session(workspace, game)
    validate_session(s)

    s, turn_id = start_turn(s)
    validate_session(s)

    s, _ = add_user_text(s, turn_id, "test")
    validate_session(s)

    s, _ = add_assistant_text(s, turn_id, "response")
    validate_session(s)

    s = end_turn(s, turn_id, TurnStatus.COMPLETED)
    validate_session(s)


@given(
    n_turns=st.integers(min_value=1, max_value=5),
)
@settings(max_examples=20)
def test_property_n_turns_produces_n_turn_records(n_turns):
    """Building n turns produces exactly n Turn records in the view."""
    s = new_session("/tmp", "game")
    for _ in range(n_turns):
        s, tid = start_turn(s)
        s, _ = add_user_text(s, tid, "prompt")
        s, _ = add_assistant_text(s, tid, "reply")
        s = end_turn(s, tid, TurnStatus.COMPLETED)

    view = replay(s)
    assert len(view.turns) == n_turns
