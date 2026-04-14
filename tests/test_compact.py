from __future__ import annotations

from realtalk.compact import (
    COMPACT_CONTINUATION_PREAMBLE,
    COMPACT_DIRECT_RESUME_INSTRUCTION,
    COMPACT_RECENT_MESSAGES_NOTE,
    CompactionConfig,
    compact_session,
    estimate_session_tokens,
    format_compact_summary,
    merge_compact_summaries,
    should_compact,
)
from realtalk.conversation import format_session_for_api
from realtalk.session import (
    TurnStatus,
    add_assistant_text,
    add_user_text,
    derive_messages,
    new_session,
    record_tool_call,
    record_tool_result,
    start_turn,
    end_turn,
)


def _build_session(turns: int, text_size: int = 10):
    session = new_session("/tmp/test", "realtalk")
    for _ in range(turns):
        session, turn_id = start_turn(session)
        session, _ = add_user_text(session, turn_id, "u" * text_size)
        session, _ = add_assistant_text(session, turn_id, "a" * text_size)
        session = end_turn(session, turn_id, TurnStatus.COMPLETED)
    return session


def test_estimate_tokens_empty_session() -> None:
    assert estimate_session_tokens(new_session("/tmp/test", "realtalk")) == 0


def test_should_compact_above_threshold() -> None:
    session = _build_session(6, text_size=2_000)
    assert should_compact(session, CompactionConfig(max_estimated_tokens=1))


def test_should_compact_requires_enough_messages() -> None:
    session = new_session("/tmp/test", "realtalk")
    session, turn_id = start_turn(session)
    session, _ = add_user_text(session, turn_id, "x" * 400_000)
    assert not should_compact(
        session,
        CompactionConfig(preserve_recent_messages=4, max_estimated_tokens=1),
    )


def test_compact_returns_summary_sections() -> None:
    session = _build_session(10, text_size=2_000)
    result = compact_session(
        session,
        CompactionConfig(preserve_recent_messages=2, max_estimated_tokens=1),
    )
    assert result.removed_message_count > 0
    assert result.summary_sections[0] == COMPACT_CONTINUATION_PREAMBLE
    assert COMPACT_RECENT_MESSAGES_NOTE in result.summary_sections
    assert COMPACT_DIRECT_RESUME_INSTRUCTION in result.summary_sections


def test_compact_rebuild_has_no_system_message() -> None:
    session = _build_session(10, text_size=2_000)
    result = compact_session(
        session,
        CompactionConfig(preserve_recent_messages=2, max_estimated_tokens=1),
    )
    messages = derive_messages(result.compacted_session)
    assert all(message.role.value != "system" for message in messages)


def test_format_session_for_api_after_compaction() -> None:
    session = new_session("/tmp/test", "realtalk")
    session, turn_id = start_turn(session)
    session, _ = add_user_text(session, turn_id, "before")
    session, _ = add_assistant_text(session, turn_id, "")
    session, call_id = record_tool_call(session, turn_id, "generate_options", '{"options":["1","2","3"]}')
    session, _ = record_tool_result(session, turn_id, call_id, "ok")
    session, _ = add_assistant_text(session, turn_id, "after")
    session = end_turn(session, turn_id, TurnStatus.COMPLETED)

    result = compact_session(
        session,
        CompactionConfig(preserve_recent_messages=2, max_estimated_tokens=1),
    )
    messages = format_session_for_api(result.compacted_session)
    assert all(message["role"] != "system" for message in messages)
    assert not (
        isinstance(messages[0]["content"], list)
        and messages[0]["content"][0].get("type") == "tool_result"
    )


def test_format_compact_summary_strips_tags() -> None:
    assert format_compact_summary("<summary>Kept work</summary>") == "Summary:\nKept work"


def test_merge_compact_summaries_mentions_both_windows() -> None:
    merged = merge_compact_summaries(
        ["old summary"],
        "<summary>new summary</summary>",
    )
    formatted = format_compact_summary(merged)
    assert "Previously compacted context" in formatted
    assert "Newly compacted context" in formatted
