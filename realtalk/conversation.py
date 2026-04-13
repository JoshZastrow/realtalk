"""
realtalk.conversation -- Layer 4: the conversation engine.

Drives the turn loop: user input -> LLM response -> tool execution -> repeat.
Bridges the immutable event-sourced Session with the streaming ApiClient.

Dependencies: session.py (Layer 0), api.py (Layer 3).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, Protocol, runtime_checkable

from realtalk.api import (
    ApiClient,
    ApiRequest,
    MessageStop,
    TextDelta,
    ToolUse,
    UsageEvent,
)
from realtalk.session import (
    MessageAdded,
    MessageRole,
    Session,
    SessionEvent,
    ToolCallRecorded,
    ToolResultRecorded,
    TurnStatus,
    add_assistant_text,
    add_user_text,
    end_turn,
    record_tool_call,
    record_tool_result,
    start_turn,
)


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class ToolExecutor(Protocol):
    """Structural interface for tool dispatch.

    In the game: executes game mechanics (mood updates, option generation).
    In tests: returns canned responses.
    """

    def execute(self, tool_name: str, tool_input: str) -> str:
        """Execute a tool and return the result as a string.

        tool_input is a raw JSON string. The executor owns parsing it.
        Returns the result text. For errors, raise -- the runtime catches
        exceptions and records them as error tool results.
        """
        ...


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolCallInfo:
    """Record of a single tool call within a turn."""

    tool_call_id: str
    tool_name: str
    input_json: str
    output_text: str
    is_error: bool


@dataclass(frozen=True)
class TurnSummary:
    """What happened during a single run_turn() call."""

    turn_id: str
    iterations: int
    tool_calls: tuple[ToolCallInfo, ...]
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    hit_iteration_limit: bool
    status: TurnStatus


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class NoOpExecutor:
    """Returns empty string for any tool call. Use when tool execution is irrelevant."""

    def execute(self, tool_name: str, tool_input: str) -> str:
        return ""


class EchoExecutor:
    """Returns the tool name and input as the output. Verifies input flows through."""

    def execute(self, tool_name: str, tool_input: str) -> str:
        return f"{tool_name}: {tool_input}"


class StaticExecutor:
    """Pre-configured responses per tool name. Raises KeyError for unknown tools.

    >>> ex = StaticExecutor({"update_mood": "mood is now 65"})
    >>> ex.execute("update_mood", '{"delta": 5}')
    'mood is now 65'
    """

    def __init__(self, responses: dict[str, str]) -> None:
        self._responses = responses

    def execute(self, tool_name: str, tool_input: str) -> str:
        return self._responses[tool_name]


# ---------------------------------------------------------------------------
# Message formatting: Session events -> Anthropic API format
# ---------------------------------------------------------------------------


def format_session_for_api(session: Session) -> list[dict[str, object]]:
    """Build Anthropic-format messages from the full session event stream.

    Walks session.events directly (not derive_messages) because the API
    format requires interleaving tool_use blocks in assistant messages
    and tool_result blocks in user messages.

    Returns a list of dicts ready for ApiRequest.messages.
    """
    output: list[dict[str, object]] = []

    for event in session.events:
        if isinstance(event, MessageAdded):
            if event.role == MessageRole.USER:
                # Plain text user message
                text = " ".join(p.text for p in event.parts)
                _append_message(output, "user", text)
            elif event.role == MessageRole.ASSISTANT:
                # Start a new assistant content block list
                text = " ".join(p.text for p in event.parts)
                blocks: list[dict[str, object]] = []
                if text:
                    blocks.append({"type": "text", "text": text})
                _append_content_blocks(output, "assistant", blocks)

        elif isinstance(event, ToolCallRecorded):
            # Append tool_use block to the current assistant message
            try:
                parsed_input = json.loads(event.input_json)
            except (json.JSONDecodeError, TypeError):
                parsed_input = event.input_json
            block: dict[str, object] = {
                "type": "tool_use",
                "id": event.tool_call_id,
                "name": event.tool_name,
                "input": parsed_input,
            }
            _append_content_blocks(output, "assistant", [block])

        elif isinstance(event, ToolResultRecorded):
            # Tool results go into user-role messages
            block = {
                "type": "tool_result",
                "tool_use_id": event.tool_call_id,
                "content": event.output_text,
            }
            if event.is_error:
                block["is_error"] = True
            _append_content_blocks(output, "user", [block])

    return output


def _append_message(
    output: list[dict[str, object]], role: str, text: str
) -> None:
    """Append a plain-text message, merging into the previous if same role."""
    if output and output[-1]["role"] == role:
        # Merge into existing -- convert to content blocks if needed
        prev = output[-1]
        if isinstance(prev["content"], str):
            prev["content"] = prev["content"] + "\n" + text
        else:
            prev["content"].append({"type": "text", "text": text})  # type: ignore[union-attr]
    else:
        output.append({"role": role, "content": text})


def _append_content_blocks(
    output: list[dict[str, object]],
    role: str,
    blocks: list[dict[str, object]],
) -> None:
    """Append content blocks to the last message if same role, else start a new one."""
    if output and output[-1]["role"] == role:
        prev_content = output[-1]["content"]
        if isinstance(prev_content, list):
            prev_content.extend(blocks)
        else:
            # Convert string content to blocks format
            new_blocks: list[dict[str, object]] = [
                {"type": "text", "text": str(prev_content)}
            ]
            new_blocks.extend(blocks)
            output[-1]["content"] = new_blocks
    else:
        output.append({"role": role, "content": list(blocks)})


# ---------------------------------------------------------------------------
# ConversationRuntime -- the engine
# ---------------------------------------------------------------------------


class ConversationRuntime:
    """The conversation engine. Drives the turn loop.

    Takes an ApiClient and ToolExecutor as constructor arguments. Neither is
    hardcoded. In tests, inject MockClient/ScriptedClient and NoOpExecutor.
    In production, inject AnthropicClient and the game's tool registry.

    The runtime holds a mutable reference to the current (immutable) Session.
    Each session mutation rebinds self._session to the new snapshot.
    """

    def __init__(
        self,
        api_client: ApiClient,
        tool_executor: ToolExecutor,
        session: Session,
        system_prompt: list[str],
        tool_definitions: list[dict[str, object]],
        on_text: Callable[[str], None],
        model: str = "claude-haiku-4-5-20251001",
        max_tokens: int = 8096,
        temperature: float = 1.0,
        max_iterations: int = 10,
    ) -> None:
        self._api_client = api_client
        self._tool_executor = tool_executor
        self._session = session
        self._system_prompt = system_prompt
        self._tool_definitions = tool_definitions
        self._on_text = on_text
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._max_iterations = max_iterations

    @property
    def session(self) -> Session:
        """The current session snapshot (read-only)."""
        return self._session

    def run_turn(self, user_input: str) -> TurnSummary:
        """Execute one full turn: user input -> assistant response(s) -> tool execution loop."""
        # 1. Open turn
        self._session, turn_id = start_turn(self._session, opened_by="player")

        # 2. Record user input
        self._session, _ = add_user_text(self._session, turn_id, user_input)

        # Accumulators
        total_input_tokens = 0
        total_output_tokens = 0
        total_cache_creation = 0
        total_cache_read = 0
        all_tool_calls: list[ToolCallInfo] = []
        iterations = 0
        hit_limit = False

        # 3. Inner loop
        while iterations < self._max_iterations:
            iterations += 1

            # 3a. Format messages
            api_messages = format_session_for_api(self._session)

            # 3b. Build request
            request = ApiRequest(
                system_prompt=self._system_prompt,
                messages=api_messages,
                tools=self._tool_definitions,
                model=self._model,
                max_tokens=self._max_tokens,
                temperature=self._temperature,
            )

            # 3c. Stream response
            text_buffer: list[str] = []
            pending_tool_uses: list[ToolUse] = []

            for event in self._api_client.stream(request):
                if isinstance(event, TextDelta):
                    text_buffer.append(event.text)
                    self._on_text(event.text)
                elif isinstance(event, ToolUse):
                    pending_tool_uses.append(event)
                elif isinstance(event, UsageEvent):
                    total_input_tokens += event.input_tokens
                    total_output_tokens += event.output_tokens
                    total_cache_creation += event.cache_creation_tokens
                    total_cache_read += event.cache_read_tokens
                elif isinstance(event, MessageStop):
                    break

            # 3d. Record assistant message (always, even if empty text)
            accumulated_text = "".join(text_buffer)
            self._session, _ = add_assistant_text(
                self._session, turn_id, accumulated_text
            )

            # Record tool calls
            call_ids: list[str] = []
            for tu in pending_tool_uses:
                self._session, call_id = record_tool_call(
                    self._session, turn_id, tu.name, tu.input
                )
                call_ids.append(call_id)

            # 3e. If no tool uses, break
            if not pending_tool_uses:
                break

            # 3f. Execute tools
            for tu, call_id in zip(pending_tool_uses, call_ids):
                try:
                    result_text = self._tool_executor.execute(tu.name, tu.input)
                    is_error = False
                except Exception as exc:
                    result_text = f"ERROR: {type(exc).__name__}: {exc}"
                    is_error = True

                self._session, _ = record_tool_result(
                    self._session, turn_id, call_id, result_text, is_error
                )

                all_tool_calls.append(
                    ToolCallInfo(
                        tool_call_id=call_id,
                        tool_name=tu.name,
                        input_json=tu.input,
                        output_text=result_text,
                        is_error=is_error,
                    )
                )

            # 3g/h. Check if we've hit the limit on the next iteration
            if iterations >= self._max_iterations:
                hit_limit = True
                break

        else:
            # while loop exhausted without break = hit iteration limit
            hit_limit = True

        # 4. Close turn
        status = TurnStatus.FAILED if hit_limit else TurnStatus.COMPLETED
        self._session = end_turn(self._session, turn_id, status)

        # 5. Return summary
        return TurnSummary(
            turn_id=turn_id,
            iterations=iterations,
            tool_calls=tuple(all_tool_calls),
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            cache_creation_tokens=total_cache_creation,
            cache_read_tokens=total_cache_read,
            hit_iteration_limit=hit_limit,
            status=status,
        )
