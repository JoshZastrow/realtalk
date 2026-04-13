"""
realtalk.hooks — Layer 5: pre/post tool hook runner.

Hooks are shell commands configured in HookConfig. Exit code protocol:
  0 = allow, 1 = ask, 2 = deny (pre-hooks only).
Post-hooks are fire-and-forget (subprocess.Popen, non-blocking).

Hook timeout: 10 seconds. Timeout -> allow. A slow hook never blocks the game.

The ContributorCapture class provides a built-in Python hook for writing
per-turn JSONL when --contributor mode is active.

Dependencies: config.py (HookConfig, ContributorConfig).
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
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from realtalk.config import ContributorConfig, HookConfig


class HookDecision(Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass(frozen=True)
class HookResult:
    """Result of a pre-hook evaluation."""

    decision: HookDecision
    reason: str

    @staticmethod
    def allowed() -> HookResult:
        return HookResult(decision=HookDecision.ALLOW, reason="")

    @staticmethod
    def denied(reason: str) -> HookResult:
        return HookResult(decision=HookDecision.DENY, reason=reason)


_EXIT_TO_DECISION = {
    0: HookDecision.ALLOW,
    1: HookDecision.ASK,
    2: HookDecision.DENY,
}

HOOK_TIMEOUT_SECONDS = 10


class HookRunner:
    """Fire configured shell commands at tool lifecycle events.

    Pre-hooks run synchronously (blocking). Their exit code determines
    whether the tool call proceeds.

    Post-hooks run via Popen (non-blocking). The game does not wait.

    Failure hooks fire when a tool execution raises an exception.
    """

    def __init__(self, config: HookConfig) -> None:
        self._config = config

    def pre(self, tool_name: str, input_json: str) -> HookResult:
        """Run all PreToolUse hooks. First DENY wins. First ASK wins if no DENY."""
        if not self._config.pre_tool_use:
            return HookResult.allowed()

        ask_result: HookResult | None = None

        for cmd in self._config.pre_tool_use:
            decision, reason = self._run_sync(cmd, tool_name, input_json)
            if decision == HookDecision.DENY:
                return HookResult(decision=HookDecision.DENY, reason=reason)
            if decision == HookDecision.ASK and ask_result is None:
                ask_result = HookResult(decision=HookDecision.ASK, reason=reason)

        return ask_result or HookResult.allowed()

    def post(self, tool_name: str, input_json: str, output: str) -> None:
        """Fire all PostToolUse hooks (non-blocking)."""
        env = self._hook_env(tool_name, input_json, output=output)
        for cmd in self._config.post_tool_use:
            try:
                subprocess.Popen(  # noqa: S603
                    ["sh", "-c", cmd],
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError:
                pass  # fire-and-forget: swallow launch failures

    def failure(self, tool_name: str, input_json: str, error: str) -> None:
        """Fire all PostToolUseFailure hooks (non-blocking)."""
        env = self._hook_env(tool_name, input_json, error=error)
        for cmd in self._config.post_tool_use_failure:
            try:
                subprocess.Popen(  # noqa: S603
                    ["sh", "-c", cmd],
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError:
                pass

    def _run_sync(
        self, cmd: str, tool_name: str, input_json: str
    ) -> tuple[HookDecision, str]:
        """Run a single hook command synchronously. Returns (decision, reason)."""
        env = self._hook_env(tool_name, input_json)
        try:
            result = subprocess.run(  # noqa: S603
                ["sh", "-c", cmd],
                env=env,
                capture_output=True,
                text=True,
                timeout=HOOK_TIMEOUT_SECONDS,
            )
            decision = _EXIT_TO_DECISION.get(result.returncode, HookDecision.ALLOW)
            reason = result.stdout.strip()
            return decision, reason
        except subprocess.TimeoutExpired:
            return HookDecision.ALLOW, "hook timed out"
        except OSError:
            return HookDecision.ALLOW, "hook failed to run"

    @staticmethod
    def _hook_env(
        tool_name: str,
        input_json: str,
        output: str = "",
        error: str = "",
    ) -> dict[str, str]:
        """Build the environment dict passed to hook subprocesses."""
        env = dict(os.environ)
        env["REALTALK_TOOL_NAME"] = tool_name
        env["REALTALK_TOOL_INPUT"] = input_json
        env["REALTALK_TOOL_OUTPUT"] = output
        env["REALTALK_TOOL_ERROR"] = error
        return env


class ContributorCapture:
    """Built-in hook for writing per-turn data to JSONL in contributor mode.

    Called by ToolRegistry after every tool execution (same position as post-hook).
    Writes one JSON line per tool call. The JSONL file is at:
        {contributor.resolved_session_dir}/{session_id}.jsonl

    Privacy guarantee: player reaction ratings are NEVER included in the
    active LLM prompt. ContributorCapture only writes to local storage.
    """

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
        with open(path, "a") as f:
            f.write(json.dumps(record) + "\n")
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
