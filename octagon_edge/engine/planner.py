"""Turn a card's options into a bet plan for a budget.

Every option is a probability (the engine's: market-first) and, when a book in the
feed offers it, a price. Options are sorted into tiers:

- value: edge >= EDGE_PREFIGHT at a book you can use. Full weight.
- lean: a smaller positive edge (>= LEAN_EDGE) that still has positive expected value.
  Half weight, and leans together get at most half of the budget.
- check: no feed price (props at Fanatics): the plan gives the worst price still
  worth taking, and the stake to use if your book offers it.

The budget is split in proportion to each bet's Kelly fraction, but no bet gets more
than MAX_SHARE of the budget and no fight more than FIGHT_CAP. Nothing is staked on a
bet with negative expected value, so on a quiet card most of the budget stays unspent.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .edge import kelly_fraction

MAX_SHARE = 0.30
FIGHT_CAP = 0.40
LEAN_WEIGHT = 0.5
LEAN_BUDGET_SHARE = 0.5


@dataclass
class Option:
    fight_id: int
    fight: str
    market: str  # moneyline | total_rounds | prop
    outcome: str  # side index, "over 2.5", or a prop outcome code
    label: str
    p: float
    decimal: float | None = None  # best price at a book you can use; None for check-at-your-book props
    book: str | None = None
    bet_at: float | None = None  # worst price still worth taking at your own book
    source: str = ""


@dataclass
class PlanBet:
    option: Option
    tier: str  # value | lean | check
    edge: float | None
    ev: float | None
    stake: float

    def to_dict(self) -> dict:
        return {**asdict(self.option), "tier": self.tier, "edge": self.edge, "ev": self.ev, "stake": self.stake}


@dataclass
class Plan:
    budget: float
    bets: list[PlanBet] = field(default_factory=list)
    checks: list[PlanBet] = field(default_factory=list)

    @property
    def spent(self) -> float:
        return round(sum(b.stake for b in self.bets), 2)

    def to_dict(self) -> dict:
        return {"budget": self.budget, "spent": self.spent, "unspent": round(self.budget - self.spent, 2),
                "bets": [b.to_dict() for b in self.bets], "checks": [c.to_dict() for c in self.checks]}


def _priced(o: Option) -> tuple[float, float, float]:
    edge = o.p - 1 / o.decimal
    return edge, o.p * o.decimal - 1, kelly_fraction(o.p, o.decimal)


def build(options: list[Option], budget: float, threshold: float, lean_edge: float,
          max_share: float = MAX_SHARE, fight_cap: float = FIGHT_CAP) -> Plan:
    plan = Plan(budget)
    # one bet per fight and market (never both sides of the same line): keep the best expected value
    best: dict[tuple[int, str], tuple[Option, float, float, float]] = {}
    for o in options:
        if o.decimal is None or o.decimal <= 1:
            continue
        edge, ev, kelly = _priced(o)
        key = (o.fight_id, o.market)
        if key not in best or ev > best[key][2]:
            best[key] = (o, edge, ev, kelly)
    tiers: dict[str, list[tuple[Option, float, float, float]]] = {"value": [], "lean": []}
    for o, edge, ev, kelly in best.values():
        if ev <= 0 or kelly <= 0:
            continue
        if edge >= threshold:
            tiers["value"].append((o, edge, ev, kelly))
        elif edge >= lean_edge:
            tiers["lean"].append((o, edge, ev, kelly))

    def allocate(group, pot: float, weight: float, cap: float, tier: str) -> None:
        total = sum(k * weight for *_, k in group)
        for o, edge, ev, kelly in sorted(group, key=lambda g: -g[2]):
            stake = min(pot * kelly * weight / total, budget * cap) if total else 0.0
            plan.bets.append(PlanBet(o, tier, edge, ev, stake))

    allocate(tiers["value"], budget, 1.0, max_share, "value")
    left = budget - sum(b.stake for b in plan.bets)
    allocate(tiers["lean"], min(left, budget * LEAN_BUDGET_SHARE), LEAN_WEIGHT, max_share * LEAN_WEIGHT, "lean")

    # no fight takes more than fight_cap of the budget
    for fid in {b.option.fight_id for b in plan.bets}:
        on_fight = [b for b in plan.bets if b.option.fight_id == fid]
        total = sum(b.stake for b in on_fight)
        if total > budget * fight_cap:
            for b in on_fight:
                b.stake *= budget * fight_cap / total
    for b in plan.bets:
        b.stake = round(b.stake)
    plan.bets = [b for b in plan.bets if b.stake >= 1]

    for o in options:
        if o.decimal is None and o.bet_at:
            stake = round(budget * min(max_share * LEAN_WEIGHT, kelly_fraction(o.p, o.bet_at)))
            plan.checks.append(PlanBet(o, "check", None, None, stake))
    return plan  # checks stay in card order
