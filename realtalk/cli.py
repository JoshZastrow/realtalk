"""
realtalk.cli — Layer 8: entry point.

Uses chz.nested_entrypoint so every RuntimeConfig field is overridable from
the command line without explicit argparse boilerplate:

    realtalk game.model=claude-opus-4-6 display.no_color=true
    realtalk contributor.enabled=true

Build this after all game logic is wired up (v0.6 per spec).

Dependencies: config.py (and everything above it).
"""

from __future__ import annotations

from pathlib import Path

import chz

from realtalk.config import ConfigLoader, RuntimeConfig


def main(config: RuntimeConfig) -> None:
    """Entry point. Receives a fully merged RuntimeConfig from chz.

    Loads configuration, validates API key, initializes storage, and starts the game loop.
    """
    # Validate API key is set
    try:
        api_key = config.api_key  # noqa: F841
    except EnvironmentError as e:
        print(f"✗ {e}")
        print()
        print("Set your API key with:")
        print("  export ANTHROPIC_API_KEY=sk-ant-...")
        return

    # Import here to avoid circular imports
    from realtalk.api import ApiRequest, LiteLLMClient
    from realtalk.storage import SessionStore

    # Initialize storage
    session_store = SessionStore(root=config.contributor.resolved_session_dir)

    # Create LLM client
    client = LiteLLMClient(
        model=config.game.model,
        temperature=getattr(config.game, "temperature", 1.0),
        max_tokens=getattr(config.game, "max_tokens", 8096),
    )

    # Initialize game session (placeholder for Layer 4 game loop)
    print()
    print("╔════════════════════════════════════════════════════════════╗")
    print("║                      REALTALK v1.0                         ║")
    print("╚════════════════════════════════════════════════════════════╝")
    print()
    print(f"Model: {config.game.model}")
    print(f"Min turns to win: {config.game.min_turns_to_win}")
    print(f"Hard cap: {config.game.turn_hard_cap} turns")
    print()

    # Test LLM connection
    print("Initializing LLM connection...")
    try:
        test_request = ApiRequest(
            system_prompt=["You are a helpful assistant."],
            messages=[{"role": "user", "content": "Hello!"}],
            tools=[],
            model=config.game.model,
        )
        response_text = ""
        for event in client.stream(test_request):
            if hasattr(event, "text"):
                response_text += event.text
        print(f"LLM Response: {response_text[:100]}...")
        print()
        print("✓ LLM connection successful!")
    except Exception as e:
        print(f"✗ Failed to connect to LLM: {e}")
        return

    print()
    print("Game loop not yet implemented (Layer 4).")
    print("See docs/spec/ for architecture and development status.")
    print()
    print("Run tests with: pytest")
    print("Check logs at: ~/.realtalk/sessions/")
    print()


def entrypoint() -> None:
    """CLI entrypoint that loads config from files first, then applies CLI overrides."""
    # Load config from files (.realtalk.json, etc.)
    loader = ConfigLoader(cwd=Path.cwd())
    config = loader.load()

    # Call main with the loaded config
    # Future: add chz for CLI overrides on top of file config
    main(config)


if __name__ == "__main__":
    entrypoint()
