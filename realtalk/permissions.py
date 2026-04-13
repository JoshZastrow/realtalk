"""
realtalk.permissions — Layer 5: permission model for game tools.

Permission is a scale: READ_ONLY < GAME_WRITE < CONTRIBUTOR_WRITE.
GamePermissionPolicy.check() is a pure function with no side effects.
The ToolRegistry calls it before every dispatch.

Dependencies: none (no imports from this project).
"""

from __future__ import annotations

from enum import Enum


class PermissionMode(Enum):
    """Permission levels, ordered by privilege.

    READ_ONLY:          tool reads game state but cannot mutate it.
    GAME_WRITE:         tool mutates GameState (mood, security, arc flags).
    CONTRIBUTOR_WRITE:  tool writes session data to disk (contributor mode).
    """

    READ_ONLY = "read-only"
    GAME_WRITE = "game-write"
    CONTRIBUTOR_WRITE = "contributor-write"

    def __le__(self, other: PermissionMode) -> bool:
        order = [
            PermissionMode.READ_ONLY,
            PermissionMode.GAME_WRITE,
            PermissionMode.CONTRIBUTOR_WRITE,
        ]
        return order.index(self) <= order.index(other)

    def __lt__(self, other: PermissionMode) -> bool:
        return self <= other and self != other


class PermissionDenied(Exception):
    """Raised when a tool call requires a higher permission level."""

    def __init__(self, tool_name: str, required: PermissionMode, active: PermissionMode) -> None:
        self.tool_name = tool_name
        self.required = required
        self.active = active
        super().__init__(
            f"tool '{tool_name}' requires {required.value}, "
            f"but active mode is {active.value}"
        )


class GamePermissionPolicy:
    """Evaluate whether a tool call is permitted under the active mode.

    Pure data object — no side effects. Pass to ToolRegistry; never
    prompt the user or fire hooks directly.

    The policy enforces: tool.required_permission <= active_mode.
    """

    def __init__(self, active_mode: PermissionMode) -> None:
        self._active = active_mode

    @property
    def active_mode(self) -> PermissionMode:
        return self._active

    def check(self, tool_name: str, required: PermissionMode) -> None:
        """Raise PermissionDenied if the tool's required permission exceeds active mode."""
        if not (required <= self._active):
            raise PermissionDenied(tool_name, required, self._active)
