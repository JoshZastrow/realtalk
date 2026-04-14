from __future__ import annotations

import os

from realtalk.cli import _ensure_api_key
from realtalk.config import ContributorConfig, DisplayConfig, GameConfig, HookConfig, RuntimeConfig


def _config() -> RuntimeConfig:
    return RuntimeConfig(
        game=GameConfig(
            model="test-model",
            temperature=0.0,
            max_tokens=256,
            min_turns_to_win=8,
            turn_hard_cap=25,
            arc_trigger_threshold=80,
            mood_start_min=30,
            mood_start_max=50,
            security_start_min=40,
            security_start_max=60,
            reaction_delta_low=3,
            reaction_delta_medium=7,
            reaction_delta_high=12,
        ),
        contributor=ContributorConfig(enabled=False, session_dir="~/.realtalk/sessions"),
        display=DisplayConfig(no_color=False, debug=False),
        hooks=HookConfig(pre_tool_use=[], post_tool_use=[], post_tool_use_failure=[]),
    )


def test_ensure_api_key_returns_true_when_present(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert _ensure_api_key(_config()) is True


def test_ensure_api_key_prompts_when_missing(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("getpass.getpass", lambda prompt: "sk-ant-entered")

    assert _ensure_api_key(_config()) is True
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-entered"


def test_ensure_api_key_fails_without_tty(monkeypatch, capsys) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    assert _ensure_api_key(_config()) is False
    captured = capsys.readouterr()
    assert "ANTHROPIC_API_KEY is not set" in captured.out
