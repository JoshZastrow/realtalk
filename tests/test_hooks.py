"""Tests for realtalk.hooks — Layer 5: hook runner.

Uses real subprocesses (sh -c '...'). The only seam we mock is the
LLM API client; hooks are tested against real shell execution.
"""

from __future__ import annotations

import pytest

from realtalk.config import HookConfig
from realtalk.hooks import HookContext, HookEvent, HookResult, HookRunner

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(
    tool_name: str = "update_mood",
    tool_input: str = '{"delta": 5}',
    **kwargs: object,
) -> HookContext:
    return HookContext(tool_name=tool_name, tool_input=tool_input, **kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# HookEvent
# ---------------------------------------------------------------------------


class TestHookEvent:
    def test_values(self) -> None:
        assert HookEvent.PRE_TOOL_USE == "PreToolUse"
        assert HookEvent.POST_TOOL_USE == "PostToolUse"
        assert HookEvent.POST_TOOL_USE_FAILURE == "PostToolUseFailure"


# ---------------------------------------------------------------------------
# HookContext
# ---------------------------------------------------------------------------


class TestHookContext:
    def test_defaults(self) -> None:
        ctx = HookContext(tool_name="t", tool_input="{}")
        assert ctx.tool_output == ""
        assert ctx.tool_is_error is False

    def test_frozen(self) -> None:
        ctx = _ctx()
        with pytest.raises(AttributeError):
            ctx.tool_name = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# HookResult
# ---------------------------------------------------------------------------


class TestHookResult:
    def test_defaults(self) -> None:
        r = HookResult()
        assert not r.denied
        assert not r.failed
        assert r.reason == ""
        assert r.messages == ()

    def test_frozen(self) -> None:
        r = HookResult()
        with pytest.raises(AttributeError):
            r.denied = True  # type: ignore[misc]


# ---------------------------------------------------------------------------
# HookRunner — empty hooks (no-op fast path)
# ---------------------------------------------------------------------------


class TestHookRunnerEmpty:
    def test_empty_pre_hooks_return_default(self) -> None:
        runner = HookRunner(HookConfig())
        result = runner.run_pre_tool_use(_ctx())
        assert result == HookResult()

    def test_empty_post_hooks_return_default(self) -> None:
        runner = HookRunner(HookConfig())
        result = runner.run_post_tool_use(_ctx())
        assert result == HookResult()

    def test_empty_post_failure_hooks_return_default(self) -> None:
        runner = HookRunner(HookConfig())
        result = runner.run_post_tool_use_failure(_ctx())
        assert result == HookResult()


# ---------------------------------------------------------------------------
# HookRunner — exit code protocol
# ---------------------------------------------------------------------------


class TestExitCodeProtocol:
    def test_exit_0_allows(self) -> None:
        runner = HookRunner(HookConfig(pre_tool_use=["printf 'hook ok'"]))
        result = runner.run_pre_tool_use(_ctx())
        assert not result.denied
        assert not result.failed
        assert "hook ok" in result.messages

    def test_exit_2_denies(self) -> None:
        runner = HookRunner(HookConfig(pre_tool_use=["printf 'blocked'; exit 2"]))
        result = runner.run_pre_tool_use(_ctx())
        assert result.denied
        assert result.reason == "blocked"

    def test_exit_1_fails(self) -> None:
        runner = HookRunner(HookConfig(pre_tool_use=["printf 'oops'; exit 1"]))
        result = runner.run_pre_tool_use(_ctx())
        assert result.failed
        assert not result.denied

    def test_exit_0_no_stdout_produces_no_messages(self) -> None:
        runner = HookRunner(HookConfig(pre_tool_use=["true"]))
        result = runner.run_pre_tool_use(_ctx())
        assert result.messages == ()

    def test_exit_2_no_stdout_produces_fallback_reason(self) -> None:
        runner = HookRunner(HookConfig(pre_tool_use=["exit 2"]))
        result = runner.run_pre_tool_use(_ctx())
        assert result.denied
        assert "denied" in result.reason.lower()


# ---------------------------------------------------------------------------
# HookRunner — sequential execution
# ---------------------------------------------------------------------------


class TestSequentialExecution:
    def test_all_allow_collects_messages(self) -> None:
        runner = HookRunner(
            HookConfig(pre_tool_use=["printf 'first'", "printf 'second'"])
        )
        result = runner.run_pre_tool_use(_ctx())
        assert result.messages == ("first", "second")
        assert not result.denied
        assert not result.failed

    def test_stop_on_deny(self) -> None:
        """First allows, second denies, third never runs."""
        runner = HookRunner(
            HookConfig(
                pre_tool_use=[
                    "printf 'first ok'",
                    "printf 'denied'; exit 2",
                    "printf 'never runs'",
                ]
            )
        )
        result = runner.run_pre_tool_use(_ctx())
        assert result.denied
        assert "first ok" in result.messages
        assert not any("never runs" in m for m in result.messages)

    def test_stop_on_failure(self) -> None:
        runner = HookRunner(
            HookConfig(
                pre_tool_use=[
                    "printf 'broken'; exit 1",
                    "printf 'never runs'",
                ]
            )
        )
        result = runner.run_pre_tool_use(_ctx())
        assert result.failed
        assert not any("never runs" in m for m in result.messages)


# ---------------------------------------------------------------------------
# HookRunner — environment variables
# ---------------------------------------------------------------------------


class TestEnvironmentVariables:
    def test_hook_tool_name(self) -> None:
        runner = HookRunner(HookConfig(pre_tool_use=["echo $HOOK_TOOL_NAME"]))
        result = runner.run_pre_tool_use(_ctx(tool_name="update_mood"))
        assert "update_mood" in result.messages

    def test_hook_event(self) -> None:
        runner = HookRunner(HookConfig(pre_tool_use=["echo $HOOK_EVENT"]))
        result = runner.run_pre_tool_use(_ctx())
        assert "PreToolUse" in result.messages

    def test_hook_tool_input(self) -> None:
        runner = HookRunner(HookConfig(pre_tool_use=["echo $HOOK_TOOL_INPUT"]))
        result = runner.run_pre_tool_use(_ctx(tool_input='{"delta": 5}'))
        assert '{"delta": 5}' in result.messages

    def test_post_hook_tool_output(self) -> None:
        runner = HookRunner(HookConfig(post_tool_use=["echo $HOOK_TOOL_OUTPUT"]))
        ctx = _ctx(tool_output='{"mood": 65}')
        result = runner.run_post_tool_use(ctx)
        assert '{"mood": 65}' in result.messages

    def test_post_failure_hook_is_error_flag(self) -> None:
        runner = HookRunner(
            HookConfig(post_tool_use_failure=["echo $HOOK_TOOL_IS_ERROR"])
        )
        ctx = _ctx(tool_output="ERROR: ValueError", tool_is_error=True)
        result = runner.run_post_tool_use_failure(ctx)
        assert "1" in result.messages


# ---------------------------------------------------------------------------
# HookRunner — stdin JSON payload
# ---------------------------------------------------------------------------


class TestStdinPayload:
    def test_payload_contains_tool_name(self) -> None:
        script = "python3 -c 'import sys,json; d=json.load(sys.stdin); print(d[\"tool_name\"])'"
        runner = HookRunner(HookConfig(pre_tool_use=[script]))
        result = runner.run_pre_tool_use(_ctx(tool_name="update_mood"))
        assert "update_mood" in result.messages

    def test_payload_contains_parsed_tool_input(self) -> None:
        script = (
            "python3 -c '"
            "import sys,json; d=json.load(sys.stdin); "
            "print(d[\"tool_input\"][\"delta\"])"
            "'"
        )
        runner = HookRunner(HookConfig(pre_tool_use=[script]))
        result = runner.run_pre_tool_use(_ctx(tool_input='{"delta": 5}'))
        assert "5" in result.messages

    def test_payload_contains_raw_tool_input_json(self) -> None:
        script = (
            "python3 -c '"
            "import sys,json; d=json.load(sys.stdin); "
            "print(d[\"tool_input_json\"])"
            "'"
        )
        runner = HookRunner(HookConfig(pre_tool_use=[script]))
        result = runner.run_pre_tool_use(_ctx(tool_input='{"delta": 5}'))
        assert '{"delta": 5}' in result.messages

    def test_payload_post_failure_has_tool_error(self) -> None:
        script = (
            "python3 -c '"
            "import sys,json; d=json.load(sys.stdin); "
            "print(d[\"tool_error\"])"
            "'"
        )
        runner = HookRunner(
            HookConfig(post_tool_use_failure=[script])
        )
        ctx = _ctx(tool_output="ERROR: boom", tool_is_error=True)
        result = runner.run_post_tool_use_failure(ctx)
        assert "ERROR: boom" in result.messages

    def test_malformed_input_falls_back_to_raw(self) -> None:
        script = (
            "python3 -c '"
            "import sys,json; d=json.load(sys.stdin); "
            "print(d[\"tool_input\"][\"raw\"])"
            "'"
        )
        runner = HookRunner(HookConfig(pre_tool_use=[script]))
        result = runner.run_pre_tool_use(_ctx(tool_input="not json"))
        assert "not json" in result.messages


# ---------------------------------------------------------------------------
# HookRunner — timeout
# ---------------------------------------------------------------------------


class TestTimeout:
    def test_timeout_treated_as_failure(self) -> None:
        runner = HookRunner(HookConfig(pre_tool_use=["sleep 5"]), timeout=0.5)
        result = runner.run_pre_tool_use(_ctx())
        assert result.failed
        assert not result.denied
        assert "timed out" in result.reason.lower()
