"""
realtalk.session — Layer 0: canonical session data types and interfaces.

This module is the source of truth for all conversation state. It is:
- Lossless
- Typed
- Append-only friendly
- Stable under replay
- Easy to validate
- Easy to export into training formats

It does NOT know about APIs, tools, UI rendering, compaction, or permissions.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Iterable, Iterator, Mapping

# ---------------------------------------------------------------------------
# JSON types
# ---------------------------------------------------------------------------

JSONPrimitive = str | int | float | bool | None
JSONValue = JSONPrimitive | list["JSONValue"] | dict[str, "JSONValue"]


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class SessionError(Exception):
    """Base class for all session errors."""


class SessionValidationError(SessionError):
    """Raised when a session event stream violates invariants."""


class SerializationError(SessionError):
    """Raised on serialization or deserialization failure."""


class ReplayError(SessionError):
    """Raised when replay encounters an unrecoverable inconsistency."""


class ExportError(SessionError):
    """Raised when an export cannot be produced from the given session."""


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------


def _make_id(prefix: str) -> str:
    """Generate a globally unique prefixed ID.

    >>> _make_id("sess").startswith("sess_")
    True
    >>> _make_id("evt") != _make_id("evt")
    True
    """
    return f"{prefix}_{uuid.uuid4().hex}"


def _now_iso() -> str:
    """Return current UTC time as an ISO 8601 string."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class TurnStatus(StrEnum):
    OPEN = "open"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


class FeedbackSource(StrEnum):
    USER = "user"
    REVIEWER = "reviewer"
    RULE = "rule"
    MODEL = "model"


# ---------------------------------------------------------------------------
# Content parts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TextPart:
    content_id: str
    text: str


@dataclass(frozen=True)
class ToolCallPart:
    content_id: str
    tool_call_id: str
    tool_name: str
    input_json: str


@dataclass(frozen=True)
class ToolResultPart:
    content_id: str
    tool_result_id: str
    tool_call_id: str
    output_text: str
    is_error: bool


ContentPart = TextPart | ToolCallPart | ToolResultPart


# ---------------------------------------------------------------------------
# Envelope shared by all events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EventEnvelope:
    event_id: str
    session_id: str
    event_type: str
    timestamp: str
    sequence: int
    trace_id: str | None = None
    parent_event_id: str | None = None


# ---------------------------------------------------------------------------
# Canonical events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionStarted:
    envelope: EventEnvelope
    workspace_root: str
    game_name: str
    model_hint: str | None
    metadata: Mapping[str, JSONValue]


@dataclass(frozen=True)
class TurnStarted:
    envelope: EventEnvelope
    turn_id: str
    opened_by: str | None
    metadata: Mapping[str, JSONValue]


@dataclass(frozen=True)
class MessageAdded:
    envelope: EventEnvelope
    message_id: str
    turn_id: str | None
    role: MessageRole
    parts: tuple[ContentPart, ...]
    metadata: Mapping[str, JSONValue]


@dataclass(frozen=True)
class ToolCallRecorded:
    envelope: EventEnvelope
    turn_id: str
    tool_call_id: str
    message_id: str | None
    tool_name: str
    input_json: str
    metadata: Mapping[str, JSONValue]


@dataclass(frozen=True)
class ToolResultRecorded:
    envelope: EventEnvelope
    turn_id: str
    tool_result_id: str
    tool_call_id: str
    message_id: str | None
    output_text: str
    is_error: bool
    metadata: Mapping[str, JSONValue]


@dataclass(frozen=True)
class FeedbackRecorded:
    envelope: EventEnvelope
    feedback_id: str
    turn_id: str
    target_message_id: str | None
    target_tool_call_id: str | None
    rating: int | None
    label: str | None
    correction_text: str | None
    source: FeedbackSource
    metadata: Mapping[str, JSONValue]


@dataclass(frozen=True)
class TurnEnded:
    envelope: EventEnvelope
    turn_id: str
    status: TurnStatus
    final_message_id: str | None
    metadata: Mapping[str, JSONValue]


@dataclass(frozen=True)
class SessionMetadataUpdated:
    envelope: EventEnvelope
    patch: Mapping[str, JSONValue]


SessionEvent = (
    SessionStarted
    | TurnStarted
    | MessageAdded
    | ToolCallRecorded
    | ToolResultRecorded
    | FeedbackRecorded
    | TurnEnded
    | SessionMetadataUpdated
)


# ---------------------------------------------------------------------------
# Session — the canonical container
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Session:
    session_id: str
    created_at: str
    workspace_root: str
    game_name: str
    model_hint: str | None
    events: tuple[SessionEvent, ...]
    metadata: Mapping[str, JSONValue]


# ---------------------------------------------------------------------------
# Derived entities
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Message:
    message_id: str
    turn_id: str | None
    role: MessageRole
    parts: tuple[ContentPart, ...]
    created_at: str
    caused_by_event_id: str
    metadata: Mapping[str, JSONValue]


@dataclass(frozen=True)
class Turn:
    turn_id: str
    session_id: str
    opened_by_message_id: str | None
    closed_at: str | None
    status: TurnStatus
    message_ids: tuple[str, ...]
    tool_call_ids: tuple[str, ...]
    tool_result_ids: tuple[str, ...]
    feedback_ids: tuple[str, ...]
    metadata: Mapping[str, JSONValue]


@dataclass(frozen=True)
class FeedbackRecord:
    feedback_id: str
    turn_id: str
    target_message_id: str | None
    target_tool_call_id: str | None
    rating: int | None
    label: str | None
    correction_text: str | None
    created_at: str
    source: FeedbackSource
    metadata: Mapping[str, JSONValue]


@dataclass(frozen=True)
class SessionView:
    session_id: str
    messages: tuple[Message, ...]
    turns: tuple[Turn, ...]
    feedback: tuple[FeedbackRecord, ...]
    tool_calls: Mapping[str, ToolCallRecorded]
    tool_results: Mapping[str, ToolResultRecorded]
    metadata: Mapping[str, JSONValue]


# ---------------------------------------------------------------------------
# Training export types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SFTExample:
    example_id: str
    session_id: str
    turn_id: str
    messages: tuple[Mapping[str, JSONValue], ...]
    metadata: Mapping[str, JSONValue]


@dataclass(frozen=True)
class PreferenceExample:
    example_id: str
    session_id: str
    turn_id: str
    prompt_messages: tuple[Mapping[str, JSONValue], ...]
    chosen: Mapping[str, JSONValue]
    rejected: Mapping[str, JSONValue]
    metadata: Mapping[str, JSONValue]


@dataclass(frozen=True)
class TrajectoryExample:
    example_id: str
    session_id: str
    turn_id: str
    steps: tuple[Mapping[str, JSONValue], ...]
    final_outcome: Mapping[str, JSONValue]
    reward_signals: tuple[Mapping[str, JSONValue], ...]
    metadata: Mapping[str, JSONValue]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _next_sequence(session: Session) -> int:
    return len(session.events)


def _make_envelope(
    session: Session,
    event_type: str,
    trace_id: str | None = None,
    parent_event_id: str | None = None,
) -> EventEnvelope:
    return EventEnvelope(
        event_id=_make_id("evt"),
        session_id=session.session_id,
        event_type=event_type,
        timestamp=_now_iso(),
        sequence=_next_sequence(session),
        trace_id=trace_id,
        parent_event_id=parent_event_id,
    )


# ---------------------------------------------------------------------------
# Constructors
# ---------------------------------------------------------------------------


def new_session(
    workspace_root: str,
    game_name: str,
    model_hint: str | None = None,
    metadata: Mapping[str, JSONValue] | None = None,
) -> Session:
    """Create a new empty session with a session_started event.

    >>> s = new_session("/tmp/game", "realtalk")
    >>> s.session_id.startswith("sess_")
    True
    >>> len(s.events)
    1
    >>> s.events[0].envelope.event_type
    'session_started'
    >>> s.game_name
    'realtalk'
    """
    session_id = _make_id("sess")
    created_at = _now_iso()
    meta: Mapping[str, JSONValue] = metadata or {}

    envelope = EventEnvelope(
        event_id=_make_id("evt"),
        session_id=session_id,
        event_type="session_started",
        timestamp=created_at,
        sequence=0,
    )
    started = SessionStarted(
        envelope=envelope,
        workspace_root=workspace_root,
        game_name=game_name,
        model_hint=model_hint,
        metadata=meta,
    )
    return Session(
        session_id=session_id,
        created_at=created_at,
        workspace_root=workspace_root,
        game_name=game_name,
        model_hint=model_hint,
        events=(started,),
        metadata=meta,
    )


def append_event(session: Session, event: SessionEvent) -> Session:
    """Append a single event, returning a new immutable Session.

    >>> s = new_session("/tmp/game", "realtalk")
    >>> len(s.events)
    1
    >>> s2, turn_id = start_turn(s)
    >>> len(s2.events)
    2
    >>> s2 is not s
    True
    """
    return replace(session, events=session.events + (event,))


def start_turn(
    session: Session,
    opened_by: str | None = None,
    metadata: Mapping[str, JSONValue] | None = None,
) -> tuple[Session, str]:
    """Open a new turn, returning the updated session and the new turn_id.

    >>> s = new_session("/tmp/game", "realtalk")
    >>> s, turn_id = start_turn(s)
    >>> turn_id.startswith("turn_")
    True
    >>> s.events[-1].envelope.event_type
    'turn_started'
    """
    turn_id = _make_id("turn")
    envelope = _make_envelope(session, "turn_started")
    event = TurnStarted(
        envelope=envelope,
        turn_id=turn_id,
        opened_by=opened_by,
        metadata=metadata or {},
    )
    return append_event(session, event), turn_id


def add_user_text(
    session: Session,
    turn_id: str,
    text: str,
    metadata: Mapping[str, JSONValue] | None = None,
) -> tuple[Session, str]:
    """Add a user text message to the session.

    >>> s = new_session("/tmp/game", "realtalk")
    >>> s, turn_id = start_turn(s)
    >>> s, msg_id = add_user_text(s, turn_id, "hello")
    >>> msg_id.startswith("msg_")
    True
    >>> s.events[-1].role.value
    'user'
    """
    msg_id = _make_id("msg")
    content_id = _make_id("cnt")
    envelope = _make_envelope(session, "message_added")
    event = MessageAdded(
        envelope=envelope,
        message_id=msg_id,
        turn_id=turn_id,
        role=MessageRole.USER,
        parts=(TextPart(content_id=content_id, text=text),),
        metadata=metadata or {},
    )
    return append_event(session, event), msg_id


def add_assistant_text(
    session: Session,
    turn_id: str,
    text: str,
    metadata: Mapping[str, JSONValue] | None = None,
) -> tuple[Session, str]:
    """Add an assistant text message to the session.

    >>> s = new_session("/tmp/game", "realtalk")
    >>> s, turn_id = start_turn(s)
    >>> s, _ = add_user_text(s, turn_id, "hi")
    >>> s, msg_id = add_assistant_text(s, turn_id, "Hello!")
    >>> s.events[-1].role.value
    'assistant'
    """
    msg_id = _make_id("msg")
    content_id = _make_id("cnt")
    envelope = _make_envelope(session, "message_added")
    event = MessageAdded(
        envelope=envelope,
        message_id=msg_id,
        turn_id=turn_id,
        role=MessageRole.ASSISTANT,
        parts=(TextPart(content_id=content_id, text=text),),
        metadata=metadata or {},
    )
    return append_event(session, event), msg_id


def record_tool_call(
    session: Session,
    turn_id: str,
    tool_name: str,
    input_json: str,
    message_id: str | None = None,
    metadata: Mapping[str, JSONValue] | None = None,
) -> tuple[Session, str]:
    """Record that the assistant invoked a tool.

    >>> s = new_session("/tmp/game", "realtalk")
    >>> s, turn_id = start_turn(s)
    >>> s, call_id = record_tool_call(s, turn_id, "Bash", '{"command": "ls"}')
    >>> call_id.startswith("call_")
    True
    """
    call_id = _make_id("call")
    envelope = _make_envelope(session, "tool_call_recorded")
    event = ToolCallRecorded(
        envelope=envelope,
        turn_id=turn_id,
        tool_call_id=call_id,
        message_id=message_id,
        tool_name=tool_name,
        input_json=input_json,
        metadata=metadata or {},
    )
    return append_event(session, event), call_id


def record_tool_result(
    session: Session,
    turn_id: str,
    tool_call_id: str,
    output_text: str,
    is_error: bool = False,
    message_id: str | None = None,
    metadata: Mapping[str, JSONValue] | None = None,
) -> tuple[Session, str]:
    """Record the result of a tool call. Raises SessionValidationError if
    tool_call_id does not reference a known tool call.

    >>> s = new_session("/tmp/game", "realtalk")
    >>> s, turn_id = start_turn(s)
    >>> s, call_id = record_tool_call(s, turn_id, "Bash", '{"command": "ls"}')
    >>> s, res_id = record_tool_result(s, turn_id, call_id, "file.txt")
    >>> res_id.startswith("res_")
    True
    """
    # Validate reference
    known_calls = {
        e.tool_call_id
        for e in session.events
        if isinstance(e, ToolCallRecorded)
    }
    if tool_call_id not in known_calls:
        raise SessionValidationError(
            f"tool_call_id {tool_call_id!r} does not reference a known tool call"
        )

    res_id = _make_id("res")
    envelope = _make_envelope(session, "tool_result_recorded")
    event = ToolResultRecorded(
        envelope=envelope,
        turn_id=turn_id,
        tool_result_id=res_id,
        tool_call_id=tool_call_id,
        message_id=message_id,
        output_text=output_text,
        is_error=is_error,
        metadata=metadata or {},
    )
    return append_event(session, event), res_id


def record_feedback(
    session: Session,
    turn_id: str,
    source: FeedbackSource,
    target_message_id: str | None = None,
    target_tool_call_id: str | None = None,
    rating: int | None = None,
    label: str | None = None,
    correction_text: str | None = None,
    metadata: Mapping[str, JSONValue] | None = None,
) -> tuple[Session, str]:
    """Attach feedback to a turn (or a specific message or tool call within it).

    >>> s = new_session("/tmp/game", "realtalk")
    >>> s, turn_id = start_turn(s)
    >>> s, _ = add_user_text(s, turn_id, "go")
    >>> s, msg_id = add_assistant_text(s, turn_id, "You go.")
    >>> s, fb_id = record_feedback(s, turn_id, FeedbackSource.USER, target_message_id=msg_id, rating=5)
    >>> fb_id.startswith("fb_")
    True
    """
    fb_id = _make_id("fb")
    envelope = _make_envelope(session, "feedback_recorded")
    event = FeedbackRecorded(
        envelope=envelope,
        feedback_id=fb_id,
        turn_id=turn_id,
        target_message_id=target_message_id,
        target_tool_call_id=target_tool_call_id,
        rating=rating,
        label=label,
        correction_text=correction_text,
        source=source,
        metadata=metadata or {},
    )
    return append_event(session, event), fb_id


def end_turn(
    session: Session,
    turn_id: str,
    status: TurnStatus,
    final_message_id: str | None = None,
    metadata: Mapping[str, JSONValue] | None = None,
) -> Session:
    """Close a turn with the given status.

    >>> s = new_session("/tmp/game", "realtalk")
    >>> s, turn_id = start_turn(s)
    >>> s, _ = add_user_text(s, turn_id, "hunt")
    >>> s, msg_id = add_assistant_text(s, turn_id, "You search.")
    >>> s = end_turn(s, turn_id, TurnStatus.COMPLETED, final_message_id=msg_id)
    >>> s.events[-1].envelope.event_type
    'turn_ended'
    >>> s.events[-1].status.value
    'completed'
    """
    envelope = _make_envelope(session, "turn_ended")
    event = TurnEnded(
        envelope=envelope,
        turn_id=turn_id,
        status=status,
        final_message_id=final_message_id,
        metadata=metadata or {},
    )
    return append_event(session, event)


def update_session_metadata(
    session: Session,
    patch: Mapping[str, JSONValue],
) -> Session:
    """Apply a metadata patch to the session.

    >>> s = new_session("/tmp/game", "realtalk")
    >>> s = update_session_metadata(s, {"score": 42})
    >>> s.events[-1].envelope.event_type
    'session_metadata_updated'
    """
    envelope = _make_envelope(session, "session_metadata_updated")
    event = SessionMetadataUpdated(envelope=envelope, patch=patch)
    return append_event(session, event)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_session(session: Session) -> None:
    """Validate all structural invariants of a session's event stream.

    Raises SessionValidationError describing the first violation found.

    >>> s = new_session("/tmp/game", "realtalk")
    >>> validate_session(s)
    """
    events = session.events

    if not events:
        raise SessionValidationError("Session has no events")
    if not isinstance(events[0], SessionStarted):
        raise SessionValidationError("First event must be SessionStarted")

    seen_event_ids: set[str] = set()
    seen_turn_ids: set[str] = set()
    open_turn_ids: set[str] = set()
    closed_turn_ids: set[str] = set()
    seen_message_ids: set[str] = set()
    seen_tool_call_ids: set[str] = set()
    seen_tool_result_ids: set[str] = set()
    seen_feedback_ids: set[str] = set()

    for i, event in enumerate(events):
        env = event.envelope

        # Invariant: sequence must be strictly increasing
        if env.sequence != i:
            raise SessionValidationError(
                f"Event at index {i} has sequence {env.sequence}; expected {i}"
            )

        # Invariant: all events share the same session_id
        if env.session_id != session.session_id:
            raise SessionValidationError(
                f"Event {env.event_id} has session_id {env.session_id!r}; "
                f"expected {session.session_id!r}"
            )

        # Invariant: event_id values must be unique
        if env.event_id in seen_event_ids:
            raise SessionValidationError(f"Duplicate event_id: {env.event_id!r}")
        seen_event_ids.add(env.event_id)

        if isinstance(event, TurnStarted):
            if event.turn_id in seen_turn_ids:
                raise SessionValidationError(
                    f"Duplicate turn_id: {event.turn_id!r}"
                )
            seen_turn_ids.add(event.turn_id)
            open_turn_ids.add(event.turn_id)

        elif isinstance(event, TurnEnded):
            if event.turn_id not in open_turn_ids:
                if event.turn_id in closed_turn_ids:
                    raise SessionValidationError(
                        f"Turn {event.turn_id!r} ended twice"
                    )
                raise SessionValidationError(
                    f"TurnEnded references unknown or already-closed turn {event.turn_id!r}"
                )
            open_turn_ids.remove(event.turn_id)
            closed_turn_ids.add(event.turn_id)

        elif isinstance(event, MessageAdded):
            if event.message_id in seen_message_ids:
                raise SessionValidationError(
                    f"Duplicate message_id: {event.message_id!r}"
                )
            seen_message_ids.add(event.message_id)

            if event.turn_id is not None:
                if event.turn_id not in seen_turn_ids:
                    raise SessionValidationError(
                        f"MessageAdded references unknown turn_id {event.turn_id!r}"
                    )

        elif isinstance(event, ToolCallRecorded):
            if event.tool_call_id in seen_tool_call_ids:
                raise SessionValidationError(
                    f"Duplicate tool_call_id: {event.tool_call_id!r}"
                )
            seen_tool_call_ids.add(event.tool_call_id)
            if event.turn_id not in seen_turn_ids:
                raise SessionValidationError(
                    f"ToolCallRecorded references unknown turn_id {event.turn_id!r}"
                )

        elif isinstance(event, ToolResultRecorded):
            if event.tool_result_id in seen_tool_result_ids:
                raise SessionValidationError(
                    f"Duplicate tool_result_id: {event.tool_result_id!r}"
                )
            seen_tool_result_ids.add(event.tool_result_id)
            if event.tool_call_id not in seen_tool_call_ids:
                raise SessionValidationError(
                    f"ToolResultRecorded references unknown tool_call_id {event.tool_call_id!r}"
                )
            if event.turn_id not in seen_turn_ids:
                raise SessionValidationError(
                    f"ToolResultRecorded references unknown turn_id {event.turn_id!r}"
                )

        elif isinstance(event, FeedbackRecorded):
            if event.feedback_id in seen_feedback_ids:
                raise SessionValidationError(
                    f"Duplicate feedback_id: {event.feedback_id!r}"
                )
            seen_feedback_ids.add(event.feedback_id)
            if event.turn_id not in seen_turn_ids:
                raise SessionValidationError(
                    f"FeedbackRecorded references unknown turn_id {event.turn_id!r}"
                )
            if (
                event.target_message_id is not None
                and event.target_message_id not in seen_message_ids
            ):
                raise SessionValidationError(
                    f"FeedbackRecorded target_message_id {event.target_message_id!r} not found"
                )
            if (
                event.target_tool_call_id is not None
                and event.target_tool_call_id not in seen_tool_call_ids
            ):
                raise SessionValidationError(
                    f"FeedbackRecorded target_tool_call_id {event.target_tool_call_id!r} not found"
                )


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


def replay(session: Session) -> SessionView:
    """Rebuild the current session view from the event log.

    Replay is a pure fold over the event stream.

    >>> s = new_session("/tmp/game", "realtalk")
    >>> s, turn_id = start_turn(s)
    >>> s, _ = add_user_text(s, turn_id, "cross the river")
    >>> s, _ = add_assistant_text(s, turn_id, "You ford the river carefully.")
    >>> s = end_turn(s, turn_id, TurnStatus.COMPLETED)
    >>> view = replay(s)
    >>> len(view.turns)
    1
    >>> view.messages[-1].role.value
    'assistant'
    >>> view.turns[0].status.value
    'completed'
    """
    validate_session(session)

    messages_map: dict[str, Message] = {}
    message_order: list[str] = []
    turns_map: dict[str, dict] = {}  # turn_id -> mutable builder dict
    turn_order: list[str] = []
    tool_calls: dict[str, ToolCallRecorded] = {}
    tool_results: dict[str, ToolResultRecorded] = {}
    feedback_map: dict[str, FeedbackRecord] = {}
    feedback_order: list[str] = []
    session_metadata: dict[str, JSONValue] = {}

    for event in session.events:
        if isinstance(event, SessionStarted):
            session_metadata.update(event.metadata)

        elif isinstance(event, TurnStarted):
            turns_map[event.turn_id] = {
                "turn_id": event.turn_id,
                "session_id": session.session_id,
                "opened_by_message_id": None,
                "closed_at": None,
                "status": TurnStatus.OPEN,
                "message_ids": [],
                "tool_call_ids": [],
                "tool_result_ids": [],
                "feedback_ids": [],
                "metadata": dict(event.metadata),
            }
            turn_order.append(event.turn_id)

        elif isinstance(event, MessageAdded):
            msg = Message(
                message_id=event.message_id,
                turn_id=event.turn_id,
                role=event.role,
                parts=event.parts,
                created_at=event.envelope.timestamp,
                caused_by_event_id=event.envelope.event_id,
                metadata=event.metadata,
            )
            messages_map[event.message_id] = msg
            message_order.append(event.message_id)

            if event.turn_id and event.turn_id in turns_map:
                turns_map[event.turn_id]["message_ids"].append(event.message_id)

        elif isinstance(event, ToolCallRecorded):
            tool_calls[event.tool_call_id] = event
            if event.turn_id in turns_map:
                turns_map[event.turn_id]["tool_call_ids"].append(event.tool_call_id)

        elif isinstance(event, ToolResultRecorded):
            tool_results[event.tool_result_id] = event
            if event.turn_id in turns_map:
                turns_map[event.turn_id]["tool_result_ids"].append(event.tool_result_id)

        elif isinstance(event, FeedbackRecorded):
            fb = FeedbackRecord(
                feedback_id=event.feedback_id,
                turn_id=event.turn_id,
                target_message_id=event.target_message_id,
                target_tool_call_id=event.target_tool_call_id,
                rating=event.rating,
                label=event.label,
                correction_text=event.correction_text,
                created_at=event.envelope.timestamp,
                source=event.source,
                metadata=event.metadata,
            )
            feedback_map[event.feedback_id] = fb
            feedback_order.append(event.feedback_id)

            if event.turn_id in turns_map:
                turns_map[event.turn_id]["feedback_ids"].append(event.feedback_id)

        elif isinstance(event, TurnEnded):
            t = turns_map[event.turn_id]
            t["status"] = event.status
            t["closed_at"] = event.envelope.timestamp
            if event.final_message_id:
                t["opened_by_message_id"] = event.final_message_id

        elif isinstance(event, SessionMetadataUpdated):
            session_metadata.update(event.patch)

    # Freeze turns
    turns = tuple(
        Turn(
            turn_id=b["turn_id"],
            session_id=b["session_id"],
            opened_by_message_id=b["opened_by_message_id"],
            closed_at=b["closed_at"],
            status=b["status"],
            message_ids=tuple(b["message_ids"]),
            tool_call_ids=tuple(b["tool_call_ids"]),
            tool_result_ids=tuple(b["tool_result_ids"]),
            feedback_ids=tuple(b["feedback_ids"]),
            metadata=b["metadata"],
        )
        for b in (turns_map[tid] for tid in turn_order)
    )

    return SessionView(
        session_id=session.session_id,
        messages=tuple(messages_map[mid] for mid in message_order),
        turns=turns,
        feedback=tuple(feedback_map[fid] for fid in feedback_order),
        tool_calls=tool_calls,
        tool_results=tool_results,
        metadata=session_metadata,
    )


def derive_messages(session: Session) -> list[Message]:
    """Return messages in event order.

    >>> s = new_session("/tmp/game", "realtalk")
    >>> s, turn_id = start_turn(s)
    >>> s, _ = add_user_text(s, turn_id, "hi")
    >>> s, _ = add_assistant_text(s, turn_id, "hello")
    >>> msgs = derive_messages(s)
    >>> [m.role.value for m in msgs]
    ['user', 'assistant']
    """
    return list(replay(session).messages)


def derive_turns(session: Session) -> list[Turn]:
    """Return turns in event order.

    >>> s = new_session("/tmp/game", "realtalk")
    >>> s, tid = start_turn(s)
    >>> s = end_turn(s, tid, TurnStatus.COMPLETED)
    >>> turns = derive_turns(s)
    >>> len(turns)
    1
    >>> turns[0].status.value
    'completed'
    """
    return list(replay(session).turns)


def derive_feedback(session: Session) -> list[FeedbackRecord]:
    """Return feedback records in event order.

    >>> s = new_session("/tmp/game", "realtalk")
    >>> s, tid = start_turn(s)
    >>> s, mid = add_assistant_text(s, tid, "Try again.")
    >>> s, _ = record_feedback(s, tid, FeedbackSource.USER, target_message_id=mid, rating=3)
    >>> fb = derive_feedback(s)
    >>> len(fb)
    1
    >>> fb[0].rating
    3
    """
    return list(replay(session).feedback)


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def _content_part_to_dict(part: ContentPart) -> dict[str, JSONValue]:
    if isinstance(part, TextPart):
        return {"type": "text", "content_id": part.content_id, "text": part.text}
    elif isinstance(part, ToolCallPart):
        return {
            "type": "tool_call",
            "content_id": part.content_id,
            "tool_call_id": part.tool_call_id,
            "tool_name": part.tool_name,
            "input_json": part.input_json,
        }
    elif isinstance(part, ToolResultPart):
        return {
            "type": "tool_result",
            "content_id": part.content_id,
            "tool_result_id": part.tool_result_id,
            "tool_call_id": part.tool_call_id,
            "output_text": part.output_text,
            "is_error": part.is_error,
        }
    raise SerializationError(f"Unknown ContentPart type: {type(part)}")


def _content_part_from_dict(data: dict[str, JSONValue]) -> ContentPart:
    t = data["type"]
    if t == "text":
        return TextPart(content_id=str(data["content_id"]), text=str(data["text"]))
    elif t == "tool_call":
        return ToolCallPart(
            content_id=str(data["content_id"]),
            tool_call_id=str(data["tool_call_id"]),
            tool_name=str(data["tool_name"]),
            input_json=str(data["input_json"]),
        )
    elif t == "tool_result":
        return ToolResultPart(
            content_id=str(data["content_id"]),
            tool_result_id=str(data["tool_result_id"]),
            tool_call_id=str(data["tool_call_id"]),
            output_text=str(data["output_text"]),
            is_error=bool(data["is_error"]),
        )
    raise SerializationError(f"Unknown content part type: {t!r}")


def _envelope_to_dict(env: EventEnvelope) -> dict[str, JSONValue]:
    d: dict[str, JSONValue] = {
        "event_id": env.event_id,
        "session_id": env.session_id,
        "event_type": env.event_type,
        "timestamp": env.timestamp,
        "sequence": env.sequence,
    }
    if env.trace_id is not None:
        d["trace_id"] = env.trace_id
    if env.parent_event_id is not None:
        d["parent_event_id"] = env.parent_event_id
    return d


def _envelope_from_dict(data: dict[str, JSONValue]) -> EventEnvelope:
    return EventEnvelope(
        event_id=str(data["event_id"]),
        session_id=str(data["session_id"]),
        event_type=str(data["event_type"]),
        timestamp=str(data["timestamp"]),
        sequence=int(data["sequence"]),  # type: ignore[arg-type]
        trace_id=str(data["trace_id"]) if "trace_id" in data and data["trace_id"] is not None else None,
        parent_event_id=str(data["parent_event_id"]) if "parent_event_id" in data and data["parent_event_id"] is not None else None,
    )


def event_to_dict(event: SessionEvent) -> dict[str, JSONValue]:
    """Serialize a SessionEvent to a plain dict.

    >>> s = new_session("/tmp/game", "realtalk")
    >>> d = event_to_dict(s.events[0])
    >>> d["event_type"]
    'session_started'
    >>> isinstance(d, dict)
    True
    """
    env = event.envelope
    base = _envelope_to_dict(env)

    if isinstance(event, SessionStarted):
        base["workspace_root"] = event.workspace_root
        base["game_name"] = event.game_name
        base["model_hint"] = event.model_hint
        base["metadata"] = dict(event.metadata)

    elif isinstance(event, TurnStarted):
        base["turn_id"] = event.turn_id
        base["opened_by"] = event.opened_by
        base["metadata"] = dict(event.metadata)

    elif isinstance(event, MessageAdded):
        base["message_id"] = event.message_id
        base["turn_id"] = event.turn_id
        base["role"] = event.role.value
        base["parts"] = [_content_part_to_dict(p) for p in event.parts]
        base["metadata"] = dict(event.metadata)

    elif isinstance(event, ToolCallRecorded):
        base["turn_id"] = event.turn_id
        base["tool_call_id"] = event.tool_call_id
        base["message_id"] = event.message_id
        base["tool_name"] = event.tool_name
        base["input_json"] = event.input_json
        base["metadata"] = dict(event.metadata)

    elif isinstance(event, ToolResultRecorded):
        base["turn_id"] = event.turn_id
        base["tool_result_id"] = event.tool_result_id
        base["tool_call_id"] = event.tool_call_id
        base["message_id"] = event.message_id
        base["output_text"] = event.output_text
        base["is_error"] = event.is_error
        base["metadata"] = dict(event.metadata)

    elif isinstance(event, FeedbackRecorded):
        base["feedback_id"] = event.feedback_id
        base["turn_id"] = event.turn_id
        base["target_message_id"] = event.target_message_id
        base["target_tool_call_id"] = event.target_tool_call_id
        base["rating"] = event.rating
        base["label"] = event.label
        base["correction_text"] = event.correction_text
        base["source"] = event.source.value
        base["metadata"] = dict(event.metadata)

    elif isinstance(event, TurnEnded):
        base["turn_id"] = event.turn_id
        base["status"] = event.status.value
        base["final_message_id"] = event.final_message_id
        base["metadata"] = dict(event.metadata)

    elif isinstance(event, SessionMetadataUpdated):
        base["patch"] = dict(event.patch)

    else:
        raise SerializationError(f"Unknown event type: {type(event)}")

    return base


def event_from_dict(data: dict[str, JSONValue]) -> SessionEvent:
    """Deserialize a SessionEvent from a plain dict.

    >>> s = new_session("/tmp/game", "realtalk")
    >>> orig = s.events[0]
    >>> restored = event_from_dict(event_to_dict(orig))
    >>> restored == orig
    True
    """
    env = _envelope_from_dict(data)
    et = env.event_type

    if et == "session_started":
        return SessionStarted(
            envelope=env,
            workspace_root=str(data["workspace_root"]),
            game_name=str(data["game_name"]),
            model_hint=str(data["model_hint"]) if data.get("model_hint") is not None else None,
            metadata=dict(data.get("metadata", {})),  # type: ignore[arg-type]
        )

    elif et == "turn_started":
        return TurnStarted(
            envelope=env,
            turn_id=str(data["turn_id"]),
            opened_by=str(data["opened_by"]) if data.get("opened_by") is not None else None,
            metadata=dict(data.get("metadata", {})),  # type: ignore[arg-type]
        )

    elif et == "message_added":
        raw_parts = data.get("parts", [])
        parts = tuple(
            _content_part_from_dict(p)  # type: ignore[arg-type]
            for p in (raw_parts if isinstance(raw_parts, list) else [])
        )
        return MessageAdded(
            envelope=env,
            message_id=str(data["message_id"]),
            turn_id=str(data["turn_id"]) if data.get("turn_id") is not None else None,
            role=MessageRole(str(data["role"])),
            parts=parts,
            metadata=dict(data.get("metadata", {})),  # type: ignore[arg-type]
        )

    elif et == "tool_call_recorded":
        return ToolCallRecorded(
            envelope=env,
            turn_id=str(data["turn_id"]),
            tool_call_id=str(data["tool_call_id"]),
            message_id=str(data["message_id"]) if data.get("message_id") is not None else None,
            tool_name=str(data["tool_name"]),
            input_json=str(data["input_json"]),
            metadata=dict(data.get("metadata", {})),  # type: ignore[arg-type]
        )

    elif et == "tool_result_recorded":
        return ToolResultRecorded(
            envelope=env,
            turn_id=str(data["turn_id"]),
            tool_result_id=str(data["tool_result_id"]),
            tool_call_id=str(data["tool_call_id"]),
            message_id=str(data["message_id"]) if data.get("message_id") is not None else None,
            output_text=str(data["output_text"]),
            is_error=bool(data["is_error"]),
            metadata=dict(data.get("metadata", {})),  # type: ignore[arg-type]
        )

    elif et == "feedback_recorded":
        return FeedbackRecorded(
            envelope=env,
            feedback_id=str(data["feedback_id"]),
            turn_id=str(data["turn_id"]),
            target_message_id=str(data["target_message_id"]) if data.get("target_message_id") is not None else None,
            target_tool_call_id=str(data["target_tool_call_id"]) if data.get("target_tool_call_id") is not None else None,
            rating=int(data["rating"]) if data.get("rating") is not None else None,  # type: ignore[arg-type]
            label=str(data["label"]) if data.get("label") is not None else None,
            correction_text=str(data["correction_text"]) if data.get("correction_text") is not None else None,
            source=FeedbackSource(str(data["source"])),
            metadata=dict(data.get("metadata", {})),  # type: ignore[arg-type]
        )

    elif et == "turn_ended":
        return TurnEnded(
            envelope=env,
            turn_id=str(data["turn_id"]),
            status=TurnStatus(str(data["status"])),
            final_message_id=str(data["final_message_id"]) if data.get("final_message_id") is not None else None,
            metadata=dict(data.get("metadata", {})),  # type: ignore[arg-type]
        )

    elif et == "session_metadata_updated":
        return SessionMetadataUpdated(
            envelope=env,
            patch=dict(data.get("patch", {})),  # type: ignore[arg-type]
        )

    raise SerializationError(f"Unknown event_type: {et!r}")


def session_to_jsonl(session: Session) -> Iterator[str]:
    """Yield one JSON line per event.

    >>> s = new_session("/tmp/game", "realtalk")
    >>> lines = list(session_to_jsonl(s))
    >>> len(lines)
    1
    >>> import json; json.loads(lines[0])["event_type"]
    'session_started'
    """
    for event in session.events:
        yield json.dumps(event_to_dict(event))


def session_from_jsonl(lines: Iterable[str], *, skip_errors: bool = False) -> Session:
    """Reconstruct a Session from a JSONL event stream.

    When *skip_errors* is True, invalid JSON lines are silently skipped
    instead of raising.  This is used by the storage layer to recover
    sessions after a crash that left a truncated final line.

    >>> s = new_session("/tmp/game", "realtalk")
    >>> s, turn_id = start_turn(s)
    >>> s, _ = add_user_text(s, turn_id, "hello")
    >>> s2 = session_from_jsonl(session_to_jsonl(s))
    >>> len(s2.events) == len(s.events)
    True
    >>> s2.session_id == s.session_id
    True
    """
    events: list[SessionEvent] = []
    session_id: str | None = None
    created_at: str | None = None
    workspace_root = ""
    game_name = ""
    model_hint: str | None = None
    metadata: dict[str, JSONValue] = {}

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            if skip_errors:
                continue
            raise SerializationError(f"Invalid JSON line: {exc}") from exc

        event = event_from_dict(data)
        events.append(event)

        if isinstance(event, SessionStarted):
            session_id = event.envelope.session_id
            created_at = event.envelope.timestamp
            workspace_root = event.workspace_root
            game_name = event.game_name
            model_hint = event.model_hint
            metadata = dict(event.metadata)

    if session_id is None or created_at is None:
        raise SerializationError("JSONL stream missing SessionStarted event")

    return Session(
        session_id=session_id,
        created_at=created_at,
        workspace_root=workspace_root,
        game_name=game_name,
        model_hint=model_hint,
        events=tuple(events),
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# Training exports
# ---------------------------------------------------------------------------


def _messages_up_to_turn(view: SessionView, turn_id: str) -> list[dict[str, JSONValue]]:
    """Return all messages up through (but not including) the given turn's messages."""
    turn = next((t for t in view.turns if t.turn_id == turn_id), None)
    if turn is None:
        raise ExportError(f"Turn {turn_id!r} not found in session view")

    turn_message_ids = set(turn.message_ids)
    result = []
    for msg in view.messages:
        if msg.message_id in turn_message_ids:
            break
        if msg.parts:
            text = " ".join(
                p.text for p in msg.parts if isinstance(p, TextPart)
            )
            result.append({"role": msg.role.value, "content": text})
    return result


def _turn_to_chat_messages(
    view: SessionView, turn: Turn
) -> list[dict[str, JSONValue]]:
    """Convert a single turn's messages to chat-format dicts."""
    rows: list[dict[str, JSONValue]] = []
    for mid in turn.message_ids:
        msg = next((m for m in view.messages if m.message_id == mid), None)
        if msg is None:
            continue
        text = " ".join(p.text for p in msg.parts if isinstance(p, TextPart))
        rows.append({"role": msg.role.value, "content": text})
    return rows


def to_sft_examples(session: Session) -> list[SFTExample]:
    """Produce supervised fine-tuning rows for each completed turn.

    Each row is a chat message sequence ending with the assistant's response.

    >>> s = new_session("/tmp/game", "realtalk")
    >>> s, turn_id = start_turn(s)
    >>> s, _ = add_user_text(s, turn_id, "go west")
    >>> s, _ = add_assistant_text(s, turn_id, "You head into the plains.")
    >>> s = end_turn(s, turn_id, TurnStatus.COMPLETED)
    >>> rows = to_sft_examples(s)
    >>> len(rows)
    1
    >>> rows[0].messages[-1]["role"]
    'assistant'
    """
    view = replay(session)
    examples: list[SFTExample] = []

    for turn in view.turns:
        if turn.status != TurnStatus.COMPLETED:
            continue

        context: list[dict[str, JSONValue]] = []
        for msg in view.messages:
            if msg.turn_id == turn.turn_id:
                break
            if msg.parts:
                text = " ".join(p.text for p in msg.parts if isinstance(p, TextPart))
                context.append({"role": msg.role.value, "content": text})

        turn_msgs = _turn_to_chat_messages(view, turn)
        all_msgs = context + turn_msgs

        if not all_msgs:
            continue
        if all_msgs[-1]["role"] != "assistant":
            continue

        examples.append(
            SFTExample(
                example_id=_make_id("sft"),
                session_id=session.session_id,
                turn_id=turn.turn_id,
                messages=tuple(all_msgs),
                metadata={},
            )
        )

    return examples


def to_preference_examples(session: Session) -> list[PreferenceExample]:
    """Produce preference pairs from turns with explicit user ratings.

    Requires at least two feedback records on the same turn with different ratings
    (one rated higher as 'chosen', one lower as 'rejected'), or a correction pair.

    >>> s = new_session("/tmp/game", "realtalk")
    >>> s, tid = start_turn(s)
    >>> s, _ = add_user_text(s, tid, "go")
    >>> s, msg_id = add_assistant_text(s, tid, "You go.")
    >>> s, _ = record_feedback(s, tid, FeedbackSource.USER, target_message_id=msg_id, rating=5)
    >>> s = end_turn(s, tid, TurnStatus.COMPLETED)
    >>> examples = to_preference_examples(s)
    >>> len(examples)
    0
    """
    view = replay(session)
    examples: list[PreferenceExample] = []

    for turn in view.turns:
        rated = [
            fb for fb in view.feedback
            if fb.turn_id == turn.turn_id and fb.rating is not None
        ]
        if len(rated) < 2:
            continue

        rated_sorted = sorted(rated, key=lambda fb: fb.rating or 0)
        worst = rated_sorted[0]
        best = rated_sorted[-1]

        def _fb_to_msg(fb: FeedbackRecord) -> dict[str, JSONValue]:
            if fb.target_message_id:
                msg = next(
                    (m for m in view.messages if m.message_id == fb.target_message_id),
                    None,
                )
                if msg:
                    text = " ".join(p.text for p in msg.parts if isinstance(p, TextPart))
                    return {"role": msg.role.value, "content": text}
            return {"role": "assistant", "content": ""}

        context: list[dict[str, JSONValue]] = []
        for msg in view.messages:
            if msg.turn_id == turn.turn_id:
                break
            if msg.parts:
                text = " ".join(p.text for p in msg.parts if isinstance(p, TextPart))
                context.append({"role": msg.role.value, "content": text})

        examples.append(
            PreferenceExample(
                example_id=_make_id("pref"),
                session_id=session.session_id,
                turn_id=turn.turn_id,
                prompt_messages=tuple(context),
                chosen=_fb_to_msg(best),
                rejected=_fb_to_msg(worst),
                metadata={"chosen_rating": best.rating, "rejected_rating": worst.rating},
            )
        )

    return examples


def to_trajectory_examples(session: Session) -> list[TrajectoryExample]:
    """Produce trajectory rows that include all action-observation steps per turn.

    Each step includes user messages, assistant messages, tool calls, and tool results.
    Reward signals are populated from any attached feedback.

    >>> s = new_session("/tmp/game", "realtalk")
    >>> s, tid = start_turn(s)
    >>> s, _ = add_user_text(s, tid, "hunt")
    >>> s, _ = add_assistant_text(s, tid, "You search for food.")
    >>> s = end_turn(s, tid, TurnStatus.COMPLETED)
    >>> rows = to_trajectory_examples(s)
    >>> len(rows)
    1
    >>> rows[0].final_outcome["status"]
    'completed'
    """
    view = replay(session)
    examples: list[TrajectoryExample] = []

    for turn in view.turns:
        steps: list[dict[str, JSONValue]] = []

        for mid in turn.message_ids:
            msg = next((m for m in view.messages if m.message_id == mid), None)
            if msg is None:
                continue
            text = " ".join(p.text for p in msg.parts if isinstance(p, TextPart))
            steps.append({"type": "message", "role": msg.role.value, "content": text})

        for call_id in turn.tool_call_ids:
            call = view.tool_calls.get(call_id)
            if call:
                steps.append({
                    "type": "tool_call",
                    "tool_name": call.tool_name,
                    "input_json": call.input_json,
                })

        for res_id in turn.tool_result_ids:
            res = view.tool_results.get(res_id)
            if res:
                steps.append({
                    "type": "tool_result",
                    "tool_call_id": res.tool_call_id,
                    "output_text": res.output_text,
                    "is_error": res.is_error,
                })

        reward_signals: list[dict[str, JSONValue]] = [
            {
                "feedback_id": fb.feedback_id,
                "source": fb.source.value,
                "rating": fb.rating,
                "label": fb.label,
            }
            for fb in view.feedback
            if fb.turn_id == turn.turn_id
        ]

        examples.append(
            TrajectoryExample(
                example_id=_make_id("traj"),
                session_id=session.session_id,
                turn_id=turn.turn_id,
                steps=tuple(steps),
                final_outcome={"status": turn.status.value, "closed_at": turn.closed_at},
                reward_signals=tuple(reward_signals),
                metadata={},
            )
        )

    return examples
