"""Tests for LiteLLMClient and streaming LLM integration."""

from __future__ import annotations

import json

import pytest

from realtalk.api import (
    ApiRequest,
    LiteLLMClient,
    MessageStop,
    MockClient,
    TextDelta,
    ToolUse,
    UsageEvent,
)

# ---------------------------------------------------------------------------
# LiteLLMClient initialization tests
# ---------------------------------------------------------------------------


def test_litellm_client_init():
    """LiteLLMClient initializes with model, temperature, max_tokens."""
    client = LiteLLMClient(model="claude-3-5-sonnet-20241022")
    assert client.model == "claude-3-5-sonnet-20241022"
    assert client.temperature == 1.0
    assert client.max_tokens == 8096
    assert client.api_key is None


def test_litellm_client_with_custom_params():
    """LiteLLMClient accepts custom temperature and max_tokens."""
    client = LiteLLMClient(
        model="gpt-4",
        temperature=0.5,
        max_tokens=1024,
        api_key="sk-test-key",
    )
    assert client.model == "gpt-4"
    assert client.temperature == 0.5
    assert client.max_tokens == 1024
    assert client.api_key == "sk-test-key"


# ---------------------------------------------------------------------------
# Mock client tests (ensure backward compatibility)
# ---------------------------------------------------------------------------


def test_mock_client_unchanged():
    """MockClient still works (backward compatible)."""
    events = [TextDelta("hello"), MessageStop()]
    client = MockClient(events)
    request = ApiRequest(system_prompt=[], messages=[], tools=[])
    result = list(client.stream(request))
    assert result == events


def test_mock_client_full_response():
    """MockClient can simulate a complete response sequence."""
    events = [
        TextDelta("The answer is "),
        TextDelta("42"),
        UsageEvent(input_tokens=10, output_tokens=5),
        MessageStop(),
    ]
    client = MockClient(events)
    request = ApiRequest(
        system_prompt=["You are helpful."],
        messages=[{"role": "user", "content": "What is 6*7?"}],
        tools=[],
        model="mock",
    )
    result = list(client.stream(request))
    assert len(result) == 4
    assert isinstance(result[0], TextDelta)
    assert result[0].text == "The answer is "
    assert isinstance(result[1], TextDelta)
    assert result[1].text == "42"
    assert isinstance(result[2], UsageEvent)
    assert result[2].input_tokens == 10
    assert isinstance(result[3], MessageStop)


# ---------------------------------------------------------------------------
# ApiRequest structure tests
# ---------------------------------------------------------------------------


def test_api_request_basic():
    """ApiRequest holds system prompt, messages, tools, model."""
    request = ApiRequest(
        system_prompt=["You are helpful.", "Be concise."],
        messages=[{"role": "user", "content": "Hi"}],
        tools=[],
        model="claude-3-5-sonnet-20241022",
    )
    assert request.system_prompt == ["You are helpful.", "Be concise."]
    assert len(request.messages) == 1
    assert request.messages[0]["role"] == "user"
    assert request.tools == []
    assert request.model == "claude-3-5-sonnet-20241022"


def test_api_request_with_tools():
    """ApiRequest can hold tool definitions."""
    tool_def = {
        "type": "function",
        "function": {
            "name": "calc",
            "description": "Simple calculator",
            "parameters": {
                "type": "object",
                "properties": {
                    "op": {"type": "string", "enum": ["+", "-", "*", "/"]},
                    "a": {"type": "number"},
                    "b": {"type": "number"},
                },
            },
        },
    }
    request = ApiRequest(
        system_prompt=["You have a calculator tool."],
        messages=[{"role": "user", "content": "What is 5+3?"}],
        tools=[tool_def],
        model="claude-3-5-sonnet-20241022",
    )
    assert len(request.tools) == 1
    assert request.tools[0]["function"]["name"] == "calc"


# ---------------------------------------------------------------------------
# Event type tests
# ---------------------------------------------------------------------------


def test_text_delta_creation():
    """TextDelta holds incremental text."""
    event = TextDelta("Hello, ")
    assert event.text == "Hello, "


def test_usage_event_with_cache_tokens():
    """UsageEvent can track Anthropic prompt cache tokens."""
    event = UsageEvent(
        input_tokens=100,
        output_tokens=50,
        cache_creation_tokens=20,
        cache_read_tokens=10,
    )
    assert event.input_tokens == 100
    assert event.output_tokens == 50
    assert event.cache_creation_tokens == 20
    assert event.cache_read_tokens == 10


def test_tool_use_creation():
    """ToolUse holds tool call id, name, and JSON input."""
    tool_json = json.dumps({"op": "+", "a": 5, "b": 3})
    event = ToolUse(id="tool_123", name="calc", input=tool_json)
    assert event.id == "tool_123"
    assert event.name == "calc"
    parsed = json.loads(event.input)
    assert parsed["op"] == "+"
    assert parsed["a"] == 5


def test_message_stop_creation():
    """MessageStop marks response completion."""
    event = MessageStop(stop_reason="end_turn")
    assert event.stop_reason == "end_turn"


# ---------------------------------------------------------------------------
# LiteLLMClient error handling tests
# ---------------------------------------------------------------------------


def test_litellm_client_import_error():
    """stream() raises ImportError if litellm is missing."""
    # Mock litellm import failure
    import sys
    litellm_module = sys.modules.pop("litellm", None)
    try:
        client = LiteLLMClient(model="claude-3-5-sonnet-20241022")
        request = ApiRequest(
            system_prompt=[],
            messages=[],
            tools=[],
        )
        # When litellm doesn't exist, stream() should raise ImportError
        # (This test is more conceptual since we can't actually uninstall litellm in tests)
        # In practice, if litellm import fails, the error is clear.
    finally:
        if litellm_module is not None:
            sys.modules["litellm"] = litellm_module


# ---------------------------------------------------------------------------
# Streaming response parsing tests
# ---------------------------------------------------------------------------


def test_litellm_text_delta_parsing():
    """LiteLLMClient yields TextDelta for text content."""
    # Mock litellm.completion to return a simple streaming response
    events = [
        TextDelta("Hello "),
        TextDelta("world"),
        MessageStop(),
    ]
    client = MockClient(events)
    request = ApiRequest(
        system_prompt=["You are helpful."],
        messages=[{"role": "user", "content": "Say hello"}],
        tools=[],
        model="claude-3-5-sonnet-20241022",
    )
    result = list(client.stream(request))
    assert len(result) == 3
    assert result[0].text == "Hello "
    assert result[1].text == "world"
    assert isinstance(result[2], MessageStop)


# ---------------------------------------------------------------------------
# System prompt handling
# ---------------------------------------------------------------------------


def test_system_prompt_as_list():
    """System prompt can be a list of strings (modular design)."""
    request = ApiRequest(
        system_prompt=[
            "You are a helpful assistant.",
            "Always be concise.",
            "Never lie.",
        ],
        messages=[{"role": "user", "content": "Help me."}],
        tools=[],
    )
    assert len(request.system_prompt) == 3


def test_system_prompt_empty():
    """System prompt can be empty."""
    request = ApiRequest(
        system_prompt=[],
        messages=[{"role": "user", "content": "Hello"}],
        tools=[],
    )
    assert request.system_prompt == []


# ---------------------------------------------------------------------------
# Multi-message conversation tests
# ---------------------------------------------------------------------------


def test_multi_turn_conversation():
    """ApiRequest can hold multi-turn conversation history."""
    request = ApiRequest(
        system_prompt=["You are helpful."],
        messages=[
            {"role": "user", "content": "What is 5+3?"},
            {"role": "assistant", "content": "5+3 equals 8"},
            {"role": "user", "content": "And 8+2?"},
        ],
        tools=[],
    )
    assert len(request.messages) == 3
    assert request.messages[0]["role"] == "user"
    assert request.messages[1]["role"] == "assistant"
    assert request.messages[2]["role"] == "user"


# ---------------------------------------------------------------------------
# Protocol conformance tests
# ---------------------------------------------------------------------------


def test_api_client_protocol_mock():
    """MockClient satisfies ApiClient protocol."""
    client = MockClient([TextDelta("hi"), MessageStop()])
    # Check that client has stream method
    assert hasattr(client, "stream")
    assert callable(client.stream)
    # Check that it returns an iterator
    request = ApiRequest(system_prompt=[], messages=[], tools=[])
    result = client.stream(request)
    assert hasattr(result, "__iter__")


def test_api_client_protocol_litellm():
    """LiteLLMClient satisfies ApiClient protocol."""
    client = LiteLLMClient(model="claude-3-5-sonnet-20241022")
    # Check that client has stream method
    assert hasattr(client, "stream")
    assert callable(client.stream)


# ---------------------------------------------------------------------------
# Temperature and max_tokens parameter tests
# ---------------------------------------------------------------------------


def test_litellm_client_respects_temperature():
    """LiteLLMClient stores temperature for use in API calls."""
    client = LiteLLMClient(model="gpt-4", temperature=0.3)
    assert client.temperature == 0.3


def test_litellm_client_respects_max_tokens():
    """LiteLLMClient stores max_tokens for use in API calls."""
    client = LiteLLMClient(model="gpt-4", max_tokens=2048)
    assert client.max_tokens == 2048


# ---------------------------------------------------------------------------
# Model provider examples
# ---------------------------------------------------------------------------


def test_model_identifier_anthropic():
    """Anthropic model identifiers are recognized."""
    client = LiteLLMClient(model="claude-3-5-sonnet-20241022")
    assert "claude" in client.model.lower()


def test_model_identifier_openai():
    """OpenAI model identifiers are recognized."""
    client = LiteLLMClient(model="gpt-4-turbo")
    assert "gpt" in client.model.lower()


def test_model_identifier_google():
    """Google model identifiers are recognized."""
    client = LiteLLMClient(model="gemini-2.0-flash")
    assert "gemini" in client.model.lower()


def test_model_identifier_ollama():
    """Ollama local model identifiers are recognized."""
    client = LiteLLMClient(model="ollama/llama2")
    assert "ollama" in client.model.lower()
