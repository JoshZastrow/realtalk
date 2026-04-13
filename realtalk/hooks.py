"""
realtalk.hooks — Layer 5: pre/post tool hook runner.

Runs configured shell commands at PreToolUse, PostToolUse, and
PostToolUseFailure events. Commands execute via ``sh -lc`` with hook
context passed through environment variables and a JSON stdin payload.

The v1.5 API is:
  - HookEvent
  - HookContext
  - HookResult
  - HookRunner.run_pre_tool_use()
  - HookRunner.run_post_tool_use()
  - HookRunner.run_post_tool_use_failure()

Compatibility helpers are also kept for the older tool layer:
  - HookDecision
  - HookRunner.pre()
  - HookRunner.post()
  - HookRunner.failure()
  - ContributorCapture

Dependencies: config.py (HookConfig, ContributorConfig).
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from enum import Enum, StrEnum
from pathlib import Path

from realtalk.config import ContributorConfig, HookConfig


class HookEvent(StrEnum):
    """Points in the tool execution lifecycle where hooks fire."""

    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    POST_TOOL_USE_FAILURE = "PostToolUseFailure"


class HookDecision(Enum):
    """Legacy decision enum used by the older tool-layer tests."""

    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass(frozen=True)
class HookContext:
    """Context passed to hook commands via env vars and stdin JSON."""

    tool_name: str
    tool_input: str
    tool_output: str = ""
    tool_is_error: bool = False


@dataclass(frozen=True)
class HookResult:
    """Result of running all hooks for a single event."""

    denied: bool = False
    failed: bool = False
    reason: str = ""
    messages: tuple[str, ...] = ()

    @property
    def decision(self) -> HookDecision:
        """Compatibility view for the older pre/post hook interface."""
        if self.denied:
            return HookDecision.DENY
        return HookDecision.ALLOW

    @staticmethod
    def allowed() -> HookResult:
        return HookResult()

    @staticmethod
    def denied_result(reason: str) -> HookResult:
        return HookResult(denied=True, reason=reason, messages=(reason,))


class HookRunner:
    """Runs configured shell commands for hook events."""

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

    def pre(self, tool_name: str, input_json: str) -> HookResult:
        """Compatibility wrapper for older call sites."""
        return self.run_pre_tool_use(HookContext(tool_name=tool_name, tool_input=input_json))

    def post(self, tool_name: str, input_json: str, output: str) -> HookResult:
        """Compatibility wrapper for older call sites."""
        return self.run_post_tool_use(
            HookContext(
                tool_name=tool_name,
                tool_input=input_json,
                tool_output=output,
                tool_is_error=False,
            )
        )

    def failure(self, tool_name: str, input_json: str, error: str) -> HookResult:
        """Compatibility wrapper for older call sites."""
        return self.run_post_tool_use_failure(
            HookContext(
                tool_name=tool_name,
                tool_input=input_json,
                tool_output=error,
                tool_is_error=True,
            )
        )

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
        messages = (stdout,) if stdout else ()
        return HookResult(messages=messages)

    if proc.returncode == 2:
        reason = stdout or f"Hook `{command}` denied the tool call"
        return HookResult(denied=True, reason=reason, messages=(reason,))

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
    # Legacy aliases kept for the older tool-layer tests and callers.
    env["REALTALK_TOOL_NAME"] = ctx.tool_name
    env["REALTALK_TOOL_INPUT"] = ctx.tool_input
    env["REALTALK_TOOL_ERROR"] = ctx.tool_output if ctx.tool_is_error else ""
    if ctx.tool_output:
        env["HOOK_TOOL_OUTPUT"] = ctx.tool_output
        env["REALTALK_TOOL_OUTPUT"] = ctx.tool_output
    else:
        env["REALTALK_TOOL_OUTPUT"] = ""
    return env


def _parse_tool_input(raw: str) -> object:
    """Parse tool input JSON for the payload. Falls back to ``{\"raw\": raw}``."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {"raw": raw}


class ContributorCapture:
    """Built-in hook for writing per-turn data to JSONL in contributor mode."""

    def __init__(self, config: ContributorConfig, session_id: str) -> None:
        self._enabled = config.enabled
        self._dir = config.resolved_session_dir
        self._session_id = session_id
        self._path: Path | None = None

    def _ensure_dir(self) -> Path:
        if self._path is None:
            self._dir.mkdir(parents=True, exist_ok=True)
            self._path = self._dir / f"{self._session_id}.jsonl"
        return self._path

    def capture(
        self,
        tool_name: str,
        input_json: str,
        output: str,
        is_error: bool,
        turn_number: int,
    ) -> None:
        """Append one JSONL line. No-op if contributor mode is disabled."""
        if not self._enabled:
            return

        path = self._ensure_dir()
        record = {
            "timestamp": time.time(),
            "turn": turn_number,
            "tool": tool_name,
            "input": json.loads(input_json) if input_json else {},
            "output": output,
            "is_error": is_error,
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
