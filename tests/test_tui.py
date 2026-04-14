from __future__ import annotations

import asyncio

import pytest

from realtalk.api import ScriptedClient
from realtalk.config import ContributorConfig, DisplayConfig, GameConfig, HookConfig, RuntimeConfig
from realtalk.tui.app import RealTalkApp
from realtalk.tui.screens import QuitConfirmScreen, RoleScreen, SceneScreen
from realtalk.tui.widgets import MenuList, ReactionInput, StatusBar


def _test_config(tmp_path) -> RuntimeConfig:
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
        contributor=ContributorConfig(enabled=False, session_dir=str(tmp_path / "sessions")),
        display=DisplayConfig(no_color=False, debug=False),
        hooks=HookConfig(pre_tool_use=[], post_tool_use=[], post_tool_use_failure=[]),
    )


def _app(tmp_path) -> RealTalkApp:
    return RealTalkApp(config=_test_config(tmp_path), api_client=ScriptedClient([]))


def test_app_starts_on_scene_screen(tmp_path) -> None:
    async def run() -> None:
        async with _app(tmp_path).run_test() as pilot:
            assert isinstance(pilot.app.screen, SceneScreen)

    asyncio.run(run())


def test_scene_selection_advances_to_role(tmp_path) -> None:
    async def run() -> None:
        async with _app(tmp_path).run_test() as pilot:
            await pilot.press("1")
            assert isinstance(pilot.app.screen, RoleScreen)

    asyncio.run(run())


def test_scene_arrow_navigation_updates_selection() -> None:
    widget = MenuList("Scenes", ["A", "B", "C"])
    widget.move(1)
    assert widget.selected_index == 1
    widget.move(-1)
    assert widget.selected_index == 0


def test_ctrl_c_opens_quit_confirmation(tmp_path) -> None:
    async def run() -> None:
        async with _app(tmp_path).run_test() as pilot:
            await pilot.press("ctrl+c")
            assert isinstance(pilot.app.screen, QuitConfirmScreen)

    asyncio.run(run())


def test_status_bar_shows_mood_and_security() -> None:
    widget = StatusBar(label="MOOD", value=74, max_value=100, delta_label="+a2", color="green")
    assert "74" in widget.render_text()
    assert "+a2" in widget.render_text()


def test_reaction_input_accepts_valid() -> None:
    widget = ReactionInput()
    widget.set_value("a2")
    assert widget.direction == "a"
    assert widget.intensity == 2


@pytest.mark.parametrize(
    ("raw", "expected_valid"),
    [
        ("a2", True),
        ("r3", True),
        ("a1", True),
        ("", False),
        ("a", False),
        ("a2x", False),
        ("x5", False),
        ("a0", False),
        ("a4", False),
        ("A2", True),
        ("R1", True),
        (" a2", False),
        ("a 2", False),
    ],
)
def test_reaction_input_validation(raw: str, expected_valid: bool) -> None:
    widget = ReactionInput()
    widget.set_value(raw)
    assert widget.is_valid is expected_valid
