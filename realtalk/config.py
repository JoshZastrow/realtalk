"""
realtalk.config — Layer 2: layered game configuration.

Three-tier merge: user ~/.realtalk/config.json < project .realtalk.json
< local .realtalk/settings.local.json. Lower tiers (more local) win on conflict.
Deep-merge for nested dicts (e.g. hooks keys are merged, not overwritten).

Uses chz for live, immutable config objects and pydantic for the JSON
deserialization/validation boundary. Parse once at startup; read typed
fields throughout the game.

Dependencies: none (no imports from this project).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import chz
from pydantic import BaseModel, field_validator


# ---------------------------------------------------------------------------
# Pydantic input models — validation boundary when loading JSON from disk
# ---------------------------------------------------------------------------


class RawGameConfig(BaseModel):
    model: str = "claude-haiku-4-5-20251001"
    min_turns_to_win: int = 8
    turn_hard_cap: int = 25
    arc_trigger_threshold: int = 80   # mood >= this → Invitation Arc
    mood_start_min: int = 30
    mood_start_max: int = 50
    security_start_min: int = 40
    security_start_max: int = 60
    reaction_delta_low: int = 3       # intensity 1 → ±3 pts
    reaction_delta_medium: int = 7    # intensity 2 → ±7 pts
    reaction_delta_high: int = 12     # intensity 3 → ±12 pts

    @field_validator("arc_trigger_threshold")
    @classmethod
    def arc_threshold_in_range(cls, v: int) -> int:
        if not (0 < v <= 100):
            raise ValueError("arc_trigger_threshold must be 1–100")
        return v


class RawContributorConfig(BaseModel):
    enabled: bool = False
    session_dir: str = "~/.realtalk/sessions"


class RawDisplayConfig(BaseModel):
    no_color: bool = False
    debug: bool = False


class RawHookConfig(BaseModel):
    pre_tool_use: list[str] = []
    post_tool_use: list[str] = []
    post_tool_use_failure: list[str] = []


class RawRuntimeConfig(BaseModel):
    game: RawGameConfig = RawGameConfig()
    contributor: RawContributorConfig = RawContributorConfig()
    display: RawDisplayConfig = RawDisplayConfig()
    hooks: RawHookConfig = RawHookConfig()


# ---------------------------------------------------------------------------
# chz configuration objects — live, immutable, used throughout the game
# ---------------------------------------------------------------------------


@chz.chz
class GameConfig:
    model: str = "claude-haiku-4-5-20251001"
    min_turns_to_win: int = 8
    turn_hard_cap: int = 25
    arc_trigger_threshold: int = 80
    mood_start_min: int = 30
    mood_start_max: int = 50
    security_start_min: int = 40
    security_start_max: int = 60
    reaction_delta_low: int = 3
    reaction_delta_medium: int = 7
    reaction_delta_high: int = 12

    def reaction_delta(self, intensity: int) -> int:
        """Return the mood point delta for a player reaction of the given intensity (1–3).

        >>> GameConfig().reaction_delta(1)
        3
        >>> GameConfig().reaction_delta(2)
        7
        >>> GameConfig().reaction_delta(3)
        12
        """
        return {
            1: self.reaction_delta_low,
            2: self.reaction_delta_medium,
            3: self.reaction_delta_high,
        }[intensity]


@chz.chz
class ContributorConfig:
    enabled: bool = False
    session_dir: str = "~/.realtalk/sessions"

    @chz.init_property
    def resolved_session_dir(self) -> Path:
        return Path(self.session_dir).expanduser()


@chz.chz
class DisplayConfig:
    no_color: bool = False
    debug: bool = False


@chz.chz
class HookConfig:
    pre_tool_use: list[str] = chz.field(default_factory=list)
    post_tool_use: list[str] = chz.field(default_factory=list)
    post_tool_use_failure: list[str] = chz.field(default_factory=list)


@chz.chz
class RuntimeConfig:
    game: GameConfig = chz.field(default_factory=GameConfig)
    contributor: ContributorConfig = chz.field(default_factory=ContributorConfig)
    display: DisplayConfig = chz.field(default_factory=DisplayConfig)
    hooks: HookConfig = chz.field(default_factory=HookConfig)

    @chz.init_property
    def api_key(self) -> str:
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise EnvironmentError(
                "ANTHROPIC_API_KEY is not set.\n"
                "  export ANTHROPIC_API_KEY=sk-ant-..."
            )
        return key


# ---------------------------------------------------------------------------
# Three-tier config loader
# ---------------------------------------------------------------------------


def _deep_merge(base: dict[str, object], override: dict[str, object]) -> dict[str, object]:
    """Mutate *base* in place, merging *override* recursively. Override wins on leaf conflicts.

    >>> _deep_merge({"a": {"x": 1}}, {"a": {"y": 2}})
    {'a': {'x': 1, 'y': 2}}
    >>> _deep_merge({"a": 1}, {"a": 2})
    {'a': 2}
    """
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)  # type: ignore[arg-type]
        else:
            base[key] = value
    return base


class ConfigLoader:
    """Load and deep-merge the three config tiers into a RuntimeConfig.

    Tier priority (last wins):
        1 — ~/.realtalk/config.json          (user global defaults)
        2 — .realtalk.json                   (project settings, committed)
        3 — .realtalk/settings.local.json    (machine overrides, gitignored)

    Usage::

        config = ConfigLoader(cwd=Path.cwd()).load()
        assert config.game.arc_trigger_threshold == 80
    """

    def __init__(self, cwd: Path = Path.cwd()) -> None:
        self.cwd = cwd

    def load(self) -> RuntimeConfig:
        raw = self._load_raw()
        validated = RawRuntimeConfig(**raw)
        return self._to_chz(validated)

    def _load_raw(self) -> dict[str, object]:
        tiers: list[Path] = [
            Path.home() / ".realtalk" / "config.json",
            self.cwd / ".realtalk.json",
            self.cwd / ".realtalk" / "settings.local.json",
        ]
        merged: dict[str, object] = {}
        for path in tiers:
            if path.exists():
                try:
                    data: object = json.loads(path.read_text())
                    if isinstance(data, dict):
                        _deep_merge(merged, data)
                except (json.JSONDecodeError, OSError):
                    pass
        return merged

    def _to_chz(self, raw: RawRuntimeConfig) -> RuntimeConfig:
        return RuntimeConfig(
            game=GameConfig(**raw.game.model_dump()),
            contributor=ContributorConfig(**raw.contributor.model_dump()),
            display=DisplayConfig(**raw.display.model_dump()),
            hooks=HookConfig(**raw.hooks.model_dump()),
        )
