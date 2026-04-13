"""tests/test_tools.py — Tests for the tool layer (Layer 5).

Tests exercise:
1. Individual game tool handlers (game_tools.py)
2. Permission enforcement (permissions.py)
3. Hook runner lifecycle (hooks.py)
4. ToolRegistry dispatch pipeline (tools.py)
5. Contributor capture (hooks.py)

All tests use in-memory GameState and default GameConfig.
No real API calls. Contributor tests use tmp_path for disk I/O.
"""

import json
import time
from pathlib import Path

import pytest

from realtalk.config import ConfigLoader, GameConfig, HookConfig, RuntimeConfig
from realtalk.game_tools import (
    GameState,
    handle_apply_reaction,
    handle_character_respond,
    handle_evaluate_choice,
    handle_generate_options,
    handle_trigger_invitation,
)
from realtalk.hooks import ContributorCapture, HookDecision, HookRunner
from realtalk.permissions import GamePermissionPolicy, PermissionDenied, PermissionMode
from realtalk.tools import ALL_TOOL_SPECS, GAME_TOOL_SPECS, ToolRegistry

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def game_config() -> GameConfig:
    """Default game config (no files on disk)."""
    return ConfigLoader(cwd=Path("/nonexistent")).load().game


@pytest.fixture
def game_state() -> GameState:
    return GameState.new(mood=50, security=50)


@pytest.fixture
def config(tmp_path: Path) -> RuntimeConfig:
    return ConfigLoader(cwd=tmp_path).load()


@pytest.fixture
def registry(game_state: GameState, config: RuntimeConfig) -> ToolRegistry:
    return ToolRegistry(game_state=game_state, config=config, session_id="test")


# ---------------------------------------------------------------------------
# 1. Game tool handler tests — character_respond
# ---------------------------------------------------------------------------


def test_character_respond_applies_mood_delta(
    game_state: GameState, game_config: GameConfig,
) -> None:
    """Stage 2 mood delta: 'a' direction with intensity 2 adds 7 points."""
    input_data = {
        "dialogue": "That's sweet of you.",
        "mood_direction": "a",
        "mood_intensity": 2,
        "security_direction": "c",
        "security_intensity": 1,
        "invite_turn": False,
    }
    result = handle_character_respond(input_data, game_state, game_config)
    parsed = json.loads(result)
    assert parsed["mood"] == 57  # 50 + 7


def test_character_respond_applies_security_delta(
    game_state: GameState, game_config: GameConfig,
) -> None:
    """Security bar updated: 'p' direction with intensity 3 subtracts 12."""
    input_data = {
        "dialogue": "I don't know about that.",
        "mood_direction": "a",
        "mood_intensity": 1,
        "security_direction": "p",
        "security_intensity": 3,
        "invite_turn": False,
    }
    result = handle_character_respond(input_data, game_state, game_config)
    parsed = json.loads(result)
    assert parsed["security"] == 38  # 50 - 12


def test_character_respond_returns_dialogue(game_state: GameState, game_config: GameConfig) -> None:
    """Dialogue string is included in the result."""
    input_data = {
        "dialogue": "You always know what to say.",
        "mood_direction": "a",
        "mood_intensity": 1,
        "security_direction": "c",
        "security_intensity": 1,
        "invite_turn": False,
    }
    result = handle_character_respond(input_data, game_state, game_config)
    parsed = json.loads(result)
    assert parsed["dialogue"] == "You always know what to say."


def test_character_respond_invite_turn_flags_arc(
    game_state: GameState, game_config: GameConfig,
) -> None:
    """invite_turn=True with mood >= threshold activates the arc."""
    game_state.mood = 85
    input_data = {
        "dialogue": "I've been thinking about something.",
        "mood_direction": "a",
        "mood_intensity": 1,
        "security_direction": "c",
        "security_intensity": 1,
        "invite_turn": True,
    }
    result = handle_character_respond(input_data, game_state, game_config)
    parsed = json.loads(result)
    assert parsed["arc_active"] is True
    assert game_state.arc_active is True


def test_character_respond_mood_zero_loses(game_state: GameState, game_config: GameConfig) -> None:
    """Mood dropping to 0 triggers a lose condition."""
    game_state.mood = 5
    input_data = {
        "dialogue": "I think we're done here.",
        "mood_direction": "r",
        "mood_intensity": 2,
        "security_direction": "p",
        "security_intensity": 1,
        "invite_turn": False,
    }
    result = handle_character_respond(input_data, game_state, game_config)
    parsed = json.loads(result)
    assert parsed["game_result"] == "lose"
    assert parsed["mood"] == 0


def test_character_respond_clamps_mood_at_100(
    game_state: GameState, game_config: GameConfig,
) -> None:
    """Mood cannot exceed 100."""
    game_state.mood = 95
    input_data = {
        "dialogue": "That means a lot.",
        "mood_direction": "a",
        "mood_intensity": 3,
        "security_direction": "c",
        "security_intensity": 1,
        "invite_turn": False,
    }
    handle_character_respond(input_data, game_state, game_config)
    assert game_state.mood == 100


def test_character_respond_suspends_arc_on_mood_drop(
    game_state: GameState, game_config: GameConfig,
) -> None:
    """Arc is suspended if mood drops below threshold during active arc."""
    game_state.mood = 82
    game_state.arc_active = True
    game_state.arc_turn = 5
    input_data = {
        "dialogue": "Whatever.",
        "mood_direction": "r",
        "mood_intensity": 2,
        "security_direction": "p",
        "security_intensity": 1,
        "invite_turn": False,
    }
    handle_character_respond(input_data, game_state, game_config)
    assert game_state.arc_active is False  # 82 - 7 = 75 < 80


def test_character_respond_invalid_direction(
    game_state: GameState, game_config: GameConfig,
) -> None:
    """Invalid mood_direction raises ValueError."""
    input_data = {
        "dialogue": "Hi",
        "mood_direction": "x",
        "mood_intensity": 1,
        "security_direction": "c",
        "security_intensity": 1,
        "invite_turn": False,
    }
    with pytest.raises(ValueError, match="mood_direction"):
        handle_character_respond(input_data, game_state, game_config)


# ---------------------------------------------------------------------------
# 2. Game tool handler tests — generate_options
# ---------------------------------------------------------------------------


def test_generate_options_returns_three(game_state: GameState, game_config: GameConfig) -> None:
    """Always exactly 3 options stored."""
    input_data = {
        "options": [
            "I like that idea.",
            "You're overthinking it.",
            "Let's talk about something else.",
        ]
    }
    result = handle_generate_options(input_data, game_state, game_config)
    parsed = json.loads(result)
    assert parsed["options_stored"] == 3
    assert len(game_state.pending_options) == 3


def test_generate_options_wrong_count(game_state: GameState, game_config: GameConfig) -> None:
    """Exactly 3 required; 2 or 4 raise ValueError."""
    with pytest.raises(ValueError, match="exactly 3"):
        handle_generate_options({"options": ["a", "b"]}, game_state, game_config)


def test_generate_options_over_25_words(game_state: GameState, game_config: GameConfig) -> None:
    """Each option must be 25 words or fewer."""
    long_option = " ".join(["word"] * 26)
    with pytest.raises(ValueError, match="26 words"):
        handle_generate_options(
            {"options": [long_option, "short", "also short"]}, game_state, game_config
        )


# ---------------------------------------------------------------------------
# 3. Game tool handler tests — apply_reaction
# ---------------------------------------------------------------------------


def test_apply_reaction_a_raises_mood(game_state: GameState, game_config: GameConfig) -> None:
    """'a' direction raises the mood bar."""
    result = handle_apply_reaction({"direction": "a", "intensity": 1}, game_state, game_config)
    parsed = json.loads(result)
    assert parsed["mood"] == 53  # 50 + 3
    assert parsed["delta"] == 3


def test_apply_reaction_r_lowers_mood(game_state: GameState, game_config: GameConfig) -> None:
    """'r' direction lowers the mood bar."""
    result = handle_apply_reaction({"direction": "r", "intensity": 1}, game_state, game_config)
    parsed = json.loads(result)
    assert parsed["mood"] == 47  # 50 - 3
    assert parsed["delta"] == -3


def test_apply_reaction_intensity_deltas(game_state: GameState, game_config: GameConfig) -> None:
    """Intensity 1/2/3 maps to +-3/7/12."""
    for intensity, expected_delta in [(1, 3), (2, 7), (3, 12)]:
        state = GameState.new(mood=50, security=50)
        result = handle_apply_reaction(
            {"direction": "a", "intensity": intensity}, state, game_config
        )
        parsed = json.loads(result)
        assert parsed["delta"] == expected_delta


def test_apply_reaction_clamps_at_boundaries(
    game_state: GameState, game_config: GameConfig,
) -> None:
    """Mood clamps to [0, 100]."""
    game_state.mood = 95
    handle_apply_reaction({"direction": "a", "intensity": 3}, game_state, game_config)
    assert game_state.mood == 100  # 95 + 12 clamped

    game_state.mood = 5
    handle_apply_reaction({"direction": "r", "intensity": 3}, game_state, game_config)
    assert game_state.mood == 0  # 5 - 12 clamped


# ---------------------------------------------------------------------------
# 4. Game tool handler tests — trigger_invitation
# ---------------------------------------------------------------------------


def test_trigger_invitation_sets_arc_active(game_state: GameState, game_config: GameConfig) -> None:
    """Arc flag set on game state when mood >= threshold."""
    game_state.mood = 85
    game_state.turn_number = 10
    result = handle_trigger_invitation({"arc_turn_offset": 3}, game_state, game_config)
    parsed = json.loads(result)
    assert parsed["arc_active"] is True
    assert parsed["arc_turn"] == 13  # turn 10 + offset 3
    assert game_state.arc_active is True


def test_trigger_invitation_blocked_below_threshold(
    game_state: GameState, game_config: GameConfig,
) -> None:
    """Arc not activated if mood is below threshold."""
    game_state.mood = 70
    result = handle_trigger_invitation({"arc_turn_offset": 2}, game_state, game_config)
    parsed = json.loads(result)
    assert parsed["arc_active"] is False
    assert game_state.arc_active is False


def test_trigger_invitation_invalid_offset(game_state: GameState, game_config: GameConfig) -> None:
    """arc_turn_offset must be 1-4."""
    game_state.mood = 85
    with pytest.raises(ValueError, match="arc_turn_offset"):
        handle_trigger_invitation({"arc_turn_offset": 5}, game_state, game_config)


# ---------------------------------------------------------------------------
# 5. Game tool handler tests — evaluate_choice
# ---------------------------------------------------------------------------


def test_evaluate_choice_win(game_state: GameState, game_config: GameConfig) -> None:
    """'attuned' response -> win."""
    result = handle_evaluate_choice({"choice_quality": "attuned"}, game_state, game_config)
    parsed = json.loads(result)
    assert parsed["game_result"] == "win"
    assert game_state.game_result == "win"


def test_evaluate_choice_lose_deflecting(game_state: GameState, game_config: GameConfig) -> None:
    """'deflecting' response -> lose."""
    result = handle_evaluate_choice({"choice_quality": "deflecting"}, game_state, game_config)
    parsed = json.loads(result)
    assert parsed["game_result"] == "lose"


def test_evaluate_choice_lose_overclaiming(game_state: GameState, game_config: GameConfig) -> None:
    """'overclaiming' response -> lose."""
    result = handle_evaluate_choice({"choice_quality": "overclaiming"}, game_state, game_config)
    parsed = json.loads(result)
    assert parsed["game_result"] == "lose"


def test_evaluate_choice_invalid_quality(game_state: GameState, game_config: GameConfig) -> None:
    """Invalid choice_quality raises ValueError."""
    with pytest.raises(ValueError, match="choice_quality"):
        handle_evaluate_choice({"choice_quality": "neutral"}, game_state, game_config)


# ---------------------------------------------------------------------------
# 6. Permission enforcement tests
# ---------------------------------------------------------------------------


def test_permission_allows_matching_level() -> None:
    """GAME_WRITE policy allows GAME_WRITE and READ_ONLY tools."""
    policy = GamePermissionPolicy(PermissionMode.GAME_WRITE)
    policy.check("any_tool", PermissionMode.READ_ONLY)  # should not raise
    policy.check("any_tool", PermissionMode.GAME_WRITE)  # should not raise


def test_permission_denied_raises() -> None:
    """READ_ONLY policy blocks GAME_WRITE tools."""
    policy = GamePermissionPolicy(PermissionMode.READ_ONLY)
    with pytest.raises(PermissionDenied) as exc_info:
        policy.check("character_respond", PermissionMode.GAME_WRITE)
    assert exc_info.value.tool_name == "character_respond"
    assert exc_info.value.required == PermissionMode.GAME_WRITE


def test_permission_contributor_allows_all() -> None:
    """CONTRIBUTOR_WRITE policy allows all permission levels."""
    policy = GamePermissionPolicy(PermissionMode.CONTRIBUTOR_WRITE)
    policy.check("t", PermissionMode.READ_ONLY)
    policy.check("t", PermissionMode.GAME_WRITE)
    policy.check("t", PermissionMode.CONTRIBUTOR_WRITE)


# ---------------------------------------------------------------------------
# 7. Hook runner tests
# ---------------------------------------------------------------------------


def test_hook_pre_no_hooks_configured() -> None:
    """No pre-hooks configured -> ALLOW."""
    runner = HookRunner(HookConfig(pre_tool_use=[], post_tool_use=[], post_tool_use_failure=[]))
    result = runner.pre("test_tool", '{}')
    assert result.decision == HookDecision.ALLOW


def test_hook_pre_allow(tmp_path: Path) -> None:
    """Hook that exits 0 -> ALLOW."""
    runner = HookRunner(HookConfig(
        pre_tool_use=["exit 0"],
        post_tool_use=[],
        post_tool_use_failure=[],
    ))
    result = runner.pre("test_tool", '{}')
    assert result.decision == HookDecision.ALLOW


def test_hook_pre_deny(tmp_path: Path) -> None:
    """Hook that exits 2 -> DENY with reason from stdout."""
    runner = HookRunner(HookConfig(
        pre_tool_use=["echo 'blocked by policy' && exit 2"],
        post_tool_use=[],
        post_tool_use_failure=[],
    ))
    result = runner.pre("test_tool", '{}')
    assert result.decision == HookDecision.DENY
    assert "blocked by policy" in result.reason


def test_hook_post_fires(tmp_path: Path) -> None:
    """Post-hook fires without blocking (writes a marker file)."""
    marker = tmp_path / "hook_fired"
    runner = HookRunner(HookConfig(
        pre_tool_use=[],
        post_tool_use=[f"touch {marker}"],
        post_tool_use_failure=[],
    ))
    runner.post("test_tool", '{}', "result")
    # Give the background process a moment
    time.sleep(0.2)
    assert marker.exists()


def test_hook_env_variables() -> None:
    """Hook environment contains REALTALK_TOOL_NAME and REALTALK_TOOL_INPUT."""
    runner = HookRunner(HookConfig(
        pre_tool_use=["test \"$REALTALK_TOOL_NAME\" = 'my_tool' && exit 0 || exit 2"],
        post_tool_use=[],
        post_tool_use_failure=[],
    ))
    result = runner.pre("my_tool", '{"key": "value"}')
    assert result.decision == HookDecision.ALLOW


# ---------------------------------------------------------------------------
# 8. ToolRegistry dispatch tests
# ---------------------------------------------------------------------------


def test_registry_unknown_tool_returns_error_string(registry: ToolRegistry) -> None:
    """Unknown tool returns error string, not exception."""
    result = registry.execute("nonexistent_tool", '{}')
    assert result.startswith("ERROR:")
    assert "nonexistent_tool" in result


def test_registry_dispatches_character_respond(registry: ToolRegistry) -> None:
    """Registry dispatches character_respond and returns valid JSON."""
    input_json = json.dumps({
        "dialogue": "Hello there.",
        "mood_direction": "a",
        "mood_intensity": 1,
        "security_direction": "c",
        "security_intensity": 1,
        "invite_turn": False,
    })
    result = registry.execute("character_respond", input_json)
    parsed = json.loads(result)
    assert parsed["dialogue"] == "Hello there."
    assert parsed["mood"] == 53  # 50 + 3


def test_registry_execute_reaction(registry: ToolRegistry) -> None:
    """execute_reaction convenience method works."""
    result = registry.execute_reaction("a", 2)
    parsed = json.loads(result)
    assert parsed["mood"] == 57  # 50 + 7
    assert parsed["delta"] == 7


def test_tool_definitions_match_spec_count(registry: ToolRegistry) -> None:
    """tool_definitions() returns exactly 4 LLM-callable tools (not apply_reaction)."""
    defs = registry.tool_definitions()
    assert len(defs) == 4
    names = {d["name"] for d in defs}
    assert "apply_reaction" not in names
    assert "character_respond" in names
    assert "generate_options" in names
    assert "trigger_invitation" in names
    assert "evaluate_choice" in names


def test_all_tool_specs_count() -> None:
    """ALL_TOOL_SPECS has 5 entries (4 LLM + 1 internal)."""
    assert len(ALL_TOOL_SPECS) == 5
    assert len(GAME_TOOL_SPECS) == 4


def test_registry_permission_denied_propagates(tmp_path: Path) -> None:
    """PermissionDenied from policy propagates as exception (caught by runtime)."""
    # Create a config and registry with READ_ONLY mode
    config = ConfigLoader(cwd=tmp_path).load()
    game_state = GameState.new(mood=50, security=50)

    # Manually construct with READ_ONLY policy
    registry = ToolRegistry(game_state=game_state, config=config)
    registry._policy = GamePermissionPolicy(PermissionMode.READ_ONLY)

    with pytest.raises(PermissionDenied):
        registry.execute("character_respond", '{}')


# ---------------------------------------------------------------------------
# 9. Contributor capture tests
# ---------------------------------------------------------------------------


def test_contributor_capture_writes_jsonl(tmp_path: Path) -> None:
    """ContributorCapture writes one JSONL line per capture call."""
    from realtalk.config import ContributorConfig as CC

    contrib_config = CC(enabled=True, session_dir=str(tmp_path))
    capture = ContributorCapture(contrib_config, session_id="sess1")

    capture.capture(
        tool_name="character_respond",
        input_json='{"dialogue": "hi"}',
        output='{"mood": 57}',
        is_error=False,
        turn_number=3,
    )

    jsonl_path = tmp_path / "sess1.jsonl"
    assert jsonl_path.exists()
    lines = jsonl_path.read_text().strip().split("\n")
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["tool"] == "character_respond"
    assert record["turn"] == 3
    assert record["is_error"] is False
    assert record["input"] == {"dialogue": "hi"}  # parsed, not raw string


def test_contributor_capture_noop_when_disabled(tmp_path: Path) -> None:
    """ContributorCapture is a no-op when disabled."""
    from realtalk.config import ContributorConfig as CC

    contrib_config = CC(enabled=False, session_dir=str(tmp_path))
    capture = ContributorCapture(contrib_config, session_id="sess2")

    capture.capture("tool", '{}', "out", False, 1)

    # No file should be created
    assert not list(tmp_path.iterdir())
