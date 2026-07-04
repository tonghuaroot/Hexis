"""Custom widgets for the Hexis init wizard TUI.

All widgets accept ``**kwargs`` and forward them to the base widget, so they can
be given ``id``/``classes`` (the old ``CharacterPreview(id=…)`` crash class).
"""
from __future__ import annotations

from typing import Any

from rich.style import Style
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Label, RichLog, Static

from apps.tui.design import COLORS, GLYPHS

# ── Step bar ─────────────────────────────────────────────────────────────────

STEPS = ["Models", "Path", "Setup", "Consent"]


class StepBar(Static):
    """Progress indicator: Models › Path › Setup › Consent."""

    def __init__(self, current: int = 0, **kwargs: Any) -> None:
        super().__init__("", **kwargs)
        self._current = current

    def on_mount(self) -> None:
        self.update(self._build())

    def _build(self) -> Text:
        out = Text(justify="center")
        for i, name in enumerate(STEPS):
            if i:
                out.append(f"  {GLYPHS['chev_closed']}  ", style=Style(color=COLORS["muted"]))
            if i < self._current:
                out.append(name, style=Style(color=COLORS["ok"]))
            elif i == self._current:
                out.append(name, style=Style(color=COLORS["accent"], bold=True))
            else:
                out.append(name, style=Style(color=COLORS["muted"]))
        return out


# ── Big Five sliders ─────────────────────────────────────────────────────────

_TRAIT_NAMES = ["Openness", "Conscientiousness", "Extraversion",
                "Agreeableness", "Neuroticism"]
_TRAIT_KEYS = [t.lower() for t in _TRAIT_NAMES]


class TraitSlider(Static):
    """A focusable 0.0–1.0 slider driven by ←/→ (or h/l)."""

    can_focus = True
    BAR_WIDTH = 22

    def __init__(self, value: float = 0.5, **kwargs: Any) -> None:
        super().__init__("", **kwargs)
        self._value = max(0.0, min(1.0, value))

    def on_mount(self) -> None:
        self._repaint()

    @property
    def value(self) -> float:
        return self._value

    def _repaint(self) -> None:
        filled = int(round(self._value * self.BAR_WIDTH))
        t = Text()
        t.append("█" * filled, style=Style(color=COLORS["accent"]))
        t.append("░" * (self.BAR_WIDTH - filled), style=Style(color=COLORS["muted"]))
        t.append(f"  {self._value:.2f}", style=Style(color=COLORS["dim"]))
        self.update(t)

    def on_key(self, event: Any) -> None:
        if event.key in ("left", "h", "down"):
            self._value = max(0.0, round(self._value - 0.05, 2))
            self._repaint()
            event.prevent_default()
        elif event.key in ("right", "l", "up"):
            self._value = min(1.0, round(self._value + 0.05, 2))
            self._repaint()
            event.prevent_default()


class BigFiveSliders(Static):
    """Five personality-trait sliders (0.0–1.0)."""

    def __init__(self, defaults: dict[str, float] | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._defaults = defaults or {}

    def compose(self) -> ComposeResult:
        for name, key in zip(_TRAIT_NAMES, _TRAIT_KEYS):
            default = self._defaults.get(key, 0.5)
            with Horizontal(classes="big-five-row"):
                yield Label(f"{name}:", classes="big-five-label")
                yield TraitSlider(value=default, id=f"trait-{key}")

    def get_traits(self) -> dict[str, float]:
        result: dict[str, float] = {}
        for key in _TRAIT_KEYS:
            try:
                result[key] = self.query_one(f"#trait-{key}", TraitSlider).value
            except Exception:
                result[key] = 0.5
        return result


# ── Character preview ────────────────────────────────────────────────────────

class CharacterPreview(Static):
    """Right-side panel showing details of a selected character card."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        # markup=False → card text (which may contain brackets) is never parsed.
        yield RichLog(id="preview-log", wrap=True, markup=False)

    def update_preview(self, card: dict[str, Any] | None) -> None:
        log = self.query_one("#preview-log", RichLog)
        log.clear()
        if card is None:
            log.write(Text("Select a character to preview", style=Style(color=COLORS["dim"])))
            return

        from core.init_api import get_card_summary

        summary = get_card_summary(card)
        log.write(Text(summary["name"], style=Style(color=COLORS["accent"], bold=True)))
        log.write("")
        for field in ("voice", "values", "personality"):
            if summary.get(field):
                line = Text()
                line.append(f"{field.capitalize()}: ", style=Style(color=COLORS["teal"]))
                line.append(str(summary[field]), style=Style(color=COLORS["text"]))
                log.write(line)
        if summary.get("description"):
            log.write("")
            log.write(Text(str(summary["description"]), style=Style(color=COLORS["text"])))

        ext = card.get("extensions_hexis", {})
        traits = ext.get("personality_traits", {})
        if traits:
            log.write("")
            log.write(Text("Big Five:", style=Style(color=COLORS["teal"])))
            for trait_name in _TRAIT_NAMES:
                val = traits.get(trait_name.lower(), 0.5)
                filled = int(val * 20)
                bar = "█" * filled + "░" * (20 - filled)
                line = Text(f"  {trait_name:18s} ", style=Style(color=COLORS["dim"]))
                line.append(bar, style=Style(color=COLORS["accent"]))
                line.append(f" {val:.2f}", style=Style(color=COLORS["dim"]))
                log.write(line)
