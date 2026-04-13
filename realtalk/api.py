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
    model: str = "claude-haiku-4-5-20251001"
    max_tokens: int = 8096
    temperature: float = 1.0


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
# ScriptedClient — multi-call test double
# ---------------------------------------------------------------------------


class ScriptedClient:
    """Serves different event sequences on successive stream() calls.

    Each stream() call pops the next sequence. Raises IndexError if called
    more times than there are sequences. Records each ApiRequest for assertions.

    >>> client = ScriptedClient([
    ...     [TextDelta("hi"), MessageStop()],
    ...     [TextDelta("bye"), MessageStop()],
    ... ])
    >>> list(client.stream(ApiRequest(system_prompt=[], messages=[], tools=[])))
    [TextDelta(text='hi'), MessageStop(stop_reason='end_turn')]
    >>> list(client.stream(ApiRequest(system_prompt=[], messages=[], tools=[])))
    [TextDelta(text='bye'), MessageStop(stop_reason='end_turn')]
    >>> client.call_count
    2
    """

    def __init__(self, sequences: list[list[AssistantEvent]]) -> None:
        self._sequences = list(sequences)
        self._index = 0
        self.requests: list[ApiRequest] = []

    @property
    def call_count(self) -> int:
        return self._index

    def stream(self, request: ApiRequest) -> Iterator[AssistantEvent]:
        if self._index >= len(self._sequences):
            raise IndexError(
                f"ScriptedClient exhausted: {self._index} calls made, "
                f"only {len(self._sequences)} sequences provided"
            )
        self.requests.append(request)
        events = self._sequences[self._index]
        self._index += 1
        yield from events


# ---------------------------------------------------------------------------
# AnthropicClient — production implementation
# ---------------------------------------------------------------------------


class LiteLLMClient:
    """Streaming LLM client using litellm.ai for multi-provider support.

    Supports any provider litellm.ai supports: Anthropic, OpenAI, Google, Llama, etc.
    Auto-detects API keys from environment variables (ANTHROPIC_API_KEY, OPENAI_API_KEY, etc).

    Args:
        model: Model identifier (e.g., "claude-3-5-sonnet-20241022")
        temperature: Sampling temperature, 0.0-2.0 (default 1.0)
        max_tokens: Maximum output tokens (default 8096)
        api_key: Optional explicit API key (overrides env)

    Example:
        >>> client = LiteLLMClient(model="claude-3-5-sonnet-20241022")  # doctest: +SKIP
        >>> request = ApiRequest(  # doctest: +SKIP
        ...     system_prompt=["You are helpful."],
        ...     messages=[{"role": "user", "content": "Hello"}],
        ...     tools=[],
        ...     model="claude-3-5-sonnet-20241022"
        ... )
        >>> events = list(client.stream(request))  # doctest: +SKIP
    """

    def __init__(
        self,
        model: str,
        temperature: float = 1.0,
        max_tokens: int = 8096,
        api_key: str | None = None,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.api_key = api_key

    def stream(self, request: ApiRequest) -> Iterator[AssistantEvent]:
        """Stream events from litellm, converting to our AssistantEvent format.

        Parses streaming SSE events and yields TextDelta, ToolUse, UsageEvent, and
        MessageStop as they arrive. Normalizes provider-specific differences.

        Raises:
            ImportError: If litellm is not installed
            RuntimeError: On API authentication or network errors
        """
        try:
            import litellm
        except ImportError:
            raise ImportError(
                "litellm is required. Install with: pip install 'litellm>=1.50'"
            )

        system_prompt = "\n".join(request.system_prompt) if request.system_prompt else ""
        messages = request.messages.copy()

        try:
            response = litellm.completion(
                model=self.model,
                messages=messages,
                system=system_prompt if system_prompt else None,
                tools=request.tools if request.tools else None,
                stream=True,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                api_key=self.api_key,
            )

            tool_input_buffer = ""
            tool_id = ""
            tool_name = ""

            for event in response:
                if isinstance(event, dict):
                    # Handle usage information
                    if "usage" in event:
                        usage = event["usage"]
                        yield UsageEvent(
                            input_tokens=usage.get("prompt_tokens", 0),
                            output_tokens=usage.get("completion_tokens", 0),
                            cache_creation_tokens=usage.get("cache_creation_input_tokens", 0),
                            cache_read_tokens=usage.get("cache_read_input_tokens", 0),
                        )

                    # Handle choice deltas (streaming content)
                    if "choices" in event:
                        for choice in event["choices"]:
                            if choice.get("finish_reason"):
                                # Message completed
                                if choice["finish_reason"] == "tool_calls":
                                    # Tool use already yielded
                                    pass
                                yield MessageStop(stop_reason=choice["finish_reason"])
                            else:
                                delta = choice.get("delta", {})

                                # Text content
                                if "content" in delta and delta["content"]:
                                    yield TextDelta(text=delta["content"])

                                # Tool use (accumulate input JSON across chunks)
                                if "tool_calls" in delta:
                                    for tool_call in delta["tool_calls"]:
                                        if "id" in tool_call:
                                            tool_id = tool_call["id"]
                                        if "function" in tool_call:
                                            if "name" in tool_call["function"]:
                                                tool_name = tool_call["function"]["name"]
                                            if "arguments" in tool_call["function"]:
                                                tool_input_buffer += tool_call["function"]["arguments"]
                                        # When we have all parts, yield the tool use
                                        if tool_id and tool_name and tool_input_buffer:
                                            try:
                                                import json
                                                json.loads(tool_input_buffer)  # Validate JSON
                                                yield ToolUse(
                                                    id=tool_id,
                                                    name=tool_name,
                                                    input=tool_input_buffer,
                                                )
                                                tool_input_buffer = ""
                                                tool_id = ""
                                                tool_name = ""
                                            except json.JSONDecodeError:
                                                # Still accumulating JSON
                                                pass

        except Exception as e:
            # Provide descriptive error messages
            error_msg = str(e)
            if "401" in error_msg or "authentication" in error_msg.lower():
                raise RuntimeError(
                    f"Authentication failed for model '{self.model}'. "
                    "Verify your API key is set and valid."
                ) from e
            elif "404" in error_msg or "not found" in error_msg.lower():
                raise RuntimeError(
                    f"Model '{self.model}' not found or not supported. "
                    "Check the model name and your provider."
                ) from e
            elif "rate limit" in error_msg.lower():
                raise RuntimeError(
                    f"Rate limited by {self.model} provider. Wait and retry."
                ) from e
            else:
                raise RuntimeError(
                    f"Failed to stream from {self.model}: {error_msg}"
                ) from e


class AnthropicClient:
    """[DEPRECATED] Streaming Anthropic SDK client.

    Use LiteLLMClient instead. Converts the SDK's streaming event model into our
    internal AssistantEvent union. Sync iterator only — no async.

    Args:
        api_key: Anthropic API key. Defaults to ANTHROPIC_API_KEY env var.
    """

    def __init__(self, api_key: str | None = None) -> None:
        raise NotImplementedError(
            "AnthropicClient is deprecated. Use LiteLLMClient instead."
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
