"""Start page "Übersicht": what is to do today, with figures per module (SPEC 10.2).

Core defines the contract; each module contributes a `ModuleOverview` through a provider
function registered in `app/main.py`. Core never imports a module.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from sqlmodel import Session

Tone = Literal["neutral", "info", "ok", "warning", "error", "ai", "brand"]


@dataclass(frozen=True)
class Tile:
    """A figure on the overview; `href` leads to the list filtered to exactly these entries."""

    label: str
    count: int
    href: str
    tone: Tone = "neutral"
    hint: str = ""


@dataclass(frozen=True)
class TodoItem:
    """One concrete task for today (e.g. a post to publish by hand, an item to check)."""

    title: str
    detail: str
    href: str
    tone: Tone = "neutral"
    badge: str = ""


@dataclass(frozen=True)
class ModuleOverview:
    """Contribution of one module to the start page."""

    module: str
    title: str
    href: str
    tiles: list[Tile]
    todos: list[TodoItem] = field(default_factory=list)
    empty_hint: str = ""

    @property
    def open_count(self) -> int:
        """Entries that need a person (tiles with a tone other than neutral/ok/info)."""
        return sum(t.count for t in self.tiles if t.tone in ("warning", "error"))


OverviewProvider = Callable[[Session], ModuleOverview]
