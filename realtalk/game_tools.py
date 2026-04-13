"""
realtalk.game_tools — Layer 5: concrete game tool handler functions.

Each function takes:
  - input_data: dict (parsed from JSON)
  - game_state: GameState (mutable reference)
  - game_config: GameConfig (read-only, for thresholds and deltas)

Returns a string — the tool result the LLM sees.
Raises ValueError on invalid input (ToolRegistry catches and records as error).

Dependencies: config.py (GameConfig for reaction deltas, arc threshold).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from realtalk.config import GameConfig

_VALID_MOOD_DIRECTIONS = {"a", "r"}
_VALID_SECURITY_DIRECTIONS = {"c", "p"}
_VALID_INTENSITIES = {1, 2, 3}


def _validate_direction(value: str, valid: set[str], field_name: str) -> str:
    if value not in valid:
        raise ValueError(f"{field_name} must be one of {valid}, got '{value}'")
    return value


def _validate_intensity(value: int, field_name: str) -> int:
    if value not in _VALID_INTENSITIES:
        raise ValueError(f"{field_name} must be 1, 2, or 3, got {value}")
    return value


@dataclass
class GameState:
    """Mutable game state. Mutated by tool handlers, read by the game loop."""

    mood: int
    security: int
    turn_number: int
    arc_active: bool
    arc_turn: int | None
    game_result: str | None

    pending_mood_delta: int
    last_mood_direction: str
    last_mood_intensity: int
    last_security_direction: str
    last_security_intensity: int

    pending_options: list[str] = field(default_factory=list)

    @staticmethod
    def new(mood: int, security: int) -> GameState:
        return GameState(
            mood=mood,
            security=security,
            turn_number=0,
            arc_active=False,
            arc_turn=None,
            game_result=None,
            pending_mood_delta=0,
            last_mood_direction="a",
            last_mood_intensity=0,
            last_security_direction="c",
            last_security_intensity=0,
            pending_options=[],
        )

    def clamp_mood(self) -> None:
        self.mood = max(0, min(100, self.mood))

    def clamp_security(self) -> None:
        self.security = max(0, min(100, self.security))


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


def handle_character_respond(
    input_data: dict[str, object],
    game_state: GameState,
    game_config: GameConfig,
) -> str:
    """LLM character delivers dialogue and updates mood + security bars.

    This is the Stage 2 update (after player reaction). The LLM determines
    how the character responds to the player's chosen response.
    """
    dialogue = str(input_data["dialogue"])
    mood_dir = _validate_direction(
        str(input_data["mood_direction"]), _VALID_MOOD_DIRECTIONS, "mood_direction"
    )
    mood_int = _validate_intensity(int(input_data["mood_intensity"]), "mood_intensity")
    sec_dir = _validate_direction(
        str(input_data["security_direction"]), _VALID_SECURITY_DIRECTIONS, "security_direction"
    )
    sec_int = _validate_intensity(int(input_data["security_intensity"]), "security_intensity")
    invite = bool(input_data["invite_turn"])

    # Apply mood delta (Stage 2)
    mood_delta = game_config.reaction_delta(mood_int)
    if mood_dir == "r":
        mood_delta = -mood_delta
    game_state.mood += mood_delta
    game_state.clamp_mood()

    # Apply security delta
    sec_delta = game_config.reaction_delta(sec_int)
    if sec_dir == "p":
        sec_delta = -sec_delta
    game_state.security += sec_delta
    game_state.clamp_security()

    # Track last deltas for display
    game_state.last_mood_direction = mood_dir
    game_state.last_mood_intensity = mood_int
    game_state.last_security_direction = sec_dir
    game_state.last_security_intensity = sec_int

    # Handle Invitation Arc trigger
    if invite and game_state.mood >= game_config.arc_trigger_threshold:
        game_state.arc_active = True
        if game_state.arc_turn is None:
            game_state.arc_turn = game_state.turn_number

    # If mood drops below threshold during active arc, suspend it
    if game_state.arc_active and game_state.mood < game_config.arc_trigger_threshold:
        game_state.arc_active = False

    # Check for mood-zero lose condition
    if game_state.mood <= 0:
        game_state.game_result = "lose"

    return json.dumps({
        "mood": game_state.mood,
        "security": game_state.security,
        "arc_active": game_state.arc_active,
        "game_result": game_state.game_result,
        "dialogue": dialogue,
    })


def handle_generate_options(
    input_data: dict[str, object],
    game_state: GameState,
    game_config: GameConfig,
) -> str:
    """LLM produces 3 differentiated player response options."""
    options = input_data.get("options")
    if not isinstance(options, list) or len(options) != 3:
        raise ValueError(f"options must be a list of exactly 3 strings, got {type(options)}")

    validated: list[str] = []
    for i, opt in enumerate(options):
        text = str(opt)
        word_count = len(text.split())
        if word_count > 25:
            raise ValueError(
                f"option {i + 1} has {word_count} words (max 25): '{text[:50]}...'"
            )
        validated.append(text)

    game_state.pending_options = validated

    return json.dumps({
        "options_stored": len(validated),
        "options": validated,
    })


def handle_apply_reaction(
    input_data: dict[str, object],
    game_state: GameState,
    game_config: GameConfig,
) -> str:
    """Apply the player's a/r + intensity rating to the mood bar (Stage 1).

    Called BEFORE response selection by the game loop, not by the LLM.
    The raw rating is NEVER sent to the LLM.
    """
    direction = _validate_direction(
        str(input_data["direction"]), _VALID_MOOD_DIRECTIONS, "direction"
    )
    intensity = _validate_intensity(int(input_data["intensity"]), "intensity")

    delta = game_config.reaction_delta(intensity)
    if direction == "r":
        delta = -delta

    game_state.mood += delta
    game_state.clamp_mood()
    game_state.pending_mood_delta = delta

    return json.dumps({
        "mood": game_state.mood,
        "delta": delta,
        "direction": direction,
        "intensity": intensity,
    })


def handle_trigger_invitation(
    input_data: dict[str, object],
    game_state: GameState,
    game_config: GameConfig,
) -> str:
    """Signal entry into the Invitation Arc.

    The LLM calls this when mood >= arc_trigger_threshold and the character's
    dialogue has been building toward a genuine opening over 2-4 turns.
    """
    offset = int(input_data.get("arc_turn_offset", 2))
    if not (1 <= offset <= 4):
        raise ValueError(f"arc_turn_offset must be 1-4, got {offset}")

    if game_state.mood < game_config.arc_trigger_threshold:
        return json.dumps({
            "arc_active": False,
            "reason": f"mood {game_state.mood} < threshold {game_config.arc_trigger_threshold}",
        })

    game_state.arc_active = True
    game_state.arc_turn = game_state.turn_number + offset

    return json.dumps({
        "arc_active": True,
        "arc_turn": game_state.arc_turn,
        "current_turn": game_state.turn_number,
    })


def handle_evaluate_choice(
    input_data: dict[str, object],
    game_state: GameState,
    game_config: GameConfig,
) -> str:
    """Score the player's response during the Invitation Arc.

    Called on the designated Invitation Turn. The LLM evaluates whether
    the player's chosen response meets the character's emotional offer.

    'attuned' = win, 'deflecting' or 'overclaiming' = lose.
    """
    quality = str(input_data["choice_quality"])
    valid_qualities = {"attuned", "deflecting", "overclaiming"}

    if quality not in valid_qualities:
        raise ValueError(
            f"choice_quality must be one of {valid_qualities}, got '{quality}'"
        )

    if quality == "attuned":
        game_state.game_result = "win"
    else:
        game_state.game_result = "lose"

    return json.dumps({
        "game_result": game_state.game_result,
        "choice_quality": quality,
        "final_mood": game_state.mood,
        "final_security": game_state.security,
    })
