"""Scene and role data for the playable game."""

from __future__ import annotations

from dataclasses import dataclass


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
