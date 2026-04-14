"""Playable game engine and MDP step function."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

from realtalk.api import ApiClient
from realtalk.compact import (
    CompactionConfig,
    compact_session,
    should_compact,
)
from realtalk.config import RuntimeConfig
from realtalk.conversation import ConversationRuntime, TurnSummary
from realtalk.game_tools import GameState
from realtalk.prompt import SystemPromptBuilder
from realtalk.scenes import ROLES, SCENES, Role, Scene
from realtalk.session import Session, new_session
from realtalk.storage import SessionStore, StoredSession
from realtalk.tools import ToolRegistry


@dataclass(frozen=True)
class StateSnapshot:
    mood: int
    security: int
    turn_number: int
    arc_active: bool
    arc_turn: int | None
    game_phase: str
    options: tuple[str, ...]
    last_dialogue: str


@dataclass(frozen=True)
class Action:
    reaction_direction: str
    reaction_intensity: int
    response_choice: int


@dataclass(frozen=True)
class StepResult:
    state: StateSnapshot
    reward: float
    done: bool
    info: dict[str, object]


class GamePhase(Enum):
    SETUP_SCENE = "setup_scene"
    SETUP_ROLE = "setup_role"
    OPENING = "opening"
    PLAYING = "playing"
    ARC_ACTIVE = "arc_active"
    INVITATION_TURN = "invitation_turn"
    GAME_OVER = "game_over"


class EngineError(RuntimeError):
    """Raised when the game engine cannot make progress."""


class GameEngine:
    def __init__(
        self,
        api_client: ApiClient,
        config: RuntimeConfig,
        on_text: Callable[[str], None] | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._api_client = api_client
        self._config = config
        self._on_text = on_text or (lambda text: None)
        self._rng = rng or random.Random()

        self._scene: Scene | None = None
        self._role: Role | None = None
        self._game_state: GameState | None = None
        self._tool_registry: ToolRegistry | None = None
        self._runtime: ConversationRuntime | None = None
        self._prompt_builder = SystemPromptBuilder()
        self._compaction_config = CompactionConfig()
        self._compacted_summary_sections: list[str] = []
        self._trajectory: list[tuple[StateSnapshot, Action, float, StateSnapshot]] = []
        self._last_dialogue = ""
        self._scene_choices = tuple(self._rng.sample(list(SCENES), 3))

        self._session: Session = new_session(str(Path.cwd()), "realtalk", model_hint=config.game.model)
        self._session_store = SessionStore(root=config.contributor.resolved_session_dir)
        self._stored_session: StoredSession = StoredSession(
            self._session_store.session_path(Path(self._session.workspace_root)),
            archive_root=self._session_store.archive_root,
        )
        self._stored_session.append_events(self._session.events)
        self._stored_event_count = len(self._session.events)

    def available_scenes(self) -> list[Scene]:
        return list(self._scene_choices)

    def select_scene(self, scene_id: str) -> None:
        self._scene = next(scene for scene in self._scene_choices if scene.id == scene_id)

    def available_roles(self) -> list[Role]:
        return list(ROLES)

    def select_role(self, role_id: str) -> None:
        if self._scene is None:
            raise EngineError("select_role called before select_scene")
        self._role = next(role for role in ROLES if role.id == role_id)
        mood = self._rng.randint(*self._config.game.starting_mood_range)
        security = self._rng.randint(*self._config.game.starting_security_range)
        self._game_state = GameState.new(mood, security)
        self._tool_registry = ToolRegistry(
            game_state=self._game_state,
            config=self._config,
            session_id=self._session.session_id,
        )
        self._runtime = ConversationRuntime(
            api_client=self._api_client,
            tool_executor=self._tool_registry,
            session=self._session,
            system_prompt=[],
            tool_definitions=self._tool_registry.tool_definitions(),
            on_text=self._on_text,
            model=self._config.game.model,
            max_tokens=self._config.game.max_tokens,
            temperature=self._config.game.temperature,
        )

    def generate_opening(self) -> str:
        runtime = self._require_runtime()
        prompt = list(self._compacted_summary_sections) + self._build_prompt()
        runtime.set_system_prompt(prompt)
        summary = runtime.run_turn("Set the scene and deliver your opening line.")
        self._persist_runtime_events()
        dialogue, _ = _extract_dialogue_and_options(summary)
        if not dialogue:
            raise EngineError("opening: LLM did not call character_respond")
        self._last_dialogue = dialogue
        return dialogue

    def current_state(self) -> StateSnapshot:
        if self._game_state is None:
            raise EngineError("game state not initialized")
        return snapshot_state(self._game_state, self._last_dialogue, self.phase)

    def step(self, action: Action) -> StepResult:
        if self.is_done():
            raise EngineError("step called after game over")

        prev_state = self.current_state()
        tool_registry = self._require_tool_registry()
        runtime = self._require_runtime()

        tool_registry.execute_reaction(
            direction=action.reaction_direction,
            intensity=action.reaction_intensity,
        )
        if self._game_state.mood <= 0:
            self._game_state.game_result = "lose"
            self._game_state.turn_number += 1
            next_state = self.current_state()
            reward = compute_reward(prev_state, next_state, True, self._game_state.game_result)
            self._trajectory.append((prev_state, action, reward, next_state))
            return StepResult(
                state=next_state,
                reward=reward,
                done=True,
                info={
                    "dialogue": "",
                    "options": tuple(self._game_state.pending_options),
                    "compacted": False,
                    "mood_delta": next_state.mood - prev_state.mood,
                    "security_delta": next_state.security - prev_state.security,
                    "game_result": self._game_state.game_result,
                },
            )
        try:
            chosen_option = self._game_state.pending_options[action.response_choice]  # type: ignore[index]
        except IndexError as exc:
            raise EngineError(f"invalid response choice: {action.response_choice}") from exc

        runtime.set_system_prompt(list(self._compacted_summary_sections) + self._build_prompt())
        summary = runtime.run_turn(chosen_option)
        self._persist_runtime_events()

        dialogue, options = _extract_dialogue_and_options(summary)
        if dialogue:
            self._last_dialogue = dialogue

        self._game_state.turn_number += 1
        if (
            self._game_state.game_result is None
            and self._game_state.turn_number >= self._config.game.turn_hard_cap
        ):
            self._game_state.game_result = "lose"

        compacted = False
        if should_compact(runtime.session, self._compaction_config, self._compacted_summary_sections):
            result = compact_session(
                runtime.session,
                self._compaction_config,
                existing_summary_sections=self._compacted_summary_sections,
            )
            runtime.set_session(result.compacted_session)
            self._session = result.compacted_session
            self._compacted_summary_sections = result.summary_sections
            self._stored_event_count = len(result.compacted_session.events)
            compacted = True

        next_state = self.current_state()
        done = self._game_state.game_result is not None
        reward = compute_reward(prev_state, next_state, done, self._game_state.game_result)
        self._trajectory.append((prev_state, action, reward, next_state))
        self._session = runtime.session
        return StepResult(
            state=next_state,
            reward=reward,
            done=done,
            info={
                "dialogue": dialogue,
                "options": options,
                "compacted": compacted,
                "mood_delta": next_state.mood - prev_state.mood,
                "security_delta": next_state.security - prev_state.security,
                "mood_label": _format_delta_label(
                    self._game_state.last_mood_direction,
                    self._game_state.last_mood_intensity,
                ),
                "security_label": _format_delta_label(
                    self._game_state.last_security_direction,
                    self._game_state.last_security_intensity,
                ),
                "game_result": self._game_state.game_result,
            },
        )

    def is_done(self) -> bool:
        return self._game_state is not None and self._game_state.game_result is not None

    @property
    def phase(self) -> GamePhase:
        if self._game_state is not None and self._game_state.game_result is not None:
            return GamePhase.GAME_OVER
        if self._scene is None:
            return GamePhase.SETUP_SCENE
        if self._role is None:
            return GamePhase.SETUP_ROLE
        if self._last_dialogue == "":
            return GamePhase.OPENING
        if self._game_state is not None and self._game_state.arc_active:
            if self._game_state.arc_turn is not None and self._game_state.turn_number >= self._game_state.arc_turn:
                return GamePhase.INVITATION_TURN
            return GamePhase.ARC_ACTIVE
        return GamePhase.PLAYING

    @property
    def game_result(self) -> str | None:
        if self._game_state is None:
            return None
        return self._game_state.game_result

    @property
    def trajectory(self) -> list[tuple[StateSnapshot, Action, float, StateSnapshot]]:
        return list(self._trajectory)

    def _build_prompt(self) -> list[str]:
        if self._scene is None or self._role is None or self._game_state is None:
            raise EngineError("prompt requested before setup is complete")
        return self._prompt_builder.build(self._scene, self._role, self._game_state)

    def _persist_runtime_events(self) -> None:
        runtime = self._require_runtime()
        new_events = runtime.session.events[self._stored_event_count :]
        if new_events:
            self._stored_session.append_events(new_events)
            self._stored_event_count = len(runtime.session.events)
        self._session = runtime.session

    def _require_runtime(self) -> ConversationRuntime:
        if self._runtime is None:
            raise EngineError("runtime not initialized")
        return self._runtime

    def _require_tool_registry(self) -> ToolRegistry:
        if self._tool_registry is None:
            raise EngineError("tool registry not initialized")
        return self._tool_registry


def compute_reward(
    prev_state: StateSnapshot,
    next_state: StateSnapshot,
    done: bool,
    game_result: str | None,
) -> float:
    reward = 0.0
    reward += (next_state.mood - prev_state.mood) / 100.0
    reward += (next_state.security - prev_state.security) / 100.0
    reward += 0.05
    if done and game_result == "win":
        reward += 5.0
    elif done and game_result == "lose":
        reward -= 3.0
    return reward


def snapshot_state(
    game_state: GameState,
    last_dialogue: str,
    phase: GamePhase,
) -> StateSnapshot:
    return StateSnapshot(
        mood=game_state.mood,
        security=game_state.security,
        turn_number=game_state.turn_number,
        arc_active=game_state.arc_active,
        arc_turn=game_state.arc_turn,
        game_phase=phase.value,
        options=tuple(game_state.pending_options),
        last_dialogue=last_dialogue,
    )


def _extract_dialogue_and_options(summary: TurnSummary) -> tuple[str, tuple[str, ...]]:
    dialogue = ""
    options: tuple[str, ...] = ()
    for tool_call in summary.tool_calls:
        try:
            payload = json.loads(tool_call.input_json)
        except (json.JSONDecodeError, TypeError):
            continue
        if tool_call.tool_name == "character_respond":
            dialogue = str(payload.get("dialogue", ""))
        elif tool_call.tool_name == "generate_options":
            raw = payload.get("options", [])
            if isinstance(raw, list):
                options = tuple(str(item) for item in raw)
    return dialogue, options


def _format_delta_label(direction: str, intensity: int) -> str:
    if intensity <= 0:
        return ""
    return f"+{direction}{intensity}"
