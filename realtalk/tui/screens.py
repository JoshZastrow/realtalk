"""Textual screens for the Realtalk flow."""

from __future__ import annotations

import asyncio

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import ModalScreen, Screen
from textual.widgets import Label, Static

from realtalk.engine import Action, GameEngine
from realtalk.tui.widgets import DialogueArea, MenuList, OptionPicker, ReactionInput, StatusBar, block_bar

# ── ASCII scene headers ────────────────────────────────────────────────────────

SCENE_HEADERS: dict[str, str] = {
    "coffee_shop": (
        "   ( )\n"
        "  (   )\n"
        "   \\_/\n"
        "  ──────"
    ),
    "late_office": (
        "    💡\n"
        "  ┌───┐\n"
        "  │   │\n"
        "  └───┘"
    ),
    "hiking_trail": (
        "    /\\\n"
        "   /  \\\n"
        "  /────\\\n"
        "   ────"
    ),
    "house_party": (
        "  ♪  ♫  ♪\n"
        " ·  · · ·\n"
        "  · · ·\n"
        "  ──────"
    ),
    "bookstore": (
        "  ╔═╗ ╔═╗\n"
        "  ║ ║ ║ ║\n"
        "  ╚═╝ ╚═╝\n"
        "  ──────"
    ),
    "airport_gate": (
        "   ✈\n"
        "  ───────\n"
        "  │ │ │ │\n"
        "  ───────"
    ),
}

_DEFAULT_HEADER = (
    "  ·  ·  ·\n"
    " ·  · ·  ·\n"
    "  ·  ·  ·\n"
    "  ──────"
)


class SceneHeader(Static):
    """Centered ASCII illustration + scene/role label shown above dialogue."""

    def __init__(self, scene_id: str, scene_name: str, role_name: str) -> None:
        art = SCENE_HEADERS.get(scene_id, _DEFAULT_HEADER)
        label = f"{scene_name.upper()} · {role_name.upper()}"
        separator = "─" * 44
        content = (
            f"[dim]{art}[/dim]\n"
            f"[dim]{label}[/dim]\n"
            f"[dim]{separator}[/dim]"
        )
        super().__init__(content)


# ── Screens ────────────────────────────────────────────────────────────────────


class SceneScreen(Screen[None]):
    def __init__(self, engine: GameEngine) -> None:
        super().__init__()
        self.engine = engine
        scenes = [scene.name for scene in self.engine.available_scenes()]
        self.menu = MenuList("Choose a scene:", scenes)

    def compose(self) -> ComposeResult:
        yield self.menu

    async def on_key(self, event) -> None:
        if event.key == "up":
            self.menu.move(-1)
        elif event.key == "down":
            self.menu.move(1)
        elif event.key in {"1", "2", "3"}:
            self.menu.set_selected_index(int(event.key) - 1)
            self._select_current()
        elif event.key == "enter":
            self._select_current()

    def _select_current(self) -> None:
        scene = self.engine.available_scenes()[self.menu.selected_index]
        self.engine.select_scene(scene.id)
        self.app.push_screen(RoleScreen(self.engine))


class RoleScreen(Screen[None]):
    def __init__(self, engine: GameEngine) -> None:
        super().__init__()
        self.engine = engine
        roles = [role.name for role in self.engine.available_roles()]
        self.menu = MenuList("Choose a role:", roles)

    def compose(self) -> ComposeResult:
        yield self.menu

    async def on_key(self, event) -> None:
        if event.key == "up":
            self.menu.move(-1)
        elif event.key == "down":
            self.menu.move(1)
        elif event.key in {"1", "2", "3", "4", "5"}:
            self.menu.set_selected_index(int(event.key) - 1)
            self._select_current()
        elif event.key == "enter":
            self._select_current()

    def _select_current(self) -> None:
        role = self.engine.available_roles()[self.menu.selected_index]
        self.engine.select_role(role.id)
        self.app.push_screen(SituationScreen(self.engine))


class SituationScreen(Screen[None]):
    def __init__(self, engine: GameEngine) -> None:
        super().__init__()
        self.engine = engine
        self.opening = "Generating opening"
        self._ready = False
        self._spinner_frame = 0
        self._spinner_timer = None
        self._body = Static(self.opening)

    def compose(self) -> ComposeResult:
        scene = self.engine._scene
        role = self.engine._role
        if scene is not None and role is not None:
            yield SceneHeader(scene.id, scene.name, role.name)
        yield self._body

    async def on_mount(self) -> None:
        self._spinner_timer = self.set_interval(0.25, self._tick_spinner)
        try:
            opening = await asyncio.to_thread(self.engine.generate_opening)
        except Exception as exc:
            if self._spinner_timer is not None:
                self._spinner_timer.stop()
            self._body.update(f"Failed to generate opening.\n\n{exc}")
            return
        if self._spinner_timer is not None:
            self._spinner_timer.stop()
        self.opening = opening
        self._ready = True
        self._body.update(f"{self.opening}\n\n[dim]Press Enter to continue.[/dim]")

    async def on_key(self, event) -> None:
        if event.key == "enter" and self._ready:
            self.app.push_screen(GameScreen(self.engine))

    def _tick_spinner(self) -> None:
        if self._ready:
            return
        self._spinner_frame += 1
        # Advance quickly to ~90% then hold — never completes until API returns
        progress = int(min(90, self._spinner_frame * 1.5))
        bar = block_bar(progress, width=20)
        self._body.update(
            f"[dim]setting the scene...[/dim]\n\n"
            f"[dim #8a7a65]{bar}[/dim #8a7a65]"
        )


class GameScreen(Screen[None]):
    def __init__(self, engine: GameEngine) -> None:
        super().__init__()
        self.engine = engine
        self.reaction = ReactionInput()
        self.options = OptionPicker()
        self.dialogue = DialogueArea("")
        self._processing = False
        self._spinner_frame = 0
        self._spinner_timer = None
        self._dialogue_text = ""
        self._spinner_text = ""
        self._status = Static("", id="game-status")
        state = self.engine.current_state()
        self.mood = StatusBar("MOOD", state.mood, delta_label="")
        self.security = StatusBar("SECURITY", state.security, delta_label="")
        self.options.set_options(state.options)
        self._dialogue_text = state.last_dialogue
        self._render_dialogue()

    def compose(self) -> ComposeResult:
        # Title bar
        with Horizontal(id="title-bar"):
            yield Label("[dim]realtalk[/dim]", id="title-left")
            yield Label("[dim]^C quit[/dim]", id="title-right")
        # Scene header
        scene = self.engine._scene
        role = self.engine._role
        if scene is not None and role is not None:
            yield SceneHeader(scene.id, scene.name, role.name)
        # Dialogue zone (borderless, padded, 1fr)
        yield self.dialogue
        # Separator
        yield Static("─" * 120, classes="separator")
        # Action zone: status bars
        yield self.mood
        yield self.security
        # Reaction input
        yield self.reaction
        # Options
        yield self.options
        # Status (thinking indicator)
        yield self._status

    async def on_mount(self) -> None:
        self._update_mood_class()

    def _update_mood_class(self) -> None:
        """Toggle mood-tier CSS class for shifting accent color."""
        self.remove_class("mood-warm", "mood-mid", "mood-cold")
        if self.mood.value >= 65:
            self.add_class("mood-warm")
        elif self.mood.value >= 35:
            self.add_class("mood-mid")
        else:
            self.add_class("mood-cold")

    async def on_key(self, event) -> None:
        if self._processing:
            return
        if event.key in {"1", "2", "3"} and self.reaction.is_valid:
            self._processing = True
            self._spinner_text = ""
            self._status.update("[dim]Thinking...[/dim]")
            self._spinner_timer = self.set_interval(0.25, self._tick_spinner)
            action = Action(
                self.reaction.direction or "a",
                self.reaction.intensity or 1,
                int(event.key) - 1,
            )
            asyncio.create_task(self._run_step(action))
        elif event.key in {"a", "r", "A", "R"}:
            self.reaction.set_value(event.key.lower())
        elif event.key in {"1", "2", "3"} and self.reaction.raw_value in {"a", "r"}:
            self.reaction.set_value(self.reaction.raw_value + event.key)
        elif event.key == "backspace":
            self.reaction.set_value("")

    async def _run_step(self, action: Action) -> None:
        old_mood = self.mood.value
        old_security = self.security.value
        result = await asyncio.to_thread(self.engine.step, action)
        self._dialogue_text = str(result.info.get("dialogue", ""))
        self._render_dialogue()
        await self._animate_bar(
            self.mood,
            old_mood,
            result.state.mood,
            str(result.info.get("mood_label", "")),
        )
        await self._animate_bar(
            self.security,
            old_security,
            result.state.security,
            str(result.info.get("security_label", "")),
        )
        self._update_mood_class()
        self.options.set_options(result.state.options)
        self.reaction.set_value("")
        self._processing = False
        if self._spinner_timer is not None:
            self._spinner_timer.stop()
            self._spinner_timer = None
        self._status.update("")
        self._render_dialogue()
        if result.done:
            self.app.push_screen(PostGameScreen(self.engine))

    async def _animate_bar(
        self,
        bar: StatusBar,
        start: int,
        end: int,
        delta_label: str,
    ) -> None:
        steps = 10
        for index in range(1, steps + 1):
            value = round(start + ((end - start) * index / steps))
            bar.refresh_bar(value, delta_label if index == steps else "")
            await asyncio.sleep(0.05)

    def _tick_spinner(self) -> None:
        if not self._processing:
            return
        self._spinner_frame = (self._spinner_frame + 1) % 4
        dots = "." * self._spinner_frame
        self._spinner_text = f"Thinking{dots}"
        self._status.update(f"[dim]{self._spinner_text}[/dim]")
        self._render_dialogue()

    def _render_dialogue(self) -> None:
        text = self._dialogue_text
        if self._processing:
            suffix = self._spinner_text or "Thinking"
            text = f"{text}\n\n[dim]{suffix}[/dim]".strip()
        self.dialogue.update(text)


class PostGameScreen(Screen[None]):
    def __init__(self, engine: GameEngine) -> None:
        super().__init__()
        self.engine = engine

    def compose(self) -> ComposeResult:
        state = self.engine.current_state()
        header = "YOU WON" if self.engine.game_result == "win" else "YOU LOST"
        yield Static(
            "\n".join(
                [
                    header,
                    StatusBar("MOOD", state.mood).render_text(),
                    StatusBar("SECURITY", state.security).render_text(),
                    f"Turns played: {len(self.engine.trajectory)}",
                    _best_turn_text(self.engine),
                    "[dim]Play again? [y/n][/dim]",
                ]
            )
        )

    async def on_key(self, event) -> None:
        if event.key == "y":
            self.app.action_restart()
        elif event.key in {"n", "q"}:
            self.app.exit()


class QuitConfirmScreen(ModalScreen[bool]):
    def compose(self) -> ComposeResult:
        yield Static("[dim]Quit game? [y/n][/dim]")

    async def on_key(self, event) -> None:
        if event.key == "y":
            self.dismiss(True)
        elif event.key in {"n", "escape"}:
            self.dismiss(False)


def _best_turn_text(engine: GameEngine) -> str:
    if not engine.trajectory:
        return "No standout turn."
    best = max(engine.trajectory, key=lambda item: item[2])
    prev, action, reward, next_state = best
    if reward <= 0:
        return "No standout turn."
    return (
        f"Best turn: Turn {prev.turn_number + 1} "
        f"({action.reaction_direction}{action.reaction_intensity} reaction, "
        f"mood {prev.mood} -> {next_state.mood})"
    )
