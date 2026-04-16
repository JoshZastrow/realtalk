"""Main Textual application for the playable game."""

from __future__ import annotations

from pathlib import Path

from textual.app import App

from realtalk.api import LiteLLMClient
from realtalk.config import ConfigLoader, RuntimeConfig
from realtalk.engine import GameEngine
from realtalk.tui.screens import QuitConfirmScreen, SceneScreen


class RealTalkApp(App[None]):
    CSS = """
/* ── Palette ────────────────────────────────────────────────────── */

App {
    background: #f5f0e8;
}

Screen {
    background: #f5f0e8;
    color: #2a1f15;
}

/* ── Menu screens ─────────────────────────────────────────────── */

MenuList {
    background: #f5f0e8;
    color: #2a1f15;
    padding: 2 3;
    height: 1fr;
}

/* ── Scene header ─────────────────────────────────────────────── */

SceneHeader {
    background: #f5f0e8;
    color: #8a7a65;
    padding: 1 3 0 3;
    text-align: center;
}

/* ── Title bar ────────────────────────────────────────────────── */

#title-bar {
    background: #f5f0e8;
    color: #8a7a65;
    height: 1;
    padding: 0 2;
}

#title-right {
    dock: right;
    background: #f5f0e8;
    color: #8a7a65;
}

/* ── Dialogue zone ────────────────────────────────────────────── */

DialogueArea {
    background: #f5f0e8;
    color: #2a1f15;
    padding: 2 3;
    height: 1fr;
}

/* ── Separator ────────────────────────────────────────────────── */

.separator {
    background: #ede8de;
    color: #c8c0b0;
    height: 1;
}

/* ── Action zone ─────────────────────────────────────────────── */

StatusBar {
    background: #ede8de;
    color: #8a7a65;
    height: 1;
    padding: 0 2;
}

ReactionInput {
    background: #ede8de;
    color: #2a1f15;
    height: 2;
    padding: 0 2;
}

OptionPicker {
    background: #ede8de;
    color: #2a1f15;
    padding: 0 2 1 2;
}

/* ── Post-game and quit screens ──────────────────────────────── */

PostGameScreen Static {
    background: #f5f0e8;
    color: #2a1f15;
    padding: 2 3;
}

QuitConfirmScreen Static {
    background: #f5f0e8;
    color: #2a1f15;
    padding: 2 3;
}

/* Status line at bottom of game ─────────────────────────────── */

#game-status {
    background: #ede8de;
    color: #8a7a65;
    height: 1;
    padding: 0 2;
}
"""

    BINDINGS = [("ctrl+c", "request_quit", "Quit")]

    def __init__(
        self,
        config: RuntimeConfig | None = None,
        api_client=None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._config = config or ConfigLoader(cwd=Path.cwd()).load()
        self._api_client = api_client or LiteLLMClient(
            model=self._config.game.model,
            temperature=self._config.game.temperature,
            max_tokens=self._config.game.max_tokens,
        )
        self.engine = self._new_engine()

    def _new_engine(self) -> GameEngine:
        return GameEngine(
            api_client=self._api_client,
            config=self._config,
        )

    def on_mount(self) -> None:
        self.push_screen(SceneScreen(self.engine))

    def action_restart(self) -> None:
        self.engine = self._new_engine()
        self.pop_screen()
        self.push_screen(SceneScreen(self.engine))

    def action_request_quit(self) -> None:
        self.push_screen(QuitConfirmScreen(), self._handle_quit_response)

    def _handle_quit_response(self, confirmed: bool) -> None:
        if confirmed:
            self.exit()
