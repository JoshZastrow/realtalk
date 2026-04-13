"""Integration tests for LiteLLMClient with multiple providers.

These tests verify that LiteLLMClient works with real LLM providers.
They require environment variables to be set and are only run when
PYTEST_INTEGRATION=1 is set.

Environment variables:
- ANTHROPIC_API_KEY: required for Anthropic tests
- OPENAI_API_KEY: optional for OpenAI tests
- PYTEST_INTEGRATION: set to '1' to enable integration tests (opt-in)
"""

from __future__ import annotations

import os

import pytest

from realtalk.api import ApiRequest, LiteLLMClient, MessageStop, TextDelta

# Skip all tests in this file unless PYTEST_INTEGRATION=1
pytestmark = pytest.mark.skipif(
    os.getenv("PYTEST_INTEGRATION") != "1",
    reason="Integration tests disabled. Run with PYTEST_INTEGRATION=1 to enable.",
)


def test_anthropic_real_call():
    """Live test: call real Anthropic API with small prompt.

    REQUIRES: ANTHROPIC_API_KEY env var set.
    COST: ~$0.001
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        pytest.skip("ANTHROPIC_API_KEY not set")

    client = LiteLLMClient(
        model="claude-3-5-sonnet-20241022",
        temperature=0.5,
        max_tokens=100,
        api_key=api_key,
    )

    request = ApiRequest(
        system_prompt=["You are a helpful assistant."],
        messages=[{"role": "user", "content": "Say 'hello world' and stop."}],
        tools=[],
        model="claude-3-5-sonnet-20241022",
    )

    # Collect events
    events = list(client.stream(request))

    # Verify we got events
    assert len(events) > 0, "Expected at least one event"

    # Verify we got text and a stop event
    has_text = any(isinstance(e, TextDelta) for e in events)
    has_stop = any(isinstance(e, MessageStop) for e in events)
    assert has_text, "Expected at least one TextDelta event"
    assert has_stop, "Expected at least one MessageStop event"

    # Verify response contains expected words
    full_text = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert "hello" in full_text.lower(), f"Expected 'hello' in response: {full_text}"


def test_openai_real_call():
    """Live test: call real OpenAI API with small prompt.

    REQUIRES: OPENAI_API_KEY env var set.
    COST: ~$0.0001
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        pytest.skip("OPENAI_API_KEY not set")

    client = LiteLLMClient(
        model="gpt-4-turbo",
        temperature=0.5,
        max_tokens=100,
        api_key=api_key,
    )

    request = ApiRequest(
        system_prompt=["You are a helpful assistant."],
        messages=[{"role": "user", "content": "Say 'hello world' and stop."}],
        tools=[],
        model="gpt-4-turbo",
    )

    # Collect events
    events = list(client.stream(request))

    # Verify we got events
    assert len(events) > 0, "Expected at least one event"

    # Verify we got text and a stop event
    has_text = any(isinstance(e, TextDelta) for e in events)
    has_stop = any(isinstance(e, MessageStop) for e in events)
    assert has_text, "Expected at least one TextDelta event"
    assert has_stop, "Expected at least one MessageStop event"

    # Verify response contains expected words
    full_text = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert "hello" in full_text.lower(), f"Expected 'hello' in response: {full_text}"


def test_streaming_accumulation():
    """Full streaming response is correctly accumulated into events.

    Uses MockClient to simulate provider differences without hitting APIs.
    """
    from realtalk.api import MockClient, UsageEvent

    # Simulate a complete streaming response (Anthropic-style)
    events = [
        TextDelta("Hello "),
        TextDelta("world"),
        UsageEvent(input_tokens=10, output_tokens=15),
        MessageStop(stop_reason="end_turn"),
    ]

    client = MockClient(events)
    request = ApiRequest(
        system_prompt=["You are helpful."],
        messages=[{"role": "user", "content": "Say hello"}],
        tools=[],
        model="mock",
    )

    # Collect streamed events
    result = list(client.stream(request))

    # Verify sequence
    assert len(result) == 4
    assert isinstance(result[0], TextDelta)
    assert result[0].text == "Hello "
    assert isinstance(result[1], TextDelta)
    assert result[1].text == "world"
    assert isinstance(result[2], UsageEvent)
    assert result[2].input_tokens == 10
    assert result[2].output_tokens == 15
    assert isinstance(result[3], MessageStop)

    # Verify accumulated text
    full_text = "".join(e.text for e in result if isinstance(e, TextDelta))
    assert full_text == "Hello world"


def test_config_integration_temperature():
    """Verify temperature config is passed to LiteLLMClient."""
    import tempfile
    from pathlib import Path

    from realtalk.config import ConfigLoader

    with tempfile.TemporaryDirectory() as tmpdir:
        config_file = Path(tmpdir) / ".realtalk.json"
        config_file.write_text('{"game": {"temperature": 0.3}}')

        loader = ConfigLoader(cwd=Path(tmpdir))
        cfg = loader.load()

        # Verify config has temperature
        assert cfg.game.temperature == 0.3

        # Verify LiteLLMClient accepts it
        client = LiteLLMClient(
            model=cfg.game.model,
            temperature=cfg.game.temperature,
            max_tokens=cfg.game.max_tokens,
        )
        assert client.temperature == 0.3


def test_config_integration_max_tokens():
    """Verify max_tokens config is passed to LiteLLMClient."""
    import tempfile
    from pathlib import Path

    from realtalk.config import ConfigLoader

    with tempfile.TemporaryDirectory() as tmpdir:
        config_file = Path(tmpdir) / ".realtalk.json"
        config_file.write_text('{"game": {"max_tokens": 2048}}')

        loader = ConfigLoader(cwd=Path(tmpdir))
        cfg = loader.load()

        # Verify config has max_tokens
        assert cfg.game.max_tokens == 2048

        # Verify LiteLLMClient accepts it
        client = LiteLLMClient(
            model=cfg.game.model,
            temperature=cfg.game.temperature,
            max_tokens=cfg.game.max_tokens,
        )
        assert client.max_tokens == 2048
