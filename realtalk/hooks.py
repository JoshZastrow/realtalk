"""
realtalk.hooks -- Layer 5: pre/post tool hook runner.

Fires configured shell commands at PreToolUse, PostToolUse, and
PostToolUseFailure events. Commands run via ``sh -lc`` with context
passed through environment variables and a JSON stdin payload.

Exit code protocol:
    0 = allow (continue)
    2 = deny (block the tool call; stdout is the reason)
    other = failure (hook broke; chain stops)

Adapted from the Rust HookRunner in claw-code. See docs/spec/v1.5.md
for the full design rationale and reference walkthrough.

Dependencies: config.py (HookConfig).
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from enum import StrEnum

from realtalk.config import HookConfig


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class HookEvent(StrEnum):
    """Points in the tool execution lifecycle where hooks fire."""

    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    POST_TOOL_USE_FAILURE = "PostToolUseFailure"


@dataclass(frozen=True)
class HookContext:
    """Context passed to hook commands via env vars and stdin JSON.

    Always populated: tool_name, tool_input.
    Populated for post-hooks only: tool_output, tool_is_error.
    """

    tool_name: str
    tool_input: str  # raw JSON string from the LLM
    tool_output: str = ""  # tool result (post-hooks only)
    tool_is_error: bool = False


@dataclass(frozen=True)
class HookResult:
    """Result of running all hooks for a single event.

    Only PreToolUse hooks can deny. Post-hooks populate messages only.
    A failed result means the hook itself broke (non-0, non-2 exit or timeout).
    """

    denied: bool = False
    failed: bool = False
    reason: str = ""  # denial/failure reason (from stdout)
    messages: tuple[str, ...] = ()  # system messages from all hooks that ran


# ---------------------------------------------------------------------------
# HookRunner
# ---------------------------------------------------------------------------


class HookRunner:
    """Runs configured shell commands for hook events.

    Commands execute via ``sh -lc <command>`` with environment variables
    and a JSON payload on stdin. Hooks run sequentially in config order;
    execution stops on the first deny or failure.

    Timeout default: 30 seconds. A slow hook never blocks the game.
    """

    def __init__(self, config: HookConfig, timeout: float = 30.0) -> None:
        self._config = config
        self._timeout = timeout

    def run_pre_tool_use(self, context: HookContext) -> HookResult:
        """Fire PreToolUse hooks. Can deny the tool call."""
        return self._run_commands(
            HookEvent.PRE_TOOL_USE,
            self._config.pre_tool_use,
            context,
        )

    def run_post_tool_use(self, context: HookContext) -> HookResult:
        """Fire PostToolUse hooks. Observational."""
        return self._run_commands(
            HookEvent.POST_TOOL_USE,
            self._config.post_tool_use,
            context,
        )

    def run_post_tool_use_failure(self, context: HookContext) -> HookResult:
        """Fire PostToolUseFailure hooks. Observational."""
        return self._run_commands(
            HookEvent.POST_TOOL_USE_FAILURE,
            self._config.post_tool_use_failure,
            context,
        )

    # -- internals ----------------------------------------------------------

    def _run_commands(
        self,
        event: HookEvent,
        commands: list[str],
        context: HookContext,
    ) -> HookResult:
        """Execute commands sequentially. Stop on first deny or failure."""
        if not commands:
            return HookResult()

        messages: list[str] = []
        payload = _build_payload(event, context)
        env = _build_env(event, context)

        for command in commands:
            result = _run_one(command, payload, env, self._timeout)
            if result.messages:
                messages.extend(result.messages)
            if result.denied:
                return HookResult(
                    denied=True,
                    reason=result.reason,
                    messages=tuple(messages),
                )
            if result.failed:
                return HookResult(
                    failed=True,
                    reason=result.reason,
                    messages=tuple(messages),
                )

        return HookResult(messages=tuple(messages))


# ---------------------------------------------------------------------------
# Pure helpers (module-level for testability)
# ---------------------------------------------------------------------------


def _run_one(
    command: str,
    payload: str,
    env: dict[str, str],
    timeout: float,
) -> HookResult:
    """Run a single shell command and interpret the exit code."""
    try:
        proc = subprocess.run(
            ["sh", "-lc", command],
            input=payload,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return HookResult(
            failed=True,
            reason=f"Hook `{command}` timed out after {timeout}s",
            messages=(f"Hook `{command}` timed out",),
        )
    except OSError as exc:
        return HookResult(
            failed=True,
            reason=f"Hook `{command}` failed to start: {exc}",
            messages=(f"Hook `{command}` failed to start: {exc}",),
        )

    stdout = proc.stdout.strip()

    if proc.returncode == 0:
        msgs = (stdout,) if stdout else ()
        return HookResult(messages=msgs)

    if proc.returncode == 2:
        reason = stdout or f"Hook `{command}` denied the tool call"
        return HookResult(denied=True, reason=reason, messages=(reason,))

    # Any other exit code = failure
    reason = (
        stdout
        or proc.stderr.strip()
        or f"Hook `{command}` exited with status {proc.returncode}"
    )
    return HookResult(failed=True, reason=reason, messages=(reason,))


def _build_payload(event: HookEvent, ctx: HookContext) -> str:
    """Build the JSON payload piped to the hook's stdin."""
    payload: dict[str, object] = {
        "hook_event_name": event.value,
        "tool_name": ctx.tool_name,
        "tool_input": _parse_tool_input(ctx.tool_input),
        "tool_input_json": ctx.tool_input,
    }
    if event == HookEvent.POST_TOOL_USE_FAILURE:
        payload["tool_error"] = ctx.tool_output
        payload["tool_result_is_error"] = True
    else:
        payload["tool_output"] = ctx.tool_output or None
        payload["tool_result_is_error"] = ctx.tool_is_error
    return json.dumps(payload)


def _build_env(event: HookEvent, ctx: HookContext) -> dict[str, str]:
    """Build environment variables for the hook subprocess."""
    env = os.environ.copy()
    env["HOOK_EVENT"] = event.value
    env["HOOK_TOOL_NAME"] = ctx.tool_name
    env["HOOK_TOOL_INPUT"] = ctx.tool_input
    env["HOOK_TOOL_IS_ERROR"] = "1" if ctx.tool_is_error else "0"
    if ctx.tool_output:
        env["HOOK_TOOL_OUTPUT"] = ctx.tool_output
    return env


def _parse_tool_input(raw: str) -> object:
    """Parse tool input JSON for the payload. Falls back to ``{"raw": raw}``."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {"raw": raw}
