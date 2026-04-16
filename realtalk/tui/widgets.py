"""Reusable Textual widgets for the game UI."""

from __future__ import annotations

from textual.widgets import Static

# ── Status bar label tables ────────────────────────────────────────────────────

MOOD_LABELS: dict[tuple[int, int], str] = {
    (65, 100): "Warm",
    (35, 64): "Strained",
    (0, 34): "Fragile",
}

SECURITY_LABELS: dict[tuple[int, int], str] = {
    (65, 100): "Grounded",
    (35, 64): "Uneasy",
    (0, 34): "Exposed",
}

MOOD_COLORS: dict[tuple[int, int], str] = {
    (65, 100): "#7da85a",
    (35, 64): "#c4a03a",
    (0, 34): "#9e4a4a",
}


def _label_for(value: int, table: dict[tuple[int, int], str]) -> str:
    for (lo, hi), label in table.items():
        if lo <= value <= hi:
            return label
    return ""


def block_bar(value: int, width: int = 10) -> str:
    """Render a block-character progress bar.  e.g. ████████▒░ for value=84."""
    filled = round(max(0, min(100, value)) / 100 * width)
    bar = "█" * filled
    if filled < width:
        bar += "▒"
        bar += "░" * (width - filled - 1)
    return bar


# ── Widgets ────────────────────────────────────────────────────────────────────


class StatusBar(Static):
    def __init__(
        self,
        label: str,
        value: int,
        max_value: int = 100,
        delta_label: str = "",
        color: str = "green",  # kept for backwards compat; unused after redesign
    ) -> None:
        self.label = label
        self.value = value
        self.max_value = max_value
        self.delta_label = delta_label
        super().__init__(self.render_text())

    @property
    def bar_color(self) -> str:
        for (lo, hi), color in MOOD_COLORS.items():
            if lo <= self.value <= hi:
                return color
        return "#7da85a"

    def render_text(self) -> str:
        label_table = MOOD_LABELS if self.label == "MOOD" else SECURITY_LABELS
        state_label = _label_for(self.value, label_table)
        bar = block_bar(self.value)
        color = self.bar_color
        return f"{self.label}  [{color}]{bar}[/{color}]  {state_label}  {self.value}"

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
        return "\n".join(
            f"[dim]{index + 1}.[/dim]  {option}"
            for index, option in enumerate(self.options)
        )


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
        lines = [f"[bold]{self.title}[/bold]"]
        for i, item in enumerate(self.items):
            if i == self.selected_index:
                lines.append(f"[bold #c4a03a]→ {i + 1}. {item}[/bold #c4a03a]")
            else:
                lines.append(f"[dim]  {i + 1}. {item}[/dim]")
        return "\n".join(lines)


class ReactionInput(Static):
    def __init__(self) -> None:
        self.raw_value = ""
        self.direction: str | None = None
        self.intensity: int | None = None
        super().__init__(self._render_text())

    def set_value(self, raw: str) -> None:
        self.raw_value = raw
        self.direction = None
        self.intensity = None
        if raw != raw.strip() or " " in raw or len(raw) != 2:
            self.update(self._render_text())
            return
        direction = raw[0].lower()
        intensity = raw[1]
        if direction not in {"a", "r"} or intensity not in {"1", "2", "3"}:
            self.update(self._render_text())
            return
        self.direction = direction
        self.intensity = int(intensity)
        self.update(self._render_text())

    def _render_text(self) -> str:
        if not self.raw_value:
            return "[dim]Reaction: type a/r + 1-3  (a2=attract medium, r1=repel light)[/dim]"
        if self.is_valid:
            direction_label = "attract" if self.direction == "a" else "repel"
            return (
                f"Reaction: [bold #c4a03a]{self.raw_value}[/bold #c4a03a]"
                f"  ({direction_label}, intensity {self.intensity})"
                f" [dim]— press 1/2/3 to respond[/dim]"
            )
        return f"[dim]Reaction: {self.raw_value}_  (a=attract, r=repel, then 1-3)[/dim]"

    @property
    def is_valid(self) -> bool:
        return self.direction is not None and self.intensity is not None
