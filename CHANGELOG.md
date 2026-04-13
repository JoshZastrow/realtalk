# Changelog

All notable changes to this project will be documented in this file.

Format: [Semantic Versioning](https://semver.org/)

## [0.0.1.0] - 2026-04-12

### Added

- **Conversation runtime (Layer 4)** — `realtalk/conversation.py` implements the full
  turn loop: user input → LLM response → tool execution → session recording → repeat.
  `ConversationRuntime` drives the loop; `TurnSummary` captures what happened each turn
  (iterations, token usage, tool calls, status).
- **`ToolExecutor` protocol** — structural interface for game mechanics. The game layer
  (Layer 5) satisfies it by implementing `execute(tool_name, tool_input) -> str`. The
  conversation runtime never hard-codes tool behavior.
- **`format_session_for_api`** — converts the immutable event-sourced `Session` into
  Anthropic-format messages, correctly interleaving `tool_use` and `tool_result` blocks.
- **Test doubles** — `NoOpExecutor`, `EchoExecutor`, `StaticExecutor` for testing without
  a real game layer. `ScriptedClient` in `api.py` serves scripted event sequences across
  multiple stream calls for multi-turn tests.
- **17 tests** in `tests/test_conversation.py` covering: no-tool turns, single and multiple
  tool calls, chained tool loops, iteration limits, token accumulation, session event
  sequences, and `format_session_for_api` edge cases.

### Changed

- `ApiRequest` model default changed from `claude-opus-4-6` to `claude-haiku-4-5-20251001`
  (appropriate default for game interactions).
- `ApiRequest` gains `temperature: float = 1.0` field, forwarded from `ConversationRuntime`.
- Exception messages from tool executors are now sanitized before being sent to the LLM:
  only the exception type is exposed (not the raw message, which may contain file paths
  or internal state).

### Fixed

- `run_turn()` now wraps its inner loop in `try/except/re-raise` so `end_turn()` is always
  called even if the API stream or `on_text` callback raises. Sessions can no longer be
  left with an open turn on unexpected failures.
- Removed dead `while...else` branch in `run_turn()` (the `else` clause was unreachable
  because the explicit `break` always fired first before the `while` condition could
  become `False`).
