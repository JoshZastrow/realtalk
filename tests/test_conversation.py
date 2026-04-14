"""Tests for the conversation runtime (Layer 4).

Every test constructs a ConversationRuntime with injected test doubles
(ScriptedClient/MockClient + NoOpExecutor/EchoExecutor/StaticExecutor)
and exercises run_turn(). No real API calls, no disk I/O.
"""

import pytest

from realtalk.api import (
    ApiRequest,
    MessageStop,
    MockClient,
    ScriptedClient,
    TextDelta,
    ToolUse,
    UsageEvent,
)
from realtalk.conversation import (
    ConversationRuntime,
    NoOpExecutor,
    StaticExecutor,
    format_session_for_api,
)
from realtalk.session import (
    MessageRole,
    ToolCallRecorded,
    ToolResultRecorded,
    TurnStatus,
    add_assistant_text,
    add_user_text,
    derive_messages,
    new_session,
    record_tool_call,
    record_tool_result,
    start_turn,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session():
    return new_session("/tmp/test", "realtalk", model_hint="test-model")


def _make_runtime(
    client,
    executor=None,
    session=None,
    system_prompt=None,
    tool_definitions=None,
    max_iterations=10,
):
    return ConversationRuntime(
        api_client=client,
        tool_executor=executor or NoOpExecutor(),
        session=session or _make_session(),
        system_prompt=system_prompt or ["You are a test character."],
        tool_definitions=tool_definitions or [],
        on_text=lambda text: None,
        model="test-model",
        max_iterations=max_iterations,
    )


# ---------------------------------------------------------------------------
# 1. Single turn, no tools
# ---------------------------------------------------------------------------


def test_single_turn_no_tools():
    """User says something, model responds with text only. Simplest path."""
    client = MockClient([
        TextDelta("Hello "),
        TextDelta("there!"),
        UsageEvent(input_tokens=10, output_tokens=5),
        MessageStop(),
    ])
    runtime = _make_runtime(client)
    summary = runtime.run_turn("hi")

    assert summary.iterations == 1
    assert summary.input_tokens == 10
    assert summary.output_tokens == 5
    assert summary.tool_calls == ()
    assert summary.hit_iteration_limit is False
    assert summary.status == TurnStatus.COMPLETED

    # Session should have: SessionStarted + TurnStarted + user msg + assistant msg + TurnEnded
    messages = derive_messages(runtime.session)
    assert len(messages) == 2
    assert messages[0].role == MessageRole.USER
    assert messages[1].role == MessageRole.ASSISTANT
    assert messages[1].parts[0].text == "Hello there!"


# ---------------------------------------------------------------------------
# 2. Single tool call (two iterations)
# ---------------------------------------------------------------------------


def test_single_tool_call():
    """Model calls one tool, gets result, then responds with text."""
    client = ScriptedClient([
        [
            ToolUse(id="tu_1", name="update_mood", input='{"delta": 5}'),
            UsageEvent(input_tokens=20, output_tokens=10),
            MessageStop(),
        ],
        [
            TextDelta("I can feel the warmth growing."),
            UsageEvent(input_tokens=30, output_tokens=15),
            MessageStop(),
        ],
    ])
    executor = StaticExecutor({"update_mood": "mood updated to 65"})
    runtime = _make_runtime(client, executor=executor)
    summary = runtime.run_turn("I appreciate you sharing that.")

    assert summary.iterations == 2
    assert len(summary.tool_calls) == 1
    assert summary.tool_calls[0].tool_name == "update_mood"
    assert summary.tool_calls[0].output_text == "mood updated to 65"
    assert summary.tool_calls[0].is_error is False
    assert summary.input_tokens == 50   # 20 + 30
    assert summary.output_tokens == 25  # 10 + 15


# ---------------------------------------------------------------------------
# 3. Multiple tool calls in one iteration
# ---------------------------------------------------------------------------


def test_multiple_tools_one_iteration():
    """Model calls two tools in a single response, both execute, then model responds."""
    client = ScriptedClient([
        [
            ToolUse(id="tu_1", name="update_mood", input='{"delta": 3}'),
            ToolUse(id="tu_2", name="update_security", input='{"delta": -2}'),
            UsageEvent(input_tokens=25, output_tokens=12),
            MessageStop(),
        ],
        [
            TextDelta("Things are shifting."),
            UsageEvent(input_tokens=35, output_tokens=8),
            MessageStop(),
        ],
    ])
    executor = StaticExecutor({
        "update_mood": "mood now 53",
        "update_security": "security now 48",
    })
    runtime = _make_runtime(client, executor=executor)
    summary = runtime.run_turn("Tell me more about yourself.")

    assert summary.iterations == 2
    assert len(summary.tool_calls) == 2
    assert summary.tool_calls[0].tool_name == "update_mood"
    assert summary.tool_calls[1].tool_name == "update_security"


# ---------------------------------------------------------------------------
# 4. Three-iteration tool chain
# ---------------------------------------------------------------------------


def test_chained_tool_calls():
    """Model calls tools across three iterations before stopping."""
    client = ScriptedClient([
        [ToolUse(id="t1", name="analyze", input='{}'), UsageEvent(10, 5), MessageStop()],
        [ToolUse(id="t2", name="update_mood", input='{}'), UsageEvent(10, 5), MessageStop()],
        [TextDelta("All done."), UsageEvent(10, 5), MessageStop()],
    ])
    executor = StaticExecutor({"analyze": "analysis complete", "update_mood": "ok"})
    runtime = _make_runtime(client, executor=executor)
    summary = runtime.run_turn("What do you think?")

    assert summary.iterations == 3
    assert len(summary.tool_calls) == 2
    assert summary.status == TurnStatus.COMPLETED


# ---------------------------------------------------------------------------
# 5. Iteration limit
# ---------------------------------------------------------------------------


def test_iteration_limit_stops_runaway_loop():
    """If the model keeps calling tools past max_iterations, the turn fails."""
    sequences = [
        [ToolUse(id=f"t{i}", name="loop_tool", input='{}'), UsageEvent(5, 5), MessageStop()]
        for i in range(5)
    ]
    client = ScriptedClient(sequences)
    executor = StaticExecutor({"loop_tool": "looping"})
    runtime = _make_runtime(client, executor=executor, max_iterations=3)
    summary = runtime.run_turn("go")

    assert summary.iterations == 3
    assert summary.hit_iteration_limit is True
    assert summary.status == TurnStatus.FAILED


# ---------------------------------------------------------------------------
# 6. on_text callback fires for each TextDelta
# ---------------------------------------------------------------------------


def test_on_text_callback():
    """Each TextDelta fires the on_text callback with the delta text."""
    captured: list[str] = []
    client = MockClient([
        TextDelta("Hello "),
        TextDelta("world"),
        MessageStop(),
    ])
    runtime = ConversationRuntime(
        api_client=client,
        tool_executor=NoOpExecutor(),
        session=_make_session(),
        system_prompt=["test"],
        tool_definitions=[],
        on_text=captured.append,
        model="test",
    )
    runtime.run_turn("hi")
    assert captured == ["Hello ", "world"]


# ---------------------------------------------------------------------------
# 7. Tool executor error becomes tool result (not exception)
# ---------------------------------------------------------------------------


def test_tool_executor_error_becomes_result():
    """If the executor raises, the error text becomes the tool result with is_error=True."""
    class FailingExecutor:
        def execute(self, tool_name: str, tool_input: str) -> str:
            raise ValueError("parse error: invalid JSON")

    client = ScriptedClient([
        [ToolUse(id="t1", name="bad_tool", input="not json"), UsageEvent(10, 5), MessageStop()],
        [TextDelta("I see there was an error."), UsageEvent(10, 5), MessageStop()],
    ])
    runtime = _make_runtime(client, executor=FailingExecutor())
    summary = runtime.run_turn("do the thing")

    assert summary.iterations == 2
    assert summary.tool_calls[0].is_error is True
    # Exception type is exposed, but not the raw message (security: don't leak impl details to LLM)
    assert "ValueError" in summary.tool_calls[0].output_text
    assert "parse error" not in summary.tool_calls[0].output_text


# ---------------------------------------------------------------------------
# 8. Usage tokens accumulate across iterations
# ---------------------------------------------------------------------------


def test_usage_accumulates():
    """Usage from all iterations sums together in TurnSummary."""
    client = ScriptedClient([
        [ToolUse(id="t1", name="a", input='{}'),
         UsageEvent(input_tokens=100, output_tokens=50,
                    cache_creation_tokens=10, cache_read_tokens=5),
         MessageStop()],
        [TextDelta("done"),
         UsageEvent(input_tokens=200, output_tokens=30,
                    cache_creation_tokens=0, cache_read_tokens=20),
         MessageStop()],
    ])
    executor = StaticExecutor({"a": "ok"})
    runtime = _make_runtime(client, executor=executor)
    summary = runtime.run_turn("go")

    assert summary.input_tokens == 300
    assert summary.output_tokens == 80
    assert summary.cache_creation_tokens == 10
    assert summary.cache_read_tokens == 25


# ---------------------------------------------------------------------------
# 9. Session event sequence after a tool turn
# ---------------------------------------------------------------------------


def test_session_event_sequence():
    """Verify the session contains the correct event types after a tool turn."""
    client = ScriptedClient([
        [ToolUse(id="t1", name="mood", input='{}'), UsageEvent(10, 5), MessageStop()],
        [TextDelta("ok"), UsageEvent(10, 5), MessageStop()],
    ])
    executor = StaticExecutor({"mood": "65"})
    runtime = _make_runtime(client, executor=executor)
    runtime.run_turn("hello")

    session = runtime.session
    event_types = [e.envelope.event_type for e in session.events]
    assert "session_started" in event_types
    assert "turn_started" in event_types
    assert "message_added" in event_types
    assert "tool_call_recorded" in event_types
    assert "tool_result_recorded" in event_types
    assert "turn_ended" in event_types

    tool_calls = [e for e in session.events if isinstance(e, ToolCallRecorded)]
    tool_results = [e for e in session.events if isinstance(e, ToolResultRecorded)]
    assert len(tool_calls) == 1
    assert len(tool_results) == 1
    assert tool_calls[0].tool_name == "mood"
    assert tool_results[0].output_text == "65"


# ---------------------------------------------------------------------------
# 10. ScriptedClient records requests
# ---------------------------------------------------------------------------


def test_scripted_client_records_requests():
    """ScriptedClient.requests captures each ApiRequest for inspection."""
    client = ScriptedClient([
        [TextDelta("hi"), MessageStop()],
    ])
    runtime = _make_runtime(client, system_prompt=["Be kind."])
    runtime.run_turn("hello")

    assert client.call_count == 1
    assert client.requests[0].system_prompt == ["Be kind."]
    assert client.requests[0].model == "test-model"


# ---------------------------------------------------------------------------
# 11. ScriptedClient raises on exhaustion
# ---------------------------------------------------------------------------


def test_scripted_client_exhaustion():
    """Calling stream() past the scripted sequences raises IndexError."""
    client = ScriptedClient([[TextDelta("x"), MessageStop()]])
    list(client.stream(ApiRequest(system_prompt=[], messages=[], tools=[])))
    with pytest.raises(IndexError, match="exhausted"):
        list(client.stream(ApiRequest(system_prompt=[], messages=[], tools=[])))


# ---------------------------------------------------------------------------
# 12. format_session_for_api: basic text messages
# ---------------------------------------------------------------------------


def test_format_basic_text_messages():
    """User and assistant text messages convert to Anthropic format."""
    s = _make_session()
    s, tid = start_turn(s)
    s, _ = add_user_text(s, tid, "hello")
    s, _ = add_assistant_text(s, tid, "world")

    api_msgs = format_session_for_api(s)
    assert len(api_msgs) == 2
    assert api_msgs[0] == {"role": "user", "content": "hello"}
    assert api_msgs[1]["role"] == "assistant"


# ---------------------------------------------------------------------------
# 13. format_session_for_api: tool calls and results
# ---------------------------------------------------------------------------


def test_format_tool_call_and_result():
    """Tool calls in assistant messages and tool results format correctly."""
    s = _make_session()
    s, tid = start_turn(s)
    s, _ = add_user_text(s, tid, "do something")
    s, _ = add_assistant_text(s, tid, "")
    s, call_id = record_tool_call(s, tid, "update_mood", '{"delta": 5}')
    s, _ = record_tool_result(s, tid, call_id, "mood is now 65")

    api_msgs = format_session_for_api(s)

    # Find the assistant message -- should have a tool_use block
    assistant_msgs = [m for m in api_msgs if m["role"] == "assistant"]
    assert len(assistant_msgs) == 1
    assistant_content = assistant_msgs[0]["content"]
    assert any(
        isinstance(b, dict) and b.get("type") == "tool_use"
        for b in assistant_content
    )

    # Find the tool result -- should be in a user-role message
    user_msgs_with_results = [
        m for m in api_msgs
        if m["role"] == "user"
        and isinstance(m.get("content"), list)
        and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in m["content"])
    ]
    assert len(user_msgs_with_results) == 1


# ---------------------------------------------------------------------------
# 14. format_session_for_api: consecutive same-role merging
# ---------------------------------------------------------------------------


def test_format_merges_consecutive_same_role():
    """Two consecutive tool results (role=user) merge into one user message."""
    s = _make_session()
    s, tid = start_turn(s)
    s, _ = add_user_text(s, tid, "go")
    s, _ = add_assistant_text(s, tid, "")
    s, c1 = record_tool_call(s, tid, "tool_a", '{}')
    s, c2 = record_tool_call(s, tid, "tool_b", '{}')
    s, _ = record_tool_result(s, tid, c1, "result_a")
    s, _ = record_tool_result(s, tid, c2, "result_b")

    api_msgs = format_session_for_api(s)

    # No two consecutive messages should have the same role
    for i in range(len(api_msgs) - 1):
        assert api_msgs[i]["role"] != api_msgs[i + 1]["role"], (
            f"Consecutive same-role messages at index {i}: "
            f"{api_msgs[i]['role']}"
        )


# ---------------------------------------------------------------------------
# 15. Text-only turn produces zero tool_calls
# ---------------------------------------------------------------------------


def test_text_only_no_tool_use_in_summary():
    """A text-only response produces zero tool_calls in the summary."""
    client = MockClient([TextDelta("just text"), MessageStop()])
    runtime = _make_runtime(client)
    summary = runtime.run_turn("talk to me")
    assert summary.tool_calls == ()
    assert summary.iterations == 1


# ---------------------------------------------------------------------------
# 16. Mixed text and tool_use in a single response
# ---------------------------------------------------------------------------


def test_mixed_text_and_tool_in_single_response():
    """Model emits both text and a tool call in one response."""
    client = ScriptedClient([
        [
            TextDelta("Let me think... "),
            ToolUse(id="t1", name="analyze", input='{"topic": "trust"}'),
            UsageEvent(15, 10),
            MessageStop(),
        ],
        [TextDelta("I see now."), UsageEvent(10, 5), MessageStop()],
    ])
    executor = StaticExecutor({"analyze": "analysis done"})
    runtime = _make_runtime(client, executor=executor)
    summary = runtime.run_turn("What do you think about trust?")

    assert summary.iterations == 2
    assert len(summary.tool_calls) == 1


# ---------------------------------------------------------------------------
# 17. Second API call includes tool results in messages
# ---------------------------------------------------------------------------


def test_second_api_call_includes_tool_results():
    """After tool execution, the next API call's messages include tool results."""
    client = ScriptedClient([
        [ToolUse(id="t1", name="check", input='{}'), UsageEvent(10, 5), MessageStop()],
        [TextDelta("noted"), UsageEvent(10, 5), MessageStop()],
    ])
    executor = StaticExecutor({"check": "all clear"})
    runtime = _make_runtime(client, executor=executor)
    runtime.run_turn("check status")

    # The second request should contain more messages than the first
    assert len(client.requests) == 2
    assert len(client.requests[1].messages) > len(client.requests[0].messages)


# ---------------------------------------------------------------------------
# 18. set_system_prompt replaces the prompt for subsequent turns
# ---------------------------------------------------------------------------


def test_set_system_prompt_replaces_prompt():
    """Engine rebinds the system prompt between turns; next request uses new prompt."""
    client = ScriptedClient([
        [TextDelta("first"), MessageStop()],
        [TextDelta("second"), MessageStop()],
    ])
    runtime = _make_runtime(client, system_prompt=["initial"])
    runtime.run_turn("one")
    assert client.requests[0].system_prompt == ["initial"]

    runtime.set_system_prompt(["updated", "dynamic"])
    runtime.run_turn("two")
    assert client.requests[1].system_prompt == ["updated", "dynamic"]


# ---------------------------------------------------------------------------
# 19. set_session replaces the internal session reference
# ---------------------------------------------------------------------------


def test_set_session_replaces_session():
    """Engine swaps the internal session (used by compaction to hand back a trimmed session)."""
    client = MockClient([TextDelta("hi"), MessageStop()])
    runtime = _make_runtime(client)
    original_id = runtime.session.session_id

    replacement = new_session("/tmp/test", "realtalk", model_hint="test-model")
    assert replacement.session_id != original_id

    runtime.set_session(replacement)
    assert runtime.session.session_id == replacement.session_id
