"""The Odds API (https://the-odds-api.com) - US sportsbook moneylines for MMA.

One call returns every MMA fight listed, and costs (markets x regions) credits.
The free tier is 500 credits per month, so calls go through a budget guard.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from ..config import DATA_DIR
from ..engine.odds_math import american_to_decimal
from .card import Card

log = logging.getLogger(__name__)
URL = "https://api.the-odds-api.com/v4/sports/mma_mixed_martial_arts/odds"
BUDGET_FILE = DATA_DIR / "odds_api_budget.json"


@dataclass
class FightOdds:
    """Per-book decimal prices oriented to the card's (A, B)."""
    books: dict[str, tuple[float, float]] = field(default_factory=dict)
    commence_time: str = ""
    live: bool = False
    totals: dict[str, tuple[float, float, float]] = field(default_factory=dict)  # book -> (rounds, over, under)


class BudgetExceeded(RuntimeError):
    pass


class OddsApiClient:
    def __init__(self, api_key: str, regions: str = "us", budget: int = 150,
                 budget_file: Path = BUDGET_FILE, http: httpx.Client | None = None):
        self.api_key = api_key
        self.regions = regions
        self.budget = budget
        self.budget_file = budget_file
        self.http = http or httpx.Client(timeout=20)
        self.remaining: int | None = None
        self.spent = self._load_spent()

    def _load_spent(self) -> int:
        try:
            return int(json.loads(self.budget_file.read_text())["spent"])
        except (FileNotFoundError, KeyError, ValueError):
            return 0

    def _save_spent(self) -> None:
        self.budget_file.parent.mkdir(parents=True, exist_ok=True)
        self.budget_file.write_text(json.dumps({"spent": self.spent, "remaining": self.remaining}))

    def cost_per_call(self, markets: str = "h2h") -> int:
        return len(self.regions.split(",")) * len(markets.split(","))

    def fetch(self, markets: str = "h2h") -> list[dict]:
        """markets: "h2h", or "h2h,totals" to add total rounds (one more credit per region)."""
        if not self.api_key:
            raise RuntimeError("ODDS_API_KEY not set")
        cost = self.cost_per_call(markets)
        if self.spent + cost > self.budget:
            raise BudgetExceeded(f"Odds API budget {self.budget} reached (spent {self.spent})")
        resp = self.http.get(URL, params={"apiKey": self.api_key, "regions": self.regions,
                                          "markets": markets, "oddsFormat": "american"})
        resp.raise_for_status()
        used_now = resp.headers.get("x-requests-last")
        self.spent += int(used_now) if used_now else cost
        rem = resp.headers.get("x-requests-remaining")
        self.remaining = int(float(rem)) if rem else None
        self._save_spent()
        log.info("Odds API: spent %s this run budget, %s credits left this month",
                 self.spent, self.remaining)
        return resp.json()


def parse(events: list[dict], card: Card, now_iso: str | None = None) -> dict[str, FightOdds]:
    """Map Odds API events onto the card's fights."""
    out: dict[str, FightOdds] = {}
    for ev in events:
        found = card.find(ev.get("home_team", ""), ev.get("away_team", ""))
        if not found:
            continue
        fight, _ = found
        fo = FightOdds(commence_time=ev.get("commence_time", ""))
        if now_iso and fo.commence_time and fo.commence_time <= now_iso:
            fo.live = True
        for book in ev.get("bookmakers", []):
            title = book.get("title") or book.get("key")
            for market in book.get("markets", []):
                if market.get("key") == "totals":
                    ou = {o.get("name", "").lower(): o for o in market.get("outcomes", [])}
                    over, under = ou.get("over"), ou.get("under")
                    if over and under and over.get("point") is not None and over.get("price") and under.get("price"):
                        fo.totals[title] = (float(over["point"]), american_to_decimal(float(over["price"])),
                                            american_to_decimal(float(under["price"])))
                    continue
                if market.get("key") != "h2h":
                    continue
                prices: dict[int, float] = {}
                for outcome in market.get("outcomes", []):
                    side = fight.side_of(outcome.get("name", ""))
                    if side is not None and outcome.get("price"):
                        prices[side] = american_to_decimal(float(outcome["price"]))
                if len(prices) == 2:
                    fo.books[title] = (prices[0], prices[1])
        if fo.books or fo.totals:
            out[fight.key] = fo
    return out
