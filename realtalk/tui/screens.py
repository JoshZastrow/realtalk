"""Textual screens for the Realtalk flow."""

from __future__ import annotations

import asyncio

from textual.app import ComposeResult
from textual.screen import ModalScreen, Screen
from textual.widgets import Static

from realtalk.engine import Action, GameEngine
from realtalk.tui.widgets import DialogueArea, MenuList, OptionPicker, ReactionInput, StatusBar


class SceneScreen(Screen[None]):
    def __init__(self, engine: GameEngine) -> None:
        super().__init__()
        self.engine = engine
        self.menu = MenuList("Choose a scene:", [scene.name for scene in self.engine.available_scenes()])

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
        self.menu = MenuList("Choose a role:", [role.name for role in self.engine.available_roles()])

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
        self._body.update(f"{self.opening}\n\nPress Enter to continue.")

    async def on_key(self, event) -> None:
        if event.key == "enter" and self._ready:
            self.app.push_screen(GameScreen(self.engine))

    def _tick_spinner(self) -> None:
        if self._ready:
            return
        frames = ["|", "/", "-", "\\"]
        self._spinner_frame = (self._spinner_frame + 1) % len(frames)
        spin = frames[self._spinner_frame]
        self._body.update(
            f"{spin} Generating opening scene...\n\n"
            "(This may take up to a minute while the character prepares their opening)"
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
        self._status = Static("")
        state = self.engine.current_state()
        self.mood = StatusBar("MOOD", state.mood, delta_label="")
        self.security = StatusBar("SECURITY", state.security, delta_label="")
        self.options.set_options(state.options)
        self._dialogue_text = state.last_dialogue
        self._render_dialogue()

    def compose(self) -> ComposeResult:
        yield self.dialogue
        yield Static("")
        yield self.mood
        yield self.security
        yield Static("")
        yield self.reaction
        yield Static("")
        yield self.options
        yield self._status

    async def on_key(self, event) -> None:
        if self._processing:
            return
        if event.key in {"1", "2", "3"} and self.reaction.is_valid:
            self._processing = True
            self._spinner_text = ""
            self._status.update("Thinking")
            self._spinner_timer = self.set_interval(0.25, self._tick_spinner)
            action = Action(
                self.reaction.direction or "a",
                self.reaction.intensity or 1,
                int(event.key) - 1,
            )
            asyncio.create_task(self._run_step(action))
        elif event.key in {"a", "r", "A", "R"}:
            # Start or restart reaction with a direction key
            self.reaction.set_value(event.key.lower())
        elif event.key in {"1", "2", "3"} and self.reaction.raw_value in {"a", "r"}:
            # Set intensity only after a direction is already typed
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
        self._status.update(self._spinner_text)
        self._render_dialogue()

    def _render_dialogue(self) -> None:
        text = self._dialogue_text
        if self._processing:
            suffix = self._spinner_text or "Thinking"
            text = f"{text}\n\n{suffix}".strip()
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
                    "Play again? [y/n]",
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
        yield Static("Quit game? [y/n]")

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
