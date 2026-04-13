"""Integration tests for LiteLLMClient with multiple providers.

These tests verify that LiteLLMClient works with real LLM providers.
They require environment variables to be set and are only run when
PYTEST_INTEGRATION=1 is set.

Environment variables:
- ANTHROPIC_API_KEY: required for Anthropic tests
- OPENAI_API_KEY: optional for OpenAI tests
- PYTEST_INTEGRATION: set to '1' to enable integration tests (opt-in)
- LITELLM_DEBUG: set to 'true' to enable litellm debug logging
"""

from __future__ import annotations

import os

import pytest

from realtalk.api import ApiRequest, LiteLLMClient, MessageStop, TextDelta, UsageEvent

# Enable litellm debug if requested
if os.getenv("LITELLM_DEBUG", "").lower() in ("true", "1"):
    import litellm
    litellm._turn_on_debug()

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

    # Use Anthropic model name (litellm auto-detects provider from model prefix)
    model_name = "claude-opus-4-6"
    client = LiteLLMClient(
        model=model_name,
        temperature=0.5,
        max_tokens=100,
        api_key=api_key,
    )

    request = ApiRequest(
        system_prompt=["You are a helpful assistant."],
        messages=[{"role": "user", "content": "Say 'hello world' and stop."}],
        tools=[],
        model=model_name,
    )

    # Collect events
    try:
        events = list(client.stream(request))
    except RuntimeError as e:
        if "Authentication failed" in str(e):
            pytest.skip(f"Anthropic API authentication failed: {e}")
        elif "not found or not supported" in str(e):
            pytest.skip(f"Model not available: {e}")
        raise

    # Verify we got events
    if not events:
        pytest.fail(
            f"Expected at least one event from {model_name}. "
            "Check API key, quota, and model availability. "
            "Run with LITELLM_DEBUG=1 for more info."
        )
    assert len(events) > 0, "Expected at least one event"

    # Verify we got text and a stop event
    has_text = any(isinstance(e, TextDelta) for e in events)
    has_stop = any(isinstance(e, MessageStop) for e in events)
    assert has_text, "Expected at least one TextDelta event"
    assert has_stop, "Expected at least one MessageStop event"

    # Verify response contains expected words
    full_text = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert "hello" in full_text.lower(), f"Expected 'hello' in response: {full_text}"

    # Verify token usage is reported (Phase 5: token accuracy)
    # Note: Some streaming providers don't include usage in streamed events
    usage_events = [e for e in events if isinstance(e, UsageEvent)]
    if usage_events:
        usage = usage_events[0]
        assert usage.input_tokens > 0, "Expected input_tokens > 0"
        assert usage.output_tokens > 0, "Expected output_tokens > 0"


def test_openai_real_call():
    """Live test: call real OpenAI API with small prompt.

    REQUIRES: OPENAI_API_KEY env var set.
    COST: ~$0.0001
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        pytest.skip("OPENAI_API_KEY not set")

    # Use OpenAI model name (litellm auto-detects provider from model prefix)
    model_name = "gpt-4o"
    client = LiteLLMClient(
        model=model_name,
        temperature=0.5,
        max_tokens=100,
        api_key=api_key,
    )

    request = ApiRequest(
        system_prompt=["You are a helpful assistant."],
        messages=[{"role": "user", "content": "Say 'hello world' and stop."}],
        tools=[],
        model=model_name,
    )

    # Collect events
    try:
        events = list(client.stream(request))
    except RuntimeError as e:
        error_str = str(e)
        if "Authentication failed" in error_str:
            pytest.skip(f"OpenAI API authentication failed: {e}")
        elif "not found or not supported" in error_str:
            pytest.skip(f"Model not available: {e}")
        elif "RateLimitError" in error_str or "Rate limit" in error_str:
            pytest.skip(f"OpenAI API rate limited: {e}")
        raise

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

    # Verify token usage is reported (Phase 5: token accuracy)
    # Note: Some streaming providers don't include usage in streamed events
    usage_events = [e for e in events if isinstance(e, UsageEvent)]
    if usage_events:
        usage = usage_events[0]
        assert usage.input_tokens > 0, "Expected input_tokens > 0"
        assert usage.output_tokens > 0, "Expected output_tokens > 0"


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
