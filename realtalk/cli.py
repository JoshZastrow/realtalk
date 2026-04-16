"""CLI entrypoint for the playable Textual app."""

from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

from realtalk.config import ConfigLoader
from realtalk.tui import RealTalkApp


def _ensure_api_key(config) -> bool:
    try:
        config.api_key
        return True
    except EnvironmentError as exc:
        if not sys.stdin.isatty():
            print(exc)
            return False

    print("ANTHROPIC_API_KEY is not set.")
    print("Enter an Anthropic API key to use for this session.")
    key = getpass.getpass("API key: ").strip()
    if not key:
        print("No API key entered.")
        return False
    os.environ["ANTHROPIC_API_KEY"] = key
    return True


def entrypoint() -> None:
    if "--no-color" in sys.argv:
        os.environ["NO_COLOR"] = "1"

    try:
        from dotenv import load_dotenv
        load_dotenv(override=True)
    except ImportError:
        pass

    config = ConfigLoader(cwd=Path.cwd()).load()
    if not _ensure_api_key(config):
        return

    RealTalkApp(config=config).run()


if __name__ == "__main__":
    entrypoint()
