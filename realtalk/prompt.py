"""System prompt builders for the playable game."""

from __future__ import annotations

from dataclasses import dataclass
from textwrap import dedent

from realtalk.game_tools import GameState
from realtalk.scenes import ROLES, SCENES, Role, Scene

SYSTEM_PROMPT_DYNAMIC_BOUNDARY = "SYSTEM_PROMPT_DYNAMIC_BOUNDARY"


@dataclass(frozen=True)
class PromptSection:
    content: str
    cacheable: bool


class SystemPromptBuilder:
    """Assemble static rules and dynamic game context into prompt sections."""

    def build(self, scene: Scene, role: Role, game_state: GameState) -> list[str]:
        return [
            _game_identity(),
            _tool_instructions(),
            _response_format_rules(),
            _content_guidelines(),
            SYSTEM_PROMPT_DYNAMIC_BOUNDARY,
            _scene_context(scene),
            _role_personality(role),
            _game_state_context(game_state),
            _turn_instructions(game_state),
        ]


def build_game_rules_prompt() -> str:
    return "\n\n".join(
        [
            _game_identity(),
            _tool_instructions(),
            _response_format_rules(),
            _content_guidelines(),
        ]
    )


def build_character_prompt(scene: Scene, role: Role, game_state: GameState) -> str:
    return "\n\n".join(
        [
            _scene_context(scene),
            _role_personality(role),
            _game_state_context(game_state),
            _turn_instructions(game_state),
        ]
    )


def build_state_context(game_state: GameState) -> str:
    return _game_state_context(game_state)


def build_opening_prompt(scene: Scene, role: Role) -> list[str]:
    return [
        _opening_identity(),
        _scene_context(scene),
        _role_personality(role),
        _content_guidelines(),
        _opening_instructions(),
    ]


def build_options_prompt(
    scene: Scene,
    role: Role,
    game_state: GameState,
    last_dialogue: str,
) -> list[str]:
    return [
        _options_identity(),
        _scene_context(scene),
        _role_personality(role),
        _game_state_context(game_state),
        f"Last character dialogue: {last_dialogue}",
        _options_instructions(),
    ]


def build_character_response_prompt(
    scene: Scene,
    role: Role,
    game_state: GameState,
    player_message: str,
) -> list[str]:
    return [
        _character_response_identity(),
        _scene_context(scene),
        _role_personality(role),
        _game_state_context(game_state),
        _turn_instructions(game_state),
        f"Player message: {player_message}",
        _character_response_instructions(),
    ]


def _game_identity() -> str:
    return (
        "You are a character in RealTalk, a conversational game. "
        "You are a living person, not an AI. Never break character. "
        "Never reference being an AI, having a system prompt, or the game mechanics."
    )


def _tool_instructions() -> str:
    return dedent(
        """
        Tool usage rules:
        - On the opening turn, call `character_respond` with the opening dialogue and
          zero deltas, then call `generate_options` with the first 3 player responses.
        - After the player selects a response, call `character_respond` with:
          dialogue, mood_direction, mood_intensity, security_direction,
          security_intensity, and invite_turn.
        - Then call `generate_options` with exactly 3 first-person response options.
        - Dialogue stays in character. Options stay under 25 words.
        - Use `trigger_invitation` only after mood is high and the connection has built
          toward a genuine opening.
        - On the final invitation moment, use `evaluate_choice` to resolve the game.
        """
    ).strip()


def _response_format_rules() -> str:
    return dedent(
        """
        Response format rules:
        - Always respond in character.
        - Dialogue should be 1-4 sentences.
        - Narrative cues may be italicized and limited to one short sentence.
        """
    ).strip()


def _content_guidelines() -> str:
    return dedent(
        """
        Content guidelines:
        - No explicit sexual content.
        - No coercion or pressure tactics.
        - Avoid stereotypes and keep portrayals nuanced.
        - Intimacy is emotional, not graphic.
        """
    ).strip()


def _scene_context(scene: Scene) -> str:
    return (
        f"You are in: {scene.description} "
        f"The atmosphere is {scene.atmosphere}."
    )


def _role_personality(role: Role) -> str:
    return (
        f"Your relationship to the player: {role.name}. "
        f"Key traits: {role.traits}. "
        f"Win-condition tone: {role.win_tone}. "
        f"Invitation style: {role.invitation_style}."
    )


def _game_state_context(game_state: GameState) -> str:
    parts = [
        "Current game state --",
        f"Mood: {game_state.mood}/100.",
        f"Security: {game_state.security}/100.",
        f"Turn: {game_state.turn_number}.",
        f"Arc active: {game_state.arc_active}.",
    ]
    if game_state.arc_active and game_state.arc_turn is not None:
        parts.append(f"Invitation arc turn: {game_state.arc_turn}.")
    return " ".join(parts)


def _turn_instructions(game_state: GameState) -> str:
    if game_state.game_result is not None:
        return "The game is over. Do not generate new options."
    if game_state.turn_number == 0 and not game_state.pending_options:
        return (
            "This is the opening turn. Set the scene, deliver your opening line, "
            "then call character_respond with zero deltas and generate_options."
        )
    if game_state.arc_active and game_state.arc_turn is not None:
        if game_state.turn_number >= game_state.arc_turn:
            return "This is the invitation turn. Deliver a genuine invitation and resolve it."
        return "The invitation arc is active. Shift toward a genuine, personal opening."
    return "Build rapport, react naturally, and keep the connection moving forward."


def _opening_identity() -> str:
    return "You are opening a new RealTalk scene."


def _opening_instructions() -> str:
    return (
        "Set the scene, deliver your opening line, and produce 3 response options for the player."
    )


def _options_identity() -> str:
    return "You are generating player response options for RealTalk."


def _options_instructions() -> str:
    return "Generate exactly 3 differentiated response options."


def _character_response_identity() -> str:
    return "You are generating the character's reply for RealTalk."


def _character_response_instructions() -> str:
    return (
        "Return a reply that maps cleanly onto character_respond fields, including "
        "mood_direction and security_direction."
    )
