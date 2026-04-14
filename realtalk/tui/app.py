"""Main Textual application for the playable game."""

from __future__ import annotations

from pathlib import Path

from textual.app import App

from realtalk.api import LiteLLMClient
from realtalk.config import ConfigLoader, RuntimeConfig
from realtalk.engine import GameEngine
from realtalk.tui.screens import QuitConfirmScreen, SceneScreen


class RealTalkApp(App[None]):
    CSS = ""
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
