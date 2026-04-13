"""
realtalk.hooks — Layer 5: pre/post tool hook runner.

Hooks are shell commands configured in HookConfig. Exit code protocol:
  0 = allow, 1 = ask, 2 = deny (pre-hooks only).
Post-hooks are fire-and-forget (subprocess.Popen, non-blocking).

Hook timeout: 10 seconds. Timeout -> allow. A slow hook never blocks the game.

The ContributorCapture class provides a built-in Python hook for writing
per-turn JSONL when --contributor mode is active.

Dependencies: config.py (HookConfig, ContributorConfig).
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
