# Layer 4: Conversation Runtime

Implemented the conversation engine (`realtalk/conversation.py`) that drives the
turn loop: user input → LLM response → tool execution → session recording → repeat.
Also added `ScriptedClient` to `realtalk/api.py` as a multi-call test double.

## Steps taken

1. **Read the spec and context.**
   Read `docs/spec/v1.4.md` (Layer 4 scope) and `docs/prd/1.0.md` to understand
   the game loop mechanics, the tool-use pattern, and what "conversation runtime"
   means in terms of the session model. Reviewed Layers 0–3 (`session.py`,
   `storage.py`, `config.py`, `api.py`) to understand the interfaces this layer builds on.

2. **Surveyed existing state.**
   `api.py` already had `MockClient`, `ApiRequest`, and all event types (`TextDelta`,
   `ToolUse`, `UsageEvent`, `MessageStop`). Missing per spec:
   - `ScriptedClient` (multi-call test double for the tool loop)
   - `temperature: float = 1.0` field on `ApiRequest`
   - Model default was `"claude-opus-4-6"` — should be `"claude-haiku-4-5-20251001"`

   `conversation.py` did not exist. `tests/test_conversation.py` did not exist.

3. **Added `api.py` prerequisites.**
   - Added `ScriptedClient` alongside `MockClient`. Key design: `ScriptedClient`
     pops one event sequence per `stream()` call and records each `ApiRequest` for
     later inspection. Raises `IndexError` on exhaustion with a message that includes
     both the call count and the sequence count — makes test failures obvious.
   - Added `temperature: float = 1.0` to `ApiRequest`.
   - Fixed model default from `"claude-opus-4-6"` to `"claude-haiku-4-5-20251001"`.

4. **Implemented `realtalk/conversation.py`.**

   Implemented in dependency order — types first, then utilities, then the runtime:

   **Protocols and data types:**
   - `ToolExecutor` Protocol — structural, `@runtime_checkable`. The executor owns
     JSON parsing of `tool_input`; the runtime passes the raw string through.
   - `ToolCallInfo` — frozen dataclass recording one tool call: id, name, input, output,
     error flag. One instance per tool call, collected into `TurnSummary.tool_calls`.
   - `TurnSummary` — frozen dataclass returned by `run_turn()`. Captures everything the
     caller needs: iterations, token usage (accumulated across all iterations), tool calls
     made, whether the iteration limit was hit, and final turn status.

   **Test doubles:**
   - `NoOpExecutor` — returns `""` for any tool. Used when tool behavior is irrelevant.
   - `EchoExecutor` — returns `"{tool_name}: {tool_input}"`. Verifies input plumbing.
   - `StaticExecutor` — maps tool names to pre-configured responses. Raises `KeyError`
     for unknown tools. Doctest included.

   **`format_session_for_api`:**
   Walks `session.events` directly rather than calling `derive_messages()`. This is
   the right approach because `derive_messages()` returns only `MessageAdded`-derived
   messages and excludes `ToolCallRecorded`/`ToolResultRecorded`. Walking events lets
   the function build the correct Anthropic interleaving without cross-referencing
   `SessionView`. Two helpers: `_append_message` (plain-text messages) and
   `_append_content_blocks` (structured blocks) both handle same-role merging.

   **`ConversationRuntime`:**
   - Constructor takes `api_client`, `tool_executor`, `session`, `system_prompt`,
     `tool_definitions`, `on_text` callback, plus model/token/temperature/iteration params.
     Nothing is hardcoded — all dependencies are injected.
   - `self._session` is a mutable reference to the latest immutable `Session` snapshot.
     Every session mutation rebinds it. Caller reads the final state via `runtime.session`.
   - `run_turn()` owns the full turn lifecycle: `start_turn` at the top, `end_turn` at
     the bottom. Caller always gets back a session with a closed turn.
   - Inner loop: format messages → build request → stream → accumulate → record →
     execute tools → repeat. `max_iterations` default is 10 (safety valve for runaway loops).
   - `add_assistant_text` is called once per iteration even when text is empty string `""`.
     This keeps the event stream predictable: every assistant API response produces exactly
     one `MessageAdded` event, followed by zero or more `ToolCallRecorded` events.

5. **Wrote `tests/test_conversation.py`.**
   17 tests, all using injected test doubles — no real API calls, no disk I/O.
   Tests cover: single-turn no-tools, single tool call (2 iterations), multiple tools in
   one iteration, chained tool calls (3 iterations), iteration limit triggering FAILED
   status, `on_text` callback firing per delta, executor errors becoming tool results,
   usage accumulation across iterations, session event sequence verification,
   `ScriptedClient` request recording, `ScriptedClient` exhaustion error,
   `format_session_for_api` basic messages, tool call + result formatting,
   same-role merging, text-only summary, mixed text + tool in one response, and
   tool results appearing in the second API call's messages.

6. **Verified all tests pass.**
   ```
   pytest tests/test_conversation.py -v   # 17/17
   pytest -q                              # 151/151 (1 skipped)
   ```

## Key design decisions

| Decision | Rationale |
|----------|-----------|
| `format_session_for_api` walks `session.events` directly | `derive_messages()` excludes `ToolCallRecorded`/`ToolResultRecorded`. Walking events gives the full picture needed to build Anthropic's interleaved format without querying `SessionView`. |
| Empty-text assistant messages always recorded | Every assistant API response calls `add_assistant_text()`, even with `""`. Keeps the event stream predictable — every assistant turn starts with a `MessageAdded` event, followed by tool calls. Simplifies `format_session_for_api`. |
| `run_turn()` owns full turn lifecycle | `start_turn()` at the top, `end_turn()` at the bottom. The caller always gets back a session with a closed turn. No half-open turns can escape. |
| Executor exceptions → error tool results | `try/except Exception` around each `executor.execute()`. The model sees the error text as a tool result and can react. The runtime never crashes due to a tool executor failure. |
| `ScriptedClient` lives in `api.py` | It is a test double for the `ApiClient` protocol, same as `MockClient`. Belongs alongside the protocol it satisfies, not in the test file or conversation module. |
| `on_text` callback injected, not hardcoded | Production passes `print` or a TUI render function. Tests pass a capturing lambda. No `sys.stdout` coupling in the runtime. |
| Mutable reference to immutable session | `self._session` is rebound on every mutation. The `Session` object itself is never mutated — frozen dataclass guarantees this. Rust `&mut self` pattern: the runtime is mutable, the session snapshots are not. |
| `LiteLLMClient` as the production API client | Multi-provider support (Anthropic, OpenAI, etc.) via a single dependency. `AnthropicClient` is retained in `api.py` as a stub marked deprecated. The `LiteLLMClient` is being developed on a separate branch by another engineer — the conversation runtime is decoupled from this choice via the `ApiClient` protocol. |
| `max_iterations=10` default | Safety valve for runaway tool loops. Exceeding the limit closes the turn with `TurnStatus.FAILED` and sets `TurnSummary.hit_iteration_limit = True`. The caller decides what to do. |
| No permission system, no auto-compaction | Game tool calls are known at construction time (`tool_definitions`). Permissions are a future concern. Sessions are 10–25 turns — context limits won't be hit. Both are deliberately out of scope for v1.4. |

## What this layer does not own

- **Tool implementations** — `update_mood`, `update_security`, `generate_response_options`
  are game mechanics that live in the next layer. `ToolExecutor` is the seam.
- **System prompt construction** — encoding scene, role, game state into the prompt
  is a CLI/game layer concern.
- **Win/loss detection** — checking mood threshold and arc status happens above this layer.
- **LiteLLMClient streaming** — the concrete production client is owned by another engineer.
  The conversation runtime is fully testable without it via `MockClient`/`ScriptedClient`.
