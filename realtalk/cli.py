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

import chz
from realtalk.config import RuntimeConfig


def main(config: RuntimeConfig) -> None:
    """Entry point. Receives a fully merged RuntimeConfig from chz."""
    raise NotImplementedError(
        "main() is a stub. Implement: validate config.api_key; load or create "
        "StoredSession via SessionStore; run game loop."
    )


if __name__ == "__main__":
    chz.nested_entrypoint(main)
