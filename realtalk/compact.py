"""Session compaction that keeps summaries in the system prompt, not messages."""

from __future__ import annotations

from dataclasses import dataclass, replace

from realtalk.session import (
    Message,
    MessageAdded,
    MessageRole,
    Session,
    SessionEvent,
    TextPart,
    ToolCallPart,
    ToolCallRecorded,
    ToolResultPart,
    ToolResultRecorded,
    TurnEnded,
    TurnStarted,
    derive_messages,
)

COMPACT_CONTINUATION_PREAMBLE = (
    "This session is being continued from a previous conversation that ran out of context. "
    "The summary below covers the earlier portion of the conversation.\n\n"
)
COMPACT_RECENT_MESSAGES_NOTE = "Recent messages are preserved verbatim."
COMPACT_DIRECT_RESUME_INSTRUCTION = (
    "Continue the conversation from where it left off without asking the user "
    "any further questions. Resume directly -- do not acknowledge the summary, "
    "do not recap what was happening, and do not preface with continuation text."
)


@dataclass(frozen=True)
class CompactionConfig:
    preserve_recent_messages: int = 4
    max_estimated_tokens: int = 80_000


@dataclass(frozen=True)
class CompactionResult:
    summary_sections: list[str]
    formatted_summary: str
    compacted_session: Session
    removed_message_count: int


def estimate_event_tokens(event: SessionEvent) -> int:
    if isinstance(event, MessageAdded):
        return sum(_estimate_part_tokens(part) for part in event.parts)
    if isinstance(event, ToolCallRecorded):
        return (len(event.tool_name) + len(event.input_json)) // 4 + 1
    if isinstance(event, ToolResultRecorded):
        return (len(event.tool_call_id) + len(event.output_text)) // 4 + 1
    return 0


def estimate_session_tokens(session: Session) -> int:
    return sum(estimate_event_tokens(event) for event in session.events)


def should_compact(
    session: Session,
    config: CompactionConfig,
    existing_summary_sections: list[str] | None = None,
) -> bool:
    del existing_summary_sections
    messages = derive_messages(session)
    if len(messages) <= config.preserve_recent_messages:
        return False
    token_sum = sum(_estimate_message_tokens(message) for message in messages)
    return token_sum >= config.max_estimated_tokens


def compact_session(
    session: Session,
    config: CompactionConfig,
    existing_summary_sections: list[str] | None = None,
) -> CompactionResult:
    if not should_compact(session, config, existing_summary_sections):
        return CompactionResult(
            summary_sections=list(existing_summary_sections or []),
            formatted_summary="",
            compacted_session=session,
            removed_message_count=0,
        )

    messages = derive_messages(session)
    raw_keep_from = max(0, len(messages) - config.preserve_recent_messages)
    keep_from = _safe_boundary(messages, raw_keep_from)
    removed = messages[:keep_from]
    preserved = messages[keep_from:]

    new_summary = summarize_messages(removed)
    merged = merge_compact_summaries(existing_summary_sections or [], new_summary)
    formatted_summary = format_compact_summary(merged)

    return CompactionResult(
        summary_sections=build_summary_sections(merged, recent_preserved=bool(preserved)),
        formatted_summary=formatted_summary,
        compacted_session=_rebuild_session(session, preserved),
        removed_message_count=len(removed),
    )


def format_compact_summary(summary: str) -> str:
    summary = _strip_tag_block(summary, "analysis")
    content = _extract_tag_block(summary, "summary")
    if content is None:
        return summary.strip()
    formatted = "Summary:\n" + content.strip()
    return "\n".join(line for line in formatted.splitlines() if line.strip()).strip()


def build_summary_sections(summary: str, recent_preserved: bool) -> list[str]:
    sections = [
        COMPACT_CONTINUATION_PREAMBLE,
        format_compact_summary(summary),
    ]
    if recent_preserved:
        sections.append(COMPACT_RECENT_MESSAGES_NOTE)
    sections.append(COMPACT_DIRECT_RESUME_INSTRUCTION)
    return sections


def summarize_messages(messages: list[Message]) -> str:
    user_count = sum(1 for message in messages if message.role == MessageRole.USER)
    assistant_count = sum(1 for message in messages if message.role == MessageRole.ASSISTANT)
    tool_names = sorted({part.tool_name for msg in messages for part in msg.parts if isinstance(part, ToolCallPart)})
    recent_user = [
        _summarize_message_content(message)
        for message in messages
        if message.role == MessageRole.USER and _message_has_text(message)
    ][-3:]
    pending = [
        _summarize_message_content(message)
        for message in messages
        if "todo" in _summarize_message_content(message).lower()
        or "remaining" in _summarize_message_content(message).lower()
        or "next" in _summarize_message_content(message).lower()
    ]

    lines = [
        "<summary>",
        "Conversation summary:",
        f"- Scope: {len(messages)} earlier messages compacted (user={user_count}, assistant={assistant_count}).",
    ]
    if tool_names:
        lines.append(f"- Tools mentioned: {', '.join(tool_names)}.")
    if recent_user:
        lines.append("- Recent user requests:")
        lines.extend(f"  - {item}" for item in recent_user)
    if pending:
        lines.append("- Pending work:")
        lines.extend(f"  - {item}" for item in pending[-3:])
    lines.append("- Key timeline:")
    lines.extend(
        f"  - {message.role.value}: {_summarize_message_content(message)}"
        for message in messages
    )
    lines.append("</summary>")
    return "\n".join(lines)


def merge_compact_summaries(existing: list[str] | str | None, new_summary: str) -> str:
    if not existing:
        return new_summary
    if isinstance(existing, str):
        previous = format_compact_summary(existing)
    else:
        previous = "\n".join(existing).strip()
    return (
        "<summary>\nConversation summary:\n"
        "- Previously compacted context:\n"
        f"  - {previous.replace(chr(10), chr(10) + '  - ')}\n"
        "- Newly compacted context:\n"
        f"  - {format_compact_summary(new_summary).replace(chr(10), chr(10) + '  - ')}\n"
        "</summary>"
    )


def get_compact_continuation_message(
    summary: str,
    suppress_follow_up_questions: bool = True,
    recent_messages_preserved: bool = True,
) -> str:
    sections = [COMPACT_CONTINUATION_PREAMBLE, format_compact_summary(summary)]
    if recent_messages_preserved:
        sections.append(COMPACT_RECENT_MESSAGES_NOTE)
    if suppress_follow_up_questions:
        sections.append(COMPACT_DIRECT_RESUME_INSTRUCTION)
    return "\n\n".join(sections)


def _rebuild_session(original: Session, preserved_messages: list[Message]) -> Session:
    if not preserved_messages:
        return original

    preserve_ids = {message.message_id for message in preserved_messages}
    preserved_turn_ids = {message.turn_id for message in preserved_messages if message.turn_id is not None}
    preserved_call_ids = {
        part.tool_call_id
        for message in preserved_messages
        for part in message.parts
        if isinstance(part, ToolCallPart)
    }
    preserved_result_call_ids = {
        part.tool_call_id
        for message in preserved_messages
        for part in message.parts
        if isinstance(part, ToolResultPart)
    }
    preserved_call_ids.update(preserved_result_call_ids)

    kept_events: list[SessionEvent] = [original.events[0]]
    for event in original.events[1:]:
        if isinstance(event, (TurnStarted, TurnEnded)):
            if event.turn_id in preserved_turn_ids:
                kept_events.append(event)
        elif isinstance(event, MessageAdded):
            if event.message_id in preserve_ids:
                kept_events.append(event)
        elif isinstance(event, ToolCallRecorded):
            if event.turn_id in preserved_turn_ids and event.tool_call_id in preserved_call_ids:
                kept_events.append(event)
        elif isinstance(event, ToolResultRecorded):
            if event.turn_id in preserved_turn_ids and event.tool_call_id in preserved_call_ids:
                kept_events.append(event)
        else:
            kept_events.append(event)
    kept_events = [
        replace(event, envelope=replace(event.envelope, sequence=index))
        for index, event in enumerate(kept_events)
    ]
    return Session(
        session_id=original.session_id,
        created_at=original.created_at,
        workspace_root=original.workspace_root,
        game_name=original.game_name,
        model_hint=original.model_hint,
        events=tuple(kept_events),
        metadata=original.metadata,
    )


def _safe_boundary(messages: list[Message], raw_keep_from: int) -> int:
    keep_from = raw_keep_from
    while keep_from > 0:
        first_preserved = messages[keep_from]
        if not any(isinstance(part, ToolResultPart) for part in first_preserved.parts):
            break
        keep_from -= 1
    return keep_from


def _estimate_message_tokens(message: Message) -> int:
    return sum(_estimate_part_tokens(part) for part in message.parts)


def _estimate_part_tokens(part: TextPart | ToolCallPart | ToolResultPart) -> int:
    if isinstance(part, TextPart):
        size = len(part.text)
    elif isinstance(part, ToolCallPart):
        size = len(part.tool_name) + len(part.input_json)
    else:
        size = len(part.tool_call_id) + len(part.output_text)
    return size // 4 + 1


def _extract_tag_block(text: str, tag: str) -> str | None:
    start_tag = f"<{tag}>"
    end_tag = f"</{tag}>"
    start = text.find(start_tag)
    end = text.find(end_tag)
    if start == -1 or end == -1 or end < start:
        return None
    return text[start + len(start_tag):end]


def _strip_tag_block(text: str, tag: str) -> str:
    content = _extract_tag_block(text, tag)
    if content is None:
        return text
    return text.replace(f"<{tag}>{content}</{tag}>", "")


def _message_has_text(message: Message) -> bool:
    return any(isinstance(part, TextPart) and part.text.strip() for part in message.parts)


def _summarize_message_content(message: Message) -> str:
    chunks: list[str] = []
    for part in message.parts:
        if isinstance(part, TextPart):
            chunks.append(part.text.strip())
        elif isinstance(part, ToolCallPart):
            chunks.append(f"tool:{part.tool_name}")
        elif isinstance(part, ToolResultPart):
            chunks.append(f"tool_result:{part.output_text.strip()[:60]}")
    summary = " | ".join(chunk for chunk in chunks if chunk)
    return summary[:160] if summary else "(empty)"
