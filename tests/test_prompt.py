"""tests/test_prompt.py — Prompt builder tests (Layer 6)."""

from realtalk.game_tools import GameState
from realtalk.prompt import (
    ROLES,
    SCENES,
    SYSTEM_PROMPT_DYNAMIC_BOUNDARY,
    SystemPromptBuilder,
    build_character_response_prompt,
    build_opening_prompt,
    build_options_prompt,
)


# ---------------------------------------------------------------------------
# System prompt builder acceptance criteria
# ---------------------------------------------------------------------------


def test_build_returns_sections_list() -> None:
    builder = SystemPromptBuilder()
    sections = builder.build(SCENES[0], ROLES[0], GameState.new(42, 55))
    assert isinstance(sections, list)
    assert len(sections) >= 5
    assert all(isinstance(s, str) for s in sections)


def test_boundary_marker_present() -> None:
    builder = SystemPromptBuilder()
    sections = builder.build(SCENES[0], ROLES[0], GameState.new(42, 55))
    assert SYSTEM_PROMPT_DYNAMIC_BOUNDARY in sections


def test_static_sections_identical_across_states() -> None:
    builder = SystemPromptBuilder()
    s1 = builder.build(SCENES[0], ROLES[0], GameState.new(42, 55))
    s2 = builder.build(SCENES[1], ROLES[2], GameState.new(80, 30))
    boundary_idx = s1.index(SYSTEM_PROMPT_DYNAMIC_BOUNDARY)
    assert s1[:boundary_idx] == s2[:boundary_idx]


def test_dynamic_sections_reflect_game_state() -> None:
    builder = SystemPromptBuilder()
    state = GameState.new(85, 70)
    state.arc_active = True
    sections = builder.build(SCENES[0], ROLES[0], state)
    joined = "\n".join(sections)
    assert "85" in joined
    assert "arc" in joined.lower()


def test_scene_appears_in_dynamic_section() -> None:
    builder = SystemPromptBuilder()
    sections = builder.build(SCENES[0], ROLES[0], GameState.new(42, 55))
    boundary_idx = sections.index(SYSTEM_PROMPT_DYNAMIC_BOUNDARY)
    dynamic = "\n".join(sections[boundary_idx + 1 :])
    assert "coffee" in dynamic.lower() or "cafe" in dynamic.lower()


def test_role_personality_in_prompt() -> None:
    builder = SystemPromptBuilder()
    sections = builder.build(SCENES[0], ROLES[1], GameState.new(42, 55))
    joined = "\n".join(sections)
    assert "playful" in joined.lower()


def test_tool_instructions_in_static_section() -> None:
    builder = SystemPromptBuilder()
    sections = builder.build(SCENES[0], ROLES[0], GameState.new(42, 55))
    boundary_idx = sections.index(SYSTEM_PROMPT_DYNAMIC_BOUNDARY)
    static = "\n".join(sections[:boundary_idx])
    assert "character_respond" in static
    assert "generate_options" in static


def test_arc_instructions_when_arc_active() -> None:
    builder = SystemPromptBuilder()
    state = GameState.new(85, 70)
    state.arc_active = True
    sections = builder.build(SCENES[0], ROLES[0], state)
    joined = "\n".join(sections)
    assert "invitation" in joined.lower() or "genuine" in joined.lower()


def test_no_arc_instructions_when_arc_inactive() -> None:
    builder = SystemPromptBuilder()
    state = GameState.new(42, 55)
    sections = builder.build(SCENES[0], ROLES[0], state)
    joined = "\n".join(sections)
    assert "invitation turn" not in joined.lower()


# ---------------------------------------------------------------------------
# Multi-prompt helpers
# ---------------------------------------------------------------------------


def test_opening_prompt_mentions_scene_and_role() -> None:
    sections = build_opening_prompt(SCENES[0], ROLES[0])
    joined = "\n".join(sections).lower()
    assert "coffee" in joined or "cafe" in joined
    assert "girlfriend" in joined


def test_opening_prompt_requests_three_options() -> None:
    sections = build_opening_prompt(SCENES[0], ROLES[0])
    joined = "\n".join(sections).lower()
    assert "options" in joined
    assert "3" in joined


def test_options_prompt_includes_last_dialogue() -> None:
    last_dialogue = "Do you always sit here?"
    sections = build_options_prompt(
        SCENES[0], ROLES[0], GameState.new(42, 55), last_dialogue
    )
    joined = "\n".join(sections)
    assert last_dialogue in joined


def test_character_response_prompt_mentions_mood_security_fields() -> None:
    sections = build_character_response_prompt(
        SCENES[0], ROLES[0], GameState.new(42, 55), "Hey."
    )
    joined = "\n".join(sections).lower()
    assert "mood_direction" in joined
    assert "security_direction" in joined
