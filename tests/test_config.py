"""Tests for config layer — three-tier merge, validation, and immutability.

All tests use real disk I/O via tmp_path and monkeypatch. No mocking.
"""

from __future__ import annotations

from pathlib import Path

import chz
import pytest

from realtalk.config import ConfigLoader, _deep_merge

# ---------------------------------------------------------------------------
# From original spec (7 tests)
# ---------------------------------------------------------------------------


def test_defaults_load_cleanly(tmp_path, monkeypatch):
    """Default config loads with expected values when no config files exist."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    config = ConfigLoader(cwd=tmp_path).load()
    assert config.game.arc_trigger_threshold == 80
    assert config.game.min_turns_to_win == 8
    assert config.contributor.enabled is False
    assert config.display.no_color is False


def test_three_tier_merge(tmp_path, monkeypatch):
    """Project tier overrides defaults; unset fields keep defaults."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    (tmp_path / ".realtalk.json").write_text('{"game": {"arc_trigger_threshold": 75}}')
    config = ConfigLoader(cwd=tmp_path).load()
    assert config.game.arc_trigger_threshold == 75
    assert config.game.min_turns_to_win == 8  # unchanged default


def test_local_tier_wins(tmp_path, monkeypatch):
    """Local tier overrides project tier."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    (tmp_path / ".realtalk.json").write_text('{"game": {"model": "claude-sonnet-4-6"}}')
    (tmp_path / ".realtalk").mkdir()
    (tmp_path / ".realtalk" / "settings.local.json").write_text(
        '{"game": {"model": "claude-opus-4-6"}}'
    )
    config = ConfigLoader(cwd=tmp_path).load()
    assert config.game.model == "claude-opus-4-6"


def test_deep_merge_hooks(tmp_path, monkeypatch):
    """Deep merge preserves keys from different tiers."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    (tmp_path / ".realtalk.json").write_text(
        '{"hooks": {"pre_tool_use": ["notify.sh"]}}'
    )
    (tmp_path / ".realtalk").mkdir()
    (tmp_path / ".realtalk" / "settings.local.json").write_text(
        '{"hooks": {"post_tool_use": ["log.sh"]}}'
    )
    config = ConfigLoader(cwd=tmp_path).load()
    assert "notify.sh" in config.hooks.pre_tool_use
    assert "log.sh" in config.hooks.post_tool_use


def test_missing_api_key_raises(tmp_path, monkeypatch):
    """Accessing api_key without ANTHROPIC_API_KEY raises EnvironmentError."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    config = ConfigLoader(cwd=tmp_path).load()
    with pytest.raises(EnvironmentError, match="ANTHROPIC_API_KEY"):
        _ = config.api_key


def test_chz_replace_immutability(tmp_path, monkeypatch):
    """chz.replace creates a modified copy; the original is unchanged."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    config = ConfigLoader(cwd=tmp_path).load()
    modified = chz.replace(config, game=chz.replace(config.game, arc_trigger_threshold=90))
    assert modified.game.arc_trigger_threshold == 90
    assert config.game.arc_trigger_threshold == 80


def test_invalid_arc_threshold_rejected(tmp_path, monkeypatch):
    """arc_trigger_threshold=0 fails pydantic validation."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    (tmp_path / ".realtalk.json").write_text('{"game": {"arc_trigger_threshold": 0}}')
    with pytest.raises(Exception):
        ConfigLoader(cwd=tmp_path).load()


# ---------------------------------------------------------------------------
# From eng review gaps (7 additional tests)
# ---------------------------------------------------------------------------


def test_all_three_tiers_present(tmp_path, monkeypatch):
    """When all three tiers exist, each contributes its overrides."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    # Tier 1: user global
    user_dir = tmp_path / "home" / ".realtalk"
    user_dir.mkdir(parents=True)
    (user_dir / "config.json").write_text('{"game": {"model": "tier1-model"}}')
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    # Tier 2: project
    (tmp_path / ".realtalk.json").write_text('{"game": {"min_turns_to_win": 12}}')
    # Tier 3: local
    (tmp_path / ".realtalk").mkdir(exist_ok=True)
    (tmp_path / ".realtalk" / "settings.local.json").write_text(
        '{"display": {"debug": true}}'
    )
    config = ConfigLoader(cwd=tmp_path).load()
    assert config.game.model == "tier1-model"
    assert config.game.min_turns_to_win == 12
    assert config.display.debug is True


def test_invalid_json_in_config_file(tmp_path, monkeypatch):
    """Malformed JSON in a config file is silently skipped."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    (tmp_path / ".realtalk.json").write_text('{not valid json')
    config = ConfigLoader(cwd=tmp_path).load()
    assert config.game.arc_trigger_threshold == 80  # defaults intact


def test_non_dict_json_in_config_file(tmp_path, monkeypatch):
    """Non-dict JSON (e.g., a list) in a config file is silently skipped."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    (tmp_path / ".realtalk.json").write_text('[1, 2, 3]')
    config = ConfigLoader(cwd=tmp_path).load()
    assert config.game.arc_trigger_threshold == 80  # defaults intact


def test_empty_string_api_key_raises(tmp_path, monkeypatch):
    """Empty string ANTHROPIC_API_KEY is treated as missing."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    config = ConfigLoader(cwd=tmp_path).load()
    with pytest.raises(EnvironmentError, match="ANTHROPIC_API_KEY"):
        _ = config.api_key


def test_list_replace_not_append(tmp_path, monkeypatch):
    """Two tiers both setting pre_tool_use: higher tier wins completely (no merge)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    (tmp_path / ".realtalk.json").write_text(
        '{"hooks": {"pre_tool_use": ["a.sh", "b.sh"]}}'
    )
    (tmp_path / ".realtalk").mkdir()
    (tmp_path / ".realtalk" / "settings.local.json").write_text(
        '{"hooks": {"pre_tool_use": ["c.sh"]}}'
    )
    config = ConfigLoader(cwd=tmp_path).load()
    assert config.hooks.pre_tool_use == ["c.sh"]


def test_cross_validation_mood_range(tmp_path, monkeypatch):
    """mood_start_min > mood_start_max is rejected."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    (tmp_path / ".realtalk.json").write_text(
        '{"game": {"mood_start_min": 60, "mood_start_max": 30}}'
    )
    with pytest.raises(Exception):
        ConfigLoader(cwd=tmp_path).load()


def test_negative_turn_cap_rejected(tmp_path, monkeypatch):
    """Negative turn_hard_cap is rejected."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    (tmp_path / ".realtalk.json").write_text('{"game": {"turn_hard_cap": -1}}')
    with pytest.raises(Exception):
        ConfigLoader(cwd=tmp_path).load()


# ---------------------------------------------------------------------------
# Additional: reaction_delta validation
# ---------------------------------------------------------------------------


def test_reaction_delta_valid_intensities(tmp_path, monkeypatch):
    """reaction_delta returns correct values for intensities 1, 2, 3."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    config = ConfigLoader(cwd=tmp_path).load()
    assert config.game.reaction_delta(1) == 3
    assert config.game.reaction_delta(2) == 7
    assert config.game.reaction_delta(3) == 12


def test_reaction_delta_invalid_intensity_raises(tmp_path, monkeypatch):
    """reaction_delta raises ValueError (not KeyError) for invalid intensity."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    config = ConfigLoader(cwd=tmp_path).load()
    with pytest.raises(ValueError, match="intensity"):
        config.game.reaction_delta(0)
    with pytest.raises(ValueError, match="intensity"):
        config.game.reaction_delta(4)


def test_deep_merge_utility():
    """_deep_merge recursively merges dicts; non-dict values are replaced."""
    result = _deep_merge({"a": {"x": 1}}, {"a": {"y": 2}})
    assert result == {"a": {"x": 1, "y": 2}}

    result = _deep_merge({"a": 1}, {"a": 2})
    assert result == {"a": 2}

    # Lists replace entirely, not merge
    result = _deep_merge({"a": [1, 2]}, {"a": [3]})
    assert result == {"a": [3]}


def test_temperature_valid_range(tmp_path, monkeypatch):
    """Temperature must be 0.0-2.0 (litellm API spec)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    # Valid temperatures
    for temp in [0.0, 0.5, 1.0, 1.5, 2.0]:
        config_file = tmp_path / ".realtalk.json"
        config_file.write_text(f'{{"game": {{"temperature": {temp}}}}}')
        loader = ConfigLoader(cwd=tmp_path)
        cfg = loader.load()
        assert cfg.game.temperature == temp


def test_temperature_out_of_range_rejected(tmp_path, monkeypatch):
    """Temperature < 0.0 or > 2.0 raises ValidationError."""
    import pytest
    from pydantic import ValidationError

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    for temp in [-0.1, 2.1, 5.0]:
        config_file = tmp_path / ".realtalk.json"
        config_file.write_text(f'{{"game": {{"temperature": {temp}}}}}')
        loader = ConfigLoader(cwd=tmp_path)
        with pytest.raises(ValidationError):
            loader.load()


def test_max_tokens_must_be_positive(tmp_path, monkeypatch):
    """max_tokens must be >= 1."""
    import pytest
    from pydantic import ValidationError

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    # Valid
    config_file = tmp_path / ".realtalk.json"
    config_file.write_text('{"game": {"max_tokens": 100}}')
    loader = ConfigLoader(cwd=tmp_path)
    cfg = loader.load()
    assert cfg.game.max_tokens == 100

    # Invalid
    config_file.write_text('{"game": {"max_tokens": 0}}')
    loader = ConfigLoader(cwd=tmp_path)
    with pytest.raises(ValidationError):
        loader.load()


def test_game_config_defaults(tmp_path, monkeypatch):
    """Game config has sensible defaults."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    # Create empty config
    config_file = tmp_path / ".realtalk.json"
    config_file.write_text('{}')
    loader = ConfigLoader(cwd=tmp_path)
    cfg = loader.load()

    # Verify defaults
    assert cfg.game.model == "claude-haiku-4-5-20251001"
    assert cfg.game.temperature == 1.0
    assert cfg.game.max_tokens == 8096
    assert cfg.game.min_turns_to_win == 8
