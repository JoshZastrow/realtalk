"""
realtalk.tools — Layer 5: tool registry and dispatcher.

Owns GAME_TOOL_SPECS (JSON schemas given to the LLM) and ToolRegistry
(the dispatcher that implements ToolExecutor from conversation.py).

Dispatch pipeline:
  1. permission check   (GamePermissionPolicy.check)
  2. pre-hook           (HookRunner.run_pre_tool_use -> HookResult)
  3. execute handler    (game_tools dispatch)
  4. post-hook          (HookRunner.run_post_tool_use)
  5. failure hook       (HookRunner.run_post_tool_use_failure)
  6. contributor capture (if enabled)
  7. return result str / re-raise execution error

Unknown tool -> "ERROR: unknown tool {name}" (not an exception).

Dependencies: permissions.py, hooks.py, game_tools.py, config.py.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from realtalk.config import RuntimeConfig
from realtalk.game_tools import (
    GameState,
    handle_apply_reaction,
    handle_character_respond,
    handle_evaluate_choice,
    handle_generate_options,
    handle_trigger_invitation,
)
from realtalk.hooks import ContributorCapture, HookContext, HookRunner
from realtalk.permissions import GamePermissionPolicy, PermissionMode


@dataclass(frozen=True)
class ToolSpec:
    """A tool definition: name, description, JSON schema, and required permission.

    Mirrors the Rust reference ToolSpec.
    """

    name: str
    description: str
    input_schema: dict[str, object]
    required_permission: PermissionMode


# ---------------------------------------------------------------------------
# Tool definitions — JSON schemas passed to the Anthropic API
# ---------------------------------------------------------------------------

GAME_TOOL_SPECS: list[ToolSpec] = [
    ToolSpec(
        name="character_respond",
        description=(
            "Deliver the character's dialogue and update mood/security bars. "
            "Called after the player selects a response. Returns updated game state."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "dialogue": {
                    "type": "string",
                    "description": "Character's spoken line (1-4 sentences, in character).",
                },
                "mood_direction": {
                    "type": "string",
                    "enum": ["a", "r"],
                    "description": "'a' (attraction/warmth) or 'r' (repulsion/coolness).",
                },
                "mood_intensity": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 3,
                    "description": "0=no bar change, 1=subtle, 2=noticeable, 3=strong.",
                },
                "security_direction": {
                    "type": "string",
                    "enum": ["c", "p"],
                    "description": "'c' (comfort/openness) or 'p' (protective/guarded).",
                },
                "security_intensity": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 3,
                    "description": "0=no bar change, 1=subtle, 2=noticeable, 3=strong.",
                },
                "narrative_cue": {
                    "type": "string",
                    "description": "Brief body language or environmental detail in italics.",
                },
                "invite_turn": {
                    "type": "boolean",
                    "description": "True if this is an Invitation Arc turn.",
                },
            },
            "required": [
                "dialogue",
                "mood_direction",
                "mood_intensity",
                "security_direction",
                "security_intensity",
                "invite_turn",
            ],
            "additionalProperties": False,
        },
        required_permission=PermissionMode.GAME_WRITE,
    ),
    ToolSpec(
        name="generate_options",
        description=(
            "Generate exactly 3 response options for the player to choose from. "
            "Options must be meaningfully differentiated: one open/vulnerable, "
            "one playful/deflecting, one withdrawn/practical. Each under 25 words, "
            "first person. Do NOT label which is which."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 3,
                    "maxItems": 3,
                    "description": "Exactly 3 response options, each under 25 words.",
                },
            },
            "required": ["options"],
            "additionalProperties": False,
        },
        required_permission=PermissionMode.GAME_WRITE,
    ),
    ToolSpec(
        name="trigger_invitation",
        description=(
            "Signal entry into the Invitation Arc. Call when the character's mood "
            "is >= 80 and the dialogue has been building toward a genuine opening. "
            "The arc unfolds over 2-4 turns before the Invitation Turn."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "arc_turn_offset": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 4,
                    "description": "Turns until the Invitation Turn (1-4).",
                },
            },
            "additionalProperties": False,
        },
        required_permission=PermissionMode.GAME_WRITE,
    ),
    ToolSpec(
        name="evaluate_choice",
        description=(
            "Score the player's response on the Invitation Turn. Evaluate whether "
            "the player met the character's emotional offer with genuine presence. "
            "'attuned' = win (authentic connection). 'deflecting' = lose (killed the "
            "moment). 'overclaiming' = lose (pushed too fast, broke trust)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "choice_quality": {
                    "type": "string",
                    "enum": ["attuned", "deflecting", "overclaiming"],
                    "description": "How well the player's response matches the moment.",
                },
            },
            "required": ["choice_quality"],
            "additionalProperties": False,
        },
        required_permission=PermissionMode.GAME_WRITE,
    ),
]

# apply_reaction is internal-only (called by game loop, not by LLM)
_APPLY_REACTION_SPEC = ToolSpec(
    name="apply_reaction",
    description="Apply player's a/r reaction rating to mood bar (Stage 1). Internal only.",
    input_schema={
        "type": "object",
        "properties": {
            "direction": {
                "type": "string",
                "enum": ["a", "r"],
                "description": "'a' (attraction) or 'r' (repulsion).",
            },
            "intensity": {
                "type": "integer",
                "minimum": 1,
                "maximum": 3,
                "description": "1=low (+-3), 2=medium (+-7), 3=high (+-12).",
            },
        },
        "required": ["direction", "intensity"],
        "additionalProperties": False,
    },
    required_permission=PermissionMode.GAME_WRITE,
)

ALL_TOOL_SPECS: list[ToolSpec] = [*GAME_TOOL_SPECS, _APPLY_REACTION_SPEC]


# ---------------------------------------------------------------------------
# Handler dispatch table
# ---------------------------------------------------------------------------

_HANDLER_MAP = {
    "character_respond": handle_character_respond,
    "generate_options": handle_generate_options,
    "apply_reaction": handle_apply_reaction,
    "trigger_invitation": handle_trigger_invitation,
    "evaluate_choice": handle_evaluate_choice,
}


# ---------------------------------------------------------------------------
# ToolRegistry — the dispatcher (implements ToolExecutor)
# ---------------------------------------------------------------------------


class ToolRegistry:
    """Wires permissions, hooks, and tool handlers into a single dispatch point.

    Implements the ToolExecutor protocol from conversation.py:
        def execute(self, tool_name: str, tool_input: str) -> str

    Construction:
        registry = ToolRegistry(
            game_state=GameState.new(mood=42, security=55),
            config=config,
            session_id="abc123",
        )
    """

    def __init__(
        self,
        game_state: GameState,
        config: RuntimeConfig,
        session_id: str = "",
    ) -> None:
        self._game_state = game_state
        self._game_config = config.game
        self._policy = GamePermissionPolicy(
            active_mode=PermissionMode.CONTRIBUTOR_WRITE
            if config.contributor.enabled
            else PermissionMode.GAME_WRITE
        )
        self._hooks = HookRunner(config.hooks)
        self._contributor = ContributorCapture(config.contributor, session_id)
        self._spec_map = {spec.name: spec for spec in ALL_TOOL_SPECS}

    @property
    def game_state(self) -> GameState:
        """Current game state (mutable reference)."""
        return self._game_state

    def execute(self, tool_name: str, tool_input: str) -> str:
        """Dispatch a tool call through the full pipeline.

        Pipeline:
          1. Lookup spec (unknown -> error string, not exception)
          2. Permission check (denied -> PermissionDenied exception)
          3. Pre-hook (deny/failure -> error string)
          4. Parse input JSON
          5. Execute handler
          6. Post-hook
          7. Failure hook + contributor capture on execution error
          8. Contributor capture on success
          9. Return result string
        """
        # 1. Lookup
        spec = self._spec_map.get(tool_name)
        if spec is None:
            return f"ERROR: unknown tool {tool_name}"

        # 2. Permission check (raises PermissionDenied on failure)
        self._policy.check(tool_name, spec.required_permission)

        # 3. Pre-hook
        hook_ctx = HookContext(tool_name=tool_name, tool_input=tool_input)
        hook_result = self._hooks.run_pre_tool_use(hook_ctx)
        if hook_result.denied:
            return f"ERROR: blocked by hook: {hook_result.reason}"
        if hook_result.failed:
            return f"ERROR: hook failed: {hook_result.reason}"

        try:
            # 4. Parse input
            input_data: dict[str, object] = json.loads(tool_input) if tool_input else {}

            # 5. Execute handler
            handler = _HANDLER_MAP[tool_name]
            result = handler(input_data, self._game_state, self._game_config)
        except Exception as exc:
            error_text = f"ERROR: {type(exc).__name__}: {exc}"
            self._hooks.run_post_tool_use_failure(
                HookContext(
                    tool_name=tool_name,
                    tool_input=tool_input,
                    tool_output=error_text,
                    tool_is_error=True,
                )
            )
            self._contributor.capture(
                tool_name=tool_name,
                input_json=tool_input,
                output=error_text,
                is_error=True,
                turn_number=self._game_state.turn_number,
            )
            raise

        # 6. Post-hook (fire-and-forget)
        self._hooks.run_post_tool_use(
            HookContext(
                tool_name=tool_name,
                tool_input=tool_input,
                tool_output=result,
                tool_is_error=False,
            )
        )

        # 7. Contributor capture
        self._contributor.capture(
            tool_name=tool_name,
            input_json=tool_input,
            output=result,
            is_error=False,
            turn_number=self._game_state.turn_number,
        )

        return result

    def tool_definitions(self) -> list[dict[str, object]]:
        """Return Anthropic-format tool definitions for ApiRequest.tools.

        Only returns LLM-callable tools (GAME_TOOL_SPECS). Internal tools
        like apply_reaction are excluded.
        """
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "input_schema": spec.input_schema,
            }
            for spec in GAME_TOOL_SPECS
        ]

    def execute_reaction(self, direction: str, intensity: int) -> str:
        """Convenience method for the game loop to apply player reaction.

        Bypasses the LLM — called directly by the CLI game loop after the
        player inputs their a/r + intensity rating.
        """
        input_json = json.dumps({"direction": direction, "intensity": intensity})
        return self.execute("apply_reaction", input_json)
