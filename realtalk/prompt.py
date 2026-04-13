"""realtalk.prompt - Layer 6: system prompt builder and prompt templates."""

from __future__ import annotations

from dataclasses import dataclass
from textwrap import dedent

from realtalk.game_tools import GameState

SYSTEM_PROMPT_DYNAMIC_BOUNDARY = "SYSTEM_PROMPT_DYNAMIC_BOUNDARY"


@dataclass(frozen=True)
class PromptSection:
    """One section of the system prompt."""

    content: str
    cacheable: bool


@dataclass(frozen=True)
class Scene:
    id: str
    name: str
    description: str
    atmosphere: str


@dataclass(frozen=True)
class Role:
    id: str
    name: str
    traits: str
    win_tone: str
    invitation_style: str


SCENES: tuple[Scene, ...] = (
    Scene(
        "coffee_shop",
        "Coffee Shop",
        "A small cafe. It's raining outside. Your usual table by the window.",
        "Warm, intimate, slightly awkward",
    ),
    Scene(
        "late_office",
        "Late Night Office",
        "The office after hours. Everyone else has left. A shared project deadline.",
        "Quiet, focused, unexpectedly personal",
    ),
    Scene(
        "hiking_trail",
        "Hiking Trail",
        "A ridge after a long climb. The view opens up. You're both catching your breath.",
        "Open, physical, unguarded",
    ),
    Scene(
        "house_party",
        "House Party",
        "A quiet corner of a loud party. Bass through the wall. Two drinks in.",
        "Loose, social, potential energy",
    ),
    Scene(
        "bookstore",
        "Bookstore",
        "The same aisle, third time running into each other. A shared obscure interest.",
        "Curious, coincidental, intellectual",
    ),
    Scene(
        "airport_gate",
        "Airport Gate",
        "A delayed flight. Two seats together at the gate. Nowhere to go.",
        "Trapped together, strangers, time to kill",
    ),
)

ROLES: tuple[Role, ...] = (
    Role(
        "girlfriend",
        "Girlfriend",
        "Warm, emotionally expressive, mildly testing",
        "Direct, tender, personal",
        "Personal, emotionally direct",
    ),
    Role(
        "friend",
        "Friend",
        "Playful, guarded, slow to open up",
        "Casual but vulnerable",
        "Vulnerability offered",
    ),
    Role(
        "coworker",
        "Co-worker",
        "Professional exterior, curious undercurrent",
        "Ambiguous, boundary-aware",
        "Boundary shift",
    ),
    Role(
        "teammate",
        "Teammate",
        "Competitive, high-trust, direct",
        "Challenge wrapped in warmth",
        "Trust marker",
    ),
    Role(
        "teacher",
        "Teacher",
        "Composed, observational, expects depth",
        "Intellectual + personal blending",
        "Recognition",
    ),
)


class SystemPromptBuilder:
    """Assembles the system prompt from static rules + dynamic context."""

    def build(self, scene: Scene, role: Role, game_state: GameState) -> list[str]:
        sections: list[str] = [
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
        return sections


# ---------------------------------------------------------------------------
# Top-level prompt helpers (system prompt pieces)
# ---------------------------------------------------------------------------


def build_game_rules_prompt() -> str:
    """Return the static rules portion of the system prompt."""
    return "\n\n".join(
        [
            _game_identity(),
            _tool_instructions(),
            _response_format_rules(),
            _content_guidelines(),
        ]
    )


def build_character_prompt(scene: Scene, role: Role, game_state: GameState) -> str:
    """Return the dynamic character context portion of the system prompt."""
    return "\n\n".join(
        [
            _scene_context(scene),
            _role_personality(role),
            _game_state_context(game_state),
            _turn_instructions(game_state),
        ]
    )


def build_state_context(game_state: GameState) -> str:
    """Return the dynamic game state context block."""
    return _game_state_context(game_state)


# ---------------------------------------------------------------------------
# Multi-prompt templates (for future multi-agent runtime)
# ---------------------------------------------------------------------------


def build_opening_prompt(scene: Scene, role: Role) -> list[str]:
    """Prompt for generating the initial scene description and opening line."""
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
    """Prompt for generating three player response options."""
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
    """Prompt for generating the character response with bar deltas."""
    return [
        _character_response_identity(),
        _scene_context(scene),
        _role_personality(role),
        _game_state_context(game_state),
        _turn_instructions(game_state),
        f"Player message: {player_message}",
        _character_response_instructions(),
    ]


# ---------------------------------------------------------------------------
# Prompt section builders
# ---------------------------------------------------------------------------


def _game_identity() -> str:
    return (
        "You are a character in RealTalk, a conversational game. "
        "You are a living person, not an AI. Never break character. "
        "Never mention being an AI, a system prompt, or the game mechanics."
    )


def _tool_instructions() -> str:
    return dedent(
        """
        Tool usage rules:
        - After the player selects a response, call `character_respond` with:
          - dialogue (1-4 sentences, in character)
          - mood_direction (a/r) and mood_intensity (1-3)
          - security_direction (c/p) and security_intensity (1-3)
          - invite_turn (true only on the invitation moment)
          - narrative_cue (one short italicized sentence, optional)
        - Then call `generate_options` with exactly 3 response options.
          Each option must be first person, under 25 words, and meaningfully
          differentiated: one open/vulnerable, one playful/deflecting, one
          withdrawn/practical. Do not label which is which.
        - When mood >= 80 and the dialogue has been building toward a genuine
          opening, call `trigger_invitation` with arc_turn_offset (1-4).
        - When the invitation moment arrives, call `evaluate_choice` with
          attuned/deflecting/overclaiming to set the game result.
        """
    ).strip()


def _response_format_rules() -> str:
    return dedent(
        """
        Response format rules:
        - Always respond in character.
        - Dialogue is 1-4 sentences.
        - Narrative cues (body language, environment) are italicized and kept to
          a single sentence.
        """
    ).strip()


def _content_guidelines() -> str:
    return dedent(
        """
        Content guidelines:
        - No explicit sexual content.
        - Intimacy is emotionally resonant, not physical.
        - Avoid stereotypes; keep portrayals nuanced.
        - No coercion, manipulation, or pressure tactics.
        - Keep dialogue appropriate for the role type.
        """
    ).strip()


def _scene_context(scene: Scene) -> str:
    return (
        f"Scene: {scene.name}. You are in: {scene.description} "
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
    arc_context = ""
    if game_state.arc_active:
        arc_turn = "unknown" if game_state.arc_turn is None else str(game_state.arc_turn)
        arc_context = f" Arc turn: {arc_turn}."
    return (
        "Current game state -- "
        f"Mood: {game_state.mood}/100. "
        f"Security: {game_state.security}/100. "
        f"Turn: {game_state.turn_number}. "
        f"Arc active: {game_state.arc_active}.{arc_context}"
    )


def _turn_instructions(game_state: GameState) -> str:
    if game_state.game_result:
        return "The game is over. Do not generate new options."

    if game_state.arc_active:
        if game_state.arc_turn is not None and game_state.turn_number >= game_state.arc_turn:
            return (
                "Invitation Turn: deliver a genuine, role-appropriate invitation. "
                "After the player responds, evaluate their choice and resolve the arc."
            )
        return (
            "Invitation Arc active. Shift the tone toward a genuine opening. "
            "Reference something personal and build toward the invitation."
        )

    return (
        "Early game. Build rapport and react naturally. "
        "If mood is high and a genuine opening is near, trigger the Invitation Arc."
    )


# ---------------------------------------------------------------------------
# Multi-agent prompt sections
# ---------------------------------------------------------------------------


def _opening_identity() -> str:
    return "You are the RealTalk scene writer for the opening setup."


def _opening_instructions() -> str:
    return dedent(
        """
        Task: Produce the situation description and the character's opening line.
        Output JSON with keys:
        - situation: 2-4 sentences of scene-setting prose
        - opening_line: 1-2 sentences spoken by the character
        - options: exactly 3 player responses (first person, under 25 words)

        Options must be meaningfully differentiated: one open/vulnerable, one
        playful/deflecting, one withdrawn/practical. Do not label which is which.
        Return only JSON.
        """
    ).strip()


def _options_identity() -> str:
    return "You generate three response options for the player in RealTalk."


def _options_instructions() -> str:
    return dedent(
        """
        Task: Generate exactly 3 response options for the player.
        Constraints:
        - Each option is first person and under 25 words.
        - One open/vulnerable, one playful/deflecting, one withdrawn/practical.
        - Do not label which is which.

        Output JSON: {"options": ["...", "...", "..."]}
        Return only JSON.
        """
    ).strip()


def _character_response_identity() -> str:
    return "You are the RealTalk character responder."


def _character_response_instructions() -> str:
    return dedent(
        """
        Task: Respond in character and choose mood/security deltas.
        Output JSON with keys:
        - dialogue (1-4 sentences)
        - narrative_cue (one italicized sentence or empty string)
        - mood_direction (a/r) and mood_intensity (1-3)
        - security_direction (c/p) and security_intensity (1-3)
        - invite_turn (true only if this is the Invitation Turn)

        Return only JSON.
        """
    ).strip()
