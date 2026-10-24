"""Fight-card loading. Cards live as JSON in octagon_edge/data/cards/."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..config import CARDS_DIR
from .names import same_fighter


@dataclass
class Fight:
    a: str
    b: str
    weightclass: str
    segment: str = "main"
    title: bool = False
    rounds: int = 3
    ref_odds: tuple[int, int] | None = None

    @property
    def key(self) -> str:
        return f"{self.a} vs {self.b}"

    def side_of(self, name: str) -> int | None:
        """0 if `name` is fighter A, 1 if B, None if neither."""
        if same_fighter(self.a, name):
            return 0
        if same_fighter(self.b, name):
            return 1
        return None


@dataclass
class Card:
    event: str
    date: str
    fights: list[Fight] = field(default_factory=list)
    venue: str = ""
    source_note: str = ""

    def find(self, name_1: str, name_2: str) -> tuple[Fight, bool] | None:
        """Fight featuring both names; bool is True when name_1 is fighter A."""
        for f in self.fights:
            s1, s2 = f.side_of(name_1), f.side_of(name_2)
            if s1 is not None and s2 is not None and s1 != s2:
                return f, s1 == 0
        return None


def load_card(slug_or_path: str) -> Card:
    path = Path(slug_or_path)
    if not path.exists():
        path = CARDS_DIR / f"{slug_or_path}.json"
    raw = json.loads(path.read_text())
    fights = [Fight(a=f["a"], b=f["b"], weightclass=f.get("weightclass", ""),
                    segment=f.get("segment", "main"), title=f.get("title", False),
                    rounds=f.get("rounds", 3),
                    ref_odds=tuple(f["ref_odds"]) if f.get("ref_odds") else None)
              for f in raw["fights"]]
    return Card(event=raw["event"], date=raw["date"], fights=fights,
                venue=raw.get("venue", ""), source_note=raw.get("source_note", ""))
