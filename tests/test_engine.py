from __future__ import annotations

import random
from pathlib import Path

from realtalk.api import MessageStop, ScriptedClient, TextDelta, ToolUse, UsageEvent
from realtalk.compact import CompactionConfig
from realtalk.config import ContributorConfig, DisplayConfig, GameConfig, HookConfig, RuntimeConfig
from realtalk.conversation import format_session_for_api
from realtalk.engine import Action, GameEngine, GamePhase, StateSnapshot, compute_reward
from realtalk.scenes import Scene


def _fast_test_config(
    tmp_path: Path,
    *,
    turn_hard_cap: int = 25,
) -> RuntimeConfig:
    return RuntimeConfig(
        game=GameConfig(
            model="test-model",
            temperature=0.0,
            max_tokens=512,
            min_turns_to_win=8,
            turn_hard_cap=turn_hard_cap,
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


def _turn_sequences(
    dialogue: str,
    options: list[str],
    *,
    mood_direction: str = "a",
    mood_intensity: int = 1,
    security_direction: str = "c",
    security_intensity: int = 1,
    invite_turn: bool = False,
    evaluate_choice: str | None = None,
) -> list[list[object]]:
    first = [
        ToolUse(
            id=f"cr_{abs(hash(dialogue))}",
            name="character_respond",
            input=(
                "{"
                f'"dialogue":"{dialogue}",'
                f'"mood_direction":"{mood_direction}",'
                f'"mood_intensity":{mood_intensity},'
                f'"security_direction":"{security_direction}",'
                f'"security_intensity":{security_intensity},'
                f'"invite_turn":{str(invite_turn).lower()}'
                "}"
            ),
        ),
        ToolUse(
            id=f"go_{abs(hash(dialogue))}",
            name="generate_options",
            input=(
                '{"options":['
                + ",".join(f'"{option}"' for option in options)
                + "]}"
            ),
        ),
    ]
    if evaluate_choice is not None:
        first.append(
            ToolUse(
                id=f"ev_{abs(hash(dialogue))}",
                name="evaluate_choice",
                input=f'{{"choice_quality":"{evaluate_choice}"}}',
            )
        )
    first.extend([UsageEvent(20, 10), MessageStop()])
    return [first, [TextDelta(dialogue), UsageEvent(10, 5), MessageStop()]]


def _build_script(turns: int, *, final_win: bool = True, verbose: bool = False) -> list[list[object]]:
    script = _turn_sequences(
        "Opening line",
        ["I stay", "I tease", "I deflect"],
        mood_intensity=0,
        security_intensity=0,
    )
    for index in range(1, turns + 1):
        text = (
            f"Turn {index} " + ("x" * 300 if verbose else "moves forward")
        )
        script.extend(
            _turn_sequences(
                text,
                [f"opt {index}a", f"opt {index}b", f"opt {index}c"],
                mood_intensity=2 if index < turns else 3,
                security_intensity=1,
                invite_turn=index >= turns - 1,
                evaluate_choice="attuned" if final_win and index == turns else None,
            )
        )
    return script


def _make_engine(tmp_path: Path, script: list[list[object]], *, turn_hard_cap: int = 25) -> GameEngine:
    return GameEngine(
        api_client=ScriptedClient(script),
        config=_fast_test_config(tmp_path, turn_hard_cap=turn_hard_cap),
        rng=random.Random(1),
    )


def _make_playing_engine(tmp_path: Path) -> GameEngine:
    engine = _make_engine(tmp_path, _build_script(3))
    engine.select_scene(engine.available_scenes()[0].id)
    engine.select_role("friend")
    engine.generate_opening()
    return engine


def test_available_scenes_returns_three(tmp_path: Path) -> None:
    scenes = _make_engine(tmp_path, _build_script(1)).available_scenes()
    assert len(scenes) == 3
    assert all(isinstance(scene, Scene) for scene in scenes)


def test_select_scene_locks_in(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path, _build_script(1))
    engine.select_scene(engine.available_scenes()[0].id)
    assert engine.phase == GamePhase.SETUP_ROLE


def test_select_role_initializes_state(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path, _build_script(1))
    engine.select_scene(engine.available_scenes()[0].id)
    engine.select_role("friend")
    state = engine.current_state()
    assert 30 <= state.mood <= 50
    assert 40 <= state.security <= 60
    assert state.turn_number == 0


def test_generate_opening_returns_text_and_options(tmp_path: Path) -> None:
    engine = _make_playing_engine(tmp_path)
    state = engine.current_state()
    assert state.last_dialogue == "Opening line"
    assert len(state.options) == 3


def test_step_advances_turn(tmp_path: Path) -> None:
    engine = _make_playing_engine(tmp_path)
    prev = engine.current_state()
    result = engine.step(Action("a", 2, 0))
    assert result.state.turn_number == prev.turn_number + 1


def test_step_returns_reward(tmp_path: Path) -> None:
    engine = _make_playing_engine(tmp_path)
    result = engine.step(Action("a", 1, 0))
    assert isinstance(result.reward, float)


def test_step_done_on_win(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path, _build_script(1))
    engine.select_scene(engine.available_scenes()[0].id)
    engine.select_role("friend")
    engine.generate_opening()
    result = engine.step(Action("a", 3, 0))
    assert result.done is True
    assert engine.game_result == "win"


def test_step_done_on_mood_zero(tmp_path: Path) -> None:
    engine = _make_playing_engine(tmp_path)
    engine._game_state.mood = 5  # type: ignore[union-attr]
    result = engine.step(Action("r", 3, 2))
    assert result.done is True
    assert engine.game_result == "lose"


def test_step_done_on_turn_cap(tmp_path: Path) -> None:
    engine = _make_playing_engine(tmp_path)
    engine._game_state.turn_number = engine._config.game.turn_hard_cap - 1  # type: ignore[union-attr]
    engine._game_state.game_result = None  # type: ignore[union-attr]
    result = engine.step(Action("a", 1, 0))
    assert result.done is True
    assert engine.game_result == "lose"


def test_reward_large_on_win() -> None:
    reward = compute_reward(
        StateSnapshot(85, 70, 15, True, 15, "invitation_turn", (), ""),
        StateSnapshot(90, 75, 16, True, 15, "game_over", (), ""),
        done=True,
        game_result="win",
    )
    assert reward >= 5.0


def test_trajectory_grows_each_step(tmp_path: Path) -> None:
    engine = _make_playing_engine(tmp_path)
    engine.step(Action("a", 1, 0))
    engine.step(Action("a", 2, 1))
    assert len(engine.trajectory) == 2


def test_full_game_with_scripted_llm(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path, _build_script(25), turn_hard_cap=25)
    engine.select_scene(engine.available_scenes()[0].id)
    engine.select_role("friend")
    engine.generate_opening()

    result = None
    for _ in range(25):
        result = engine.step(Action("a", 2, 0))
        if result.done:
            break

    assert result is not None
    assert engine.game_result == "win"
    assert result.state.game_phase == "game_over"
    loaded = engine._stored_session.load()
    assert loaded.session_id == engine._session.session_id


def test_full_game_with_compaction(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path, _build_script(40, verbose=True), turn_hard_cap=50)
    engine._compaction_config = CompactionConfig(max_estimated_tokens=500)
    engine.select_scene(engine.available_scenes()[0].id)
    engine.select_role("friend")
    engine.generate_opening()

    compacted = False
    result = None
    for _ in range(40):
        result = engine.step(Action("a", 1, 0))
        compacted = compacted or bool(result.info.get("compacted"))
        if result.done:
            break

    assert result is not None
    assert compacted
    assert engine.game_result == "win"
    assert engine._compacted_summary_sections
    messages = format_session_for_api(engine._runtime.session)
    assert all(message["role"] != "system" for message in messages)
