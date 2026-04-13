"""
realtalk.config — Layer 2: layered game configuration.

Three-tier merge: user ~/.realtalk/config.json < project .realtalk.json
< local .realtalk/settings.local.json. Lower tiers (more local) win on conflict.
Deep-merge for nested dicts (e.g. hooks keys are merged, not overwritten).
Lists replace entirely — the higher-priority tier wins the whole list.

Uses chz for live, immutable config objects and pydantic for the JSON
deserialization/validation boundary. Parse once at startup; read typed
fields throughout the game.

Design rule: defaults live only in pydantic models. chz classes carry no
defaults of their own (except complex types that need chz.field). This keeps
the source of truth in one place and prevents silent drift.

Dependencies: none (no imports from this project).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import chz
from pydantic import BaseModel, field_validator, model_validator

# ---------------------------------------------------------------------------
# Pydantic input models — validation boundary when loading JSON from disk
# ---------------------------------------------------------------------------


class RawGameConfig(BaseModel):
    model: str = "claude-haiku-4-5-20251001"
    temperature: float = 1.0
    max_tokens: int = 8096
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

    @field_validator("temperature")
    @classmethod
    def temperature_in_range(cls, v: float) -> float:
        if not (0.0 <= v <= 2.0):
            raise ValueError("temperature must be 0.0–2.0")
        return v

    @field_validator("max_tokens")
    @classmethod
    def max_tokens_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("max_tokens must be >= 1")
        return v

    @field_validator("arc_trigger_threshold")
    @classmethod
    def arc_threshold_in_range(cls, v: int) -> int:
        if not (0 < v <= 100):
            raise ValueError("arc_trigger_threshold must be 1–100")
        return v

    @model_validator(mode="after")
    def check_ranges(self) -> RawGameConfig:
        if self.mood_start_min > self.mood_start_max:
            raise ValueError(
                f"mood_start_min ({self.mood_start_min}) must be "
                f"<= mood_start_max ({self.mood_start_max})"
            )
        if self.security_start_min > self.security_start_max:
            raise ValueError(
                f"security_start_min ({self.security_start_min}) must be "
                f"<= security_start_max ({self.security_start_max})"
            )
        if self.turn_hard_cap < 1:
            raise ValueError(
                f"turn_hard_cap must be >= 1, got {self.turn_hard_cap}"
            )
        if self.min_turns_to_win < 1:
            raise ValueError(
                f"min_turns_to_win must be >= 1, got {self.min_turns_to_win}"
            )
        return self


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
#
# Defaults are intentionally absent from scalar fields here. All values are
# populated by ConfigLoader._to_chz() from the validated pydantic models.
# This preserves a single source of truth for defaults.
# ---------------------------------------------------------------------------


@chz.chz
class GameConfig:
    model: str
    temperature: float
    max_tokens: int
    min_turns_to_win: int
    turn_hard_cap: int
    arc_trigger_threshold: int
    mood_start_min: int
    mood_start_max: int
    security_start_min: int
    security_start_max: int
    reaction_delta_low: int
    reaction_delta_medium: int
    reaction_delta_high: int

    def reaction_delta(self, intensity: int) -> int:
        """Return the mood point delta for a player reaction of the given intensity.

        Intensity must be 1, 2, or 3. Raises ValueError for any other value.
        """
        if intensity not in (1, 2, 3):
            raise ValueError(
                f"intensity must be 1, 2, or 3, got {intensity!r}"
            )
        return {
            1: self.reaction_delta_low,
            2: self.reaction_delta_medium,
            3: self.reaction_delta_high,
        }[intensity]


@chz.chz
class ContributorConfig:
    enabled: bool
    session_dir: str

    @chz.init_property
    def resolved_session_dir(self) -> Path:
        return Path(self.session_dir).expanduser()


@chz.chz
class DisplayConfig:
    no_color: bool
    debug: bool


@chz.chz
class HookConfig:
    # chz.field required for mutable collection defaults
    pre_tool_use: list[str] = chz.field(default_factory=list)
    post_tool_use: list[str] = chz.field(default_factory=list)
    post_tool_use_failure: list[str] = chz.field(default_factory=list)


@chz.chz
class RuntimeConfig:
    game: GameConfig
    contributor: ContributorConfig
    display: DisplayConfig
    hooks: HookConfig

    @property
    def api_key(self) -> str:
        """Return the Anthropic API key from the environment.

        Raises EnvironmentError if ANTHROPIC_API_KEY is not set or is empty.
        Evaluated lazily so that config.load() always succeeds — the error
        surfaces only when the key is actually needed.
        """
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise EnvironmentError(
                "ANTHROPIC_API_KEY is not set.\n"
                "  export ANTHROPIC_API_KEY=sk-ant-..."
            )
        return key


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _deep_merge(base: dict[str, object], override: dict[str, object]) -> dict[str, object]:
    """Mutate *base* in place, merging *override* recursively. Override wins on leaf conflicts.

    Dicts are merged recursively. All other types (including lists) are replaced entirely.

    >>> _deep_merge({"a": {"x": 1}}, {"a": {"y": 2}})
    {'a': {'x': 1, 'y': 2}}
    >>> _deep_merge({"a": 1}, {"a": 2})
    {'a': 2}
    >>> _deep_merge({"a": [1, 2]}, {"a": [3]})
    {'a': [3]}
    """
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)  # type: ignore[arg-type]
        else:
            base[key] = value
    return base


# ---------------------------------------------------------------------------
# Three-tier config loader
# ---------------------------------------------------------------------------


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
