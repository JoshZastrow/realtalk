"""
realtalk.api — Layer 3: LLM API client (streaming).

Defines the ApiClient Protocol and concrete implementations:
  - AnthropicClient  — real Anthropic SDK streaming client
  - MockClient       — scripted event sequences for tests

No project dependencies. Pure I/O adapter between the conversation loop and the API.
"""

from __future__ import annotations

from dataclasses import dataclass
import time
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
# ScriptedClient — multi-turn test double
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
    Auto-detects API keys from environment variables (ANTHROPIC_API_KEY, OPENAI_API_KEY,
    etc).

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
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.5,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.api_key = api_key
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds

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

        # Build OpenAI-format messages (litellm's common format)
        messages: list[dict] = []
        if request.system_prompt:
            messages.append({
                "role": "system",
                "content": _build_system_content(request.system_prompt, self.model),
            })
        messages.extend(_to_openai_messages(request.messages))

        tools = _to_openai_tools(request.tools) if request.tools else None
        if tools and _supports_cache_control(self.model):
            # Mark the last tool to cache the whole tools block (Anthropic
            # caches the contiguous prefix up to and including this marker).
            tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}

        try:
            response = None
            for attempt in range(self.max_retries + 1):
                try:
                    kwargs: dict = dict(
                        model=self.model,
                        messages=messages,
                        tools=tools,
                        stream=True,
                        temperature=self.temperature,
                        max_tokens=self.max_tokens,
                    )
                    if self.api_key is not None:
                        kwargs["api_key"] = self.api_key
                    response = litellm.completion(**kwargs)
                    break
                except Exception as exc:
                    if not _is_retryable_error(exc) or attempt >= self.max_retries:
                        raise
                    time.sleep(self.retry_backoff_seconds * (attempt + 1))
            assert response is not None

            tool_input_buffer = ""
            tool_id = ""
            tool_name = ""

            for event in response:
                # Handle both dict and object events (litellm returns OpenAI-compatible objects)
                # Try dict access first, fall back to attribute access
                def get_attr(obj, key, default=None):
                    if isinstance(obj, dict):
                        return obj.get(key, default)
                    return getattr(obj, key, default)

                # Process usage events
                usage = get_attr(event, "usage")
                if usage:
                    yield UsageEvent(
                        input_tokens=get_attr(usage, "prompt_tokens", 0),
                        output_tokens=get_attr(usage, "completion_tokens", 0),
                        cache_creation_tokens=get_attr(usage, "cache_creation_input_tokens", 0),
                        cache_read_tokens=get_attr(usage, "cache_read_input_tokens", 0),
                    )

                # Process choice deltas
                choices = get_attr(event, "choices", [])
                if choices:
                    for choice in choices:
                        finish_reason = get_attr(choice, "finish_reason")
                        if finish_reason:
                            # Message completed
                            if finish_reason != "tool_calls":
                                yield MessageStop(stop_reason=finish_reason)
                        else:
                            delta = get_attr(choice, "delta")
                            if delta:
                                # Text content
                                content = get_attr(delta, "content")
                                if content:
                                    yield TextDelta(text=content)

                                # Tool use (accumulate input JSON across chunks)
                                tool_calls = get_attr(delta, "tool_calls", [])
                                if tool_calls:
                                    for tool_call in tool_calls:
                                        call_id = get_attr(tool_call, "id")
                                        if call_id:
                                            tool_id = call_id
                                        func = get_attr(tool_call, "function")
                                        if func:
                                            func_name = get_attr(func, "name")
                                            if func_name:
                                                tool_name = func_name
                                            func_args = get_attr(func, "arguments")
                                            if func_args:
                                                tool_input_buffer += func_args
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


def _to_openai_messages(messages: list[dict[str, object]]) -> list[dict[str, object]]:
    """Convert Anthropic-format messages to OpenAI format for litellm.

    Anthropic uses content blocks (tool_use/tool_result) in assistant/user messages.
    OpenAI uses tool_calls on assistant messages and role="tool" for results.
    """
    import json as _json

    result: list[dict[str, object]] = []
    for msg in messages:
        role = msg["role"]
        content = msg["content"]

        if isinstance(content, str):
            result.append({"role": role, "content": content})
            continue

        blocks: list[dict] = list(content)  # type: ignore[arg-type]

        if role == "assistant":
            text = " ".join(b["text"] for b in blocks if b.get("type") == "text" and b.get("text"))
            tool_calls = [
                {
                    "id": b["id"],
                    "type": "function",
                    "function": {
                        "name": b["name"],
                        "arguments": _json.dumps(b["input"]) if isinstance(b.get("input"), dict) else str(b.get("input", "{}")),
                    },
                }
                for b in blocks if b.get("type") == "tool_use"
            ]
            new_msg: dict[str, object] = {"role": "assistant", "content": text}
            if tool_calls:
                new_msg["tool_calls"] = tool_calls
            result.append(new_msg)

        elif role == "user":
            tool_results = [b for b in blocks if b.get("type") == "tool_result"]
            text_blocks = [b for b in blocks if b.get("type") == "text"]

            if text_blocks:
                text = " ".join(b.get("text", "") for b in text_blocks)  # type: ignore[union-attr]
                result.append({"role": "user", "content": text})

            for tr in tool_results:
                result.append({
                    "role": "tool",
                    "tool_call_id": tr["tool_use_id"],
                    "content": tr.get("content", ""),
                })
        else:
            result.append({"role": role, "content": content})

    return result


def _to_openai_tools(tools: list[dict[str, object]]) -> list[dict[str, object]]:
    """Convert Anthropic tool definitions to OpenAI format for litellm.

    Anthropic: {"name": ..., "description": ..., "input_schema": {...}}
    OpenAI:    {"type": "function", "function": {"name": ..., "description": ..., "parameters": {...}}}
    """
    result = []
    for t in tools:
        if "input_schema" in t:
            result.append({
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t["input_schema"],
                },
            })
        else:
            result.append(t)
    return result


# Sentinel string emitted by realtalk.prompt.SystemPromptBuilder between the
# static rules/memory prefix and the dynamic per-turn context. Matched as a
# literal to avoid a Layer 3 → Layer 5 import.
_DYNAMIC_BOUNDARY = "SYSTEM_PROMPT_DYNAMIC_BOUNDARY"


def _supports_cache_control(model: str) -> bool:
    """Only Anthropic models honor cache_control via litellm."""
    m = model.lower()
    return "claude" in m or "anthropic" in m


def _build_system_content(
    sections: list[str], model: str
) -> str | list[dict[str, object]]:
    """Build the system message content.

    For Anthropic models, split `sections` at the dynamic boundary and mark
    the static prefix with ``cache_control: ephemeral``. Everything after the
    boundary (scene, role, game state) goes in an un-cached part so turn-level
    churn doesn't invalidate the cached prefix.

    For non-Anthropic models, return a plain string (litellm format-compat).
    """
    if not _supports_cache_control(model):
        return "\n".join(sections)

    if _DYNAMIC_BOUNDARY in sections:
        idx = sections.index(_DYNAMIC_BOUNDARY)
        static = sections[:idx]
        dynamic = sections[idx + 1 :]
    else:
        static = sections
        dynamic = []

    parts: list[dict[str, object]] = []
    if any(s.strip() for s in static):
        parts.append({
            "type": "text",
            "text": "\n".join(static),
            "cache_control": {"type": "ephemeral"},
        })
    if any(s.strip() for s in dynamic):
        parts.append({"type": "text", "text": "\n".join(dynamic)})
    return parts if parts else ""


def _is_retryable_error(error: Exception) -> bool:
    message = str(error).lower()
    return any(
        token in message
        for token in (
            "connection reset by peer",
            "connection refused",
            "internalservererror",
            "timeout",
            "temporarily unavailable",
            "server disconnected",
            "apiconnectionerror",
        )
    )


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
