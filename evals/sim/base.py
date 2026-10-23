"""What every simulated app is: state that draws itself and reacts to input, and what it
draws on."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from evals.render import RGB, Text
from evals.screens import INK
from jharokha.core.models import Box

if TYPE_CHECKING:
    from evals.sim.desktop import Desktop

Mods = frozenset[str]
NONE: Mods = frozenset()


@dataclass
class Canvas:
    """Filled boxes and text in drawing order, as evals.render.Screen takes them."""

    bg: RGB
    rects: list[tuple[Box, RGB]] = field(default_factory=list)
    texts: list[Text] = field(default_factory=list)

    def rect(self, box: Box, color: RGB) -> None:
        self.rects.append((box, color))

    def text(
        self,
        x: int,
        y: int,
        s: str,
        px: int = 14,
        color: RGB = INK,
        bold: bool = False,
        font: str = "Segoe UI",
    ) -> None:
        if s:
            self.texts.append(Text(x, y, s, font, px, color, bold))


class SimApp:
    """One app of the simulated desktop. id and view are what the built-in definitions
    should identify it as; input arrives in client pixels and key names as jharokha's
    press takes them (enter, esc, pageup, a single character)."""

    id = ""
    bg: RGB = (255, 255, 255)

    def __init__(self, desk: Desktop) -> None:
        self.desk = desk

    @property
    def view(self) -> str:
        return ""

    def draw(self, c: Canvas) -> None:
        raise NotImplementedError

    def items(self) -> dict[str, dict[str, Any]]:
        """What the readers of the regions on screen should return (evals.items)."""
        return {}

    def click(self, x: int, y: int, button: str, double: bool) -> None:
        pass

    def scroll(self, x: int, y: int, ticks: int) -> None:
        pass

    def hover(self, x: int, y: int) -> None:
        """The pointer came to rest at (x, y), with no button."""

    def key(self, key: str, mods: Mods) -> None:
        pass

    def char(self, ch: str) -> None:
        pass

    def paste(self, text: str) -> None:
        """Ctrl+V of text. Apps where a pasted newline is not Enter override this."""
        for ch in text:
            if ch == "\n":
                self.key("enter", NONE)
            else:
                self.char(ch)

    def changed(self) -> None:
        self.desk.changed()

    def event(self, kind: str, detail: str) -> None:
        self.desk.event(self.id, kind, detail)


def fit(s: str, n: int) -> str:
    """s cut to n characters, so drawn text stays inside its pane."""
    return s if len(s) <= n else s[: n - 3].rstrip() + "..."
