"""
realtalk.api — Layer 3: LLM API client (streaming).

Defines the ApiClient Protocol and concrete implementations:
  - AnthropicClient  — real Anthropic SDK streaming client
  - MockClient       — scripted event sequences for tests

No project dependencies. Pure I/O adapter between the conversation loop and the API.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Protocol, Sequence, runtime_checkable


# ---------------------------------------------------------------------------
# Event types emitted by the stream
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TextDelta:
    """Incremental text chunk from the assistant."""

    text: str


@dataclass(frozen=True)
class ToolUse:
    """Model is requesting a tool call.

    ``input`` is a raw JSON string. The tool executor owns parsing it.
    """

    id: str
    name: str
    input: str  # raw JSON string


@dataclass(frozen=True)
class UsageEvent:
    """Token usage for this API call."""

    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0


@dataclass(frozen=True)
class MessageStop:
    """The model has finished its response for this API call."""

    stop_reason: str = "end_turn"


# Union of all event types
AssistantEvent = TextDelta | ToolUse | UsageEvent | MessageStop


# ---------------------------------------------------------------------------
# Request type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ApiRequest:
    """Everything the API needs to produce a response."""

    system_prompt: list[str]
    messages: list[dict[str, object]]  # Anthropic message dicts
    tools: list[dict[str, object]]     # Anthropic tool definition dicts
    model: str = "claude-opus-4-6"
    max_tokens: int = 8096


# ---------------------------------------------------------------------------
# Protocol — the seam between the conversation loop and any LLM backend
# ---------------------------------------------------------------------------


@runtime_checkable
class ApiClient(Protocol):
    """Structural interface for any streaming LLM client.

    Any class with a matching ``stream`` signature satisfies this protocol
    without inheriting from it. Tests inject MockClient; production uses
    AnthropicClient.
    """

    def stream(self, request: ApiRequest) -> Iterator[AssistantEvent]:
        """Yield AssistantEvents as they arrive from the model."""
        ...


# ---------------------------------------------------------------------------
# MockClient — test double
# ---------------------------------------------------------------------------


class MockClient:
    """Scripted event sequences for unit tests.

    Events are returned in order; the client is exhausted after one call.
    Use a new instance per test turn.

    >>> events = [TextDelta("hi"), MessageStop()]
    >>> client = MockClient(events)
    >>> list(client.stream(ApiRequest(system_prompt=[], messages=[], tools=[])))
    [TextDelta(text='hi'), MessageStop(stop_reason='end_turn')]
    """

    def __init__(self, events: Sequence[AssistantEvent]) -> None:
        self._events = list(events)

    def stream(self, request: ApiRequest) -> Iterator[AssistantEvent]:  # noqa: ARG002
        yield from self._events


# ---------------------------------------------------------------------------
# AnthropicClient — production implementation
# ---------------------------------------------------------------------------


class AnthropicClient:
    """Streaming Anthropic SDK client.

    Converts the SDK's streaming event model into our internal AssistantEvent
    union. Sync iterator only — no async.

    Args:
        api_key: Anthropic API key. Defaults to ANTHROPIC_API_KEY env var.
    """

    def __init__(self, api_key: str | None = None) -> None:
        raise NotImplementedError(
            "AnthropicClient is a stub. Implement by wrapping anthropic.Anthropic."
        )

    def stream(self, request: ApiRequest) -> Iterator[AssistantEvent]:
        """Stream events from the Anthropic API.

        Implementation notes:
        - Use anthropic.Anthropic().messages.stream() context manager
        - Yield TextDelta for text_delta events
        - Yield ToolUse when input_json_delta is complete
        - Yield UsageEvent from message_start usage
        - Yield MessageStop on message_stop
        """
        raise NotImplementedError
        yield  # make mypy happy — this is a generator stub
