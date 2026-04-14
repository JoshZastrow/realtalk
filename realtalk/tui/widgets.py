"""Reusable Textual widgets for the game UI."""

from __future__ import annotations

from textual.widgets import Static


class StatusBar(Static):
    def __init__(
        self,
        label: str,
        value: int,
        max_value: int = 100,
        delta_label: str = "",
        color: str = "green",
    ) -> None:
        self.label = label
        self.value = value
        self.max_value = max_value
        self.delta_label = delta_label
        self.color = color
        super().__init__(self.render_text())

    def render_text(self) -> str:
        filled = max(0, min(20, round((self.value / self.max_value) * 20)))
        bar = "[" + ("=" * filled).ljust(20) + "]"
        suffix = f" {self.delta_label}" if self.delta_label else ""
        return f"{self.label} {bar} {self.value}{suffix}"

    def refresh_bar(self, value: int, delta_label: str = "") -> None:
        self.value = value
        self.delta_label = delta_label
        self.update(self.render_text())


class DialogueArea(Static):
    def append_text(self, text: str) -> None:
        self.update(f"{self.renderable}{text}")


class OptionPicker(Static):
    def __init__(self, options: tuple[str, ...] | list[str] | None = None) -> None:
        self.options = tuple(options or ())
        super().__init__(self.render_text())

    def set_options(self, options: tuple[str, ...] | list[str]) -> None:
        self.options = tuple(options)
        self.update(self.render_text())

    def render_text(self) -> str:
        if not self.options:
            return ""
        return "\n".join(f"{index + 1}. {option}" for index, option in enumerate(self.options))


class MenuList(Static):
    def __init__(self, title: str, items: tuple[str, ...] | list[str]) -> None:
        self.title = title
        self.items = tuple(items)
        self.selected_index = 0
        super().__init__(self.render_text())

    def move(self, delta: int) -> None:
        if not self.items:
            return
        self.selected_index = (self.selected_index + delta) % len(self.items)
        self.update(self.render_text())

    def set_selected_index(self, index: int) -> None:
        if 0 <= index < len(self.items):
            self.selected_index = index
            self.update(self.render_text())

    def render_text(self) -> str:
        lines = [self.title]
        for index, item in enumerate(self.items):
            prefix = ">" if index == self.selected_index else " "
            lines.append(f"{prefix} {index + 1}. {item}")
        return "\n".join(lines)


class ReactionInput(Static):
    def __init__(self) -> None:
        self.raw_value = ""
        self.direction: str | None = None
        self.intensity: int | None = None
        super().__init__("")

    def set_value(self, raw: str) -> None:
        self.raw_value = raw
        self.direction = None
        self.intensity = None
        if raw != raw.strip() or " " in raw or len(raw) != 2:
            return
        direction = raw[0].lower()
        intensity = raw[1]
        if direction not in {"a", "r"} or intensity not in {"1", "2", "3"}:
            return
        self.direction = direction
        self.intensity = int(intensity)

    @property
    def is_valid(self) -> bool:
        return self.direction is not None and self.intensity is not None
