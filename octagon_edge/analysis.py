"""Combines market prices, the stats model, news and the LLM into per-fight verdicts."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

from .config import Settings
from .engine import edge as E
from .engine import model as M
from .engine.features import History
from .engine.odds_math import american_to_decimal, best_price, consensus_fair_prob, devig_power
from .ingest.card import Card, Fight
from .ingest.experts import ExpertConsensus
from .ingest.news import NewsItem, for_fighter, tone
from .ingest.odds_api import FightOdds
from .ingest.prediction_markets import MarketProb
from .llm.provider import Analyst

log = logging.getLogger(__name__)

SHOWN_STATS = [
    ("elo", "Elo rating", 0), ("sos", "Avg opponent Elo", 0), ("n_fights", "UFC fights", 0),
    ("slpm", "Sig. strikes landed / min", 2), ("slpm_adj", "Landed vs opponents' norm / min", 2),
    ("sapm", "Sig. strikes absorbed / min", 2), ("sapm_adj", "Absorbed vs opponents' norm / min", 2),
    ("str_acc", "Striking accuracy", "%"),
    ("str_def", "Striking defence", "%"), ("td15", "Takedowns / 15 min", 2),
    ("td_acc", "Takedown accuracy", "%"), ("td_def", "Takedown defence", "%"),
    ("sub15", "Sub attempts / 15 min", 2), ("ctrl_pct", "Control time share", "%"),
    ("kd15", "Knockdowns / 15 min", 2), ("age", "Age", 1), ("reach_in", "Reach (in)", 0),
    ("layoff_days", "Days since last fight", 0),
]


@dataclass
class SideVerdict:
    book: str | None = None
    decimal: float | None = None
    edge: float | None = None
    ev: float | None = None
    stake: float = 0.0
    stake_pct: float = 0.0
    market_edge: float | None = None  # edge over the de-vigged consensus alone, no model involved
    source: str = ""  # "line shopping" when the price beats the market consensus by itself, else "model"


@dataclass
class FightAnalysis:
    fight: Fight
    record_a: str
    record_b: str
    snap_a: dict
    snap_b: dict
    p_model: float | None
    p_market: float | None
    market_source: str
    p_final: float | None
    books: dict[str, tuple[float, float]] = field(default_factory=dict)
    prediction_markets: list[MarketProb] = field(default_factory=list)
    sides: tuple[SideVerdict, SideVerdict] = (SideVerdict(), SideVerdict())
    pick: int | None = None
    confidence: str = "normal"
    notes: list[str] = field(default_factory=list)
    news_a: list[NewsItem] = field(default_factory=list)
    news_b: list[NewsItem] = field(default_factory=list)
    tone_a: dict = field(default_factory=dict)
    tone_b: dict = field(default_factory=dict)
    llm: dict | None = None
    p_experts: float | None = None
    experts_source: str = ""
    blend_inputs: dict = field(default_factory=dict)  # what the alert engine needs to re-blend with fresh prices

    def name(self, side: int) -> str:
        return self.fight.a if side == 0 else self.fight.b


def market_prob(fight: Fight, odds: FightOdds | None, pms: list[MarketProb]) -> tuple[float | None, str, dict]:
    """Best available market estimate of P(A wins): sportsbooks > prediction markets > press odds."""
    if odds and odds.books:
        return consensus_fair_prob(odds.books), f"{len(odds.books)} US books (de-vigged median)", odds.books
    if pms:
        return sum(m.p_a for m in pms) / len(pms), " + ".join(m.source for m in pms), {}
    if fight.ref_odds:
        da, db = (american_to_decimal(x) for x in fight.ref_odds)
        return devig_power([da, db])[0], "press reference odds (23 Oct)", {"Reference": (da, db)}
    return None, "no market data", {}


LOW_CONFIDENCE_THRESHOLD_MULT = 1.5


def confidence_gate(fight: Fight, snap_a: dict, snap_b: dict, model_weight: float) -> tuple[list[str], str, float]:
    """(notes, confidence, model weight): the model counts for less when a fighter has no UFC record."""
    notes = []
    weight = model_weight
    confidence = "normal"
    for name, snap in ((fight.a, snap_a), (fight.b, snap_b)):
        if snap["n_fights"] == 0:
            notes.append(f"{name} has no UFC fights in the data - model is guessing from priors.")
            confidence, weight = "low", weight / 2
        elif snap["n_fights"] < 3:
            notes.append(f"{name} has only {snap['n_fights']} UFC fight(s); small sample.")
            confidence = "low" if confidence == "low" else "medium"
    if fight.rounds == 5:
        notes.append("Five-round fight: cardio and late-round output matter more than the 3-round averages show.")
    return notes, confidence, weight


def threshold_mult(confidence: str) -> float:
    return LOW_CONFIDENCE_THRESHOLD_MULT if confidence == "low" else 1.0


def analyze_fight(fight: Fight, hist: History, pipe, on: pd.Timestamp, s: Settings,
                  odds: FightOdds | None = None, pms: list[MarketProb] | None = None,
                  news: list[NewsItem] | None = None, analyst: Analyst | None = None,
                  experts: ExpertConsensus | None = None) -> FightAnalysis:
    """Pre-fight verdict for one fight.

    The market is the anchor: p_final leans mostly on the de-vigged price (MODEL_WEIGHT
    comes from the market backtest). A pick needs an edge over the best real sportsbook
    price; stakes shrink when the stats model and the market disagree.
    """
    pms = pms or []
    news = news or []
    snap_a, snap_b = hist.current(fight.a, on), hist.current(fight.b, on)
    p_model = M.predict(pipe, snap_a, snap_b)
    p_market, source, books = market_prob(fight, odds, pms)
    notes, confidence, weight = confidence_gate(fight, snap_a, snap_b, s.model_weight)
    if p_model is not None and p_market is not None and abs(p_model - p_market) > s.max_model_gap:
        notes.append(f"Stats model and market disagree by {abs(p_model - p_market) * 100:.0f} points - usually "
                     "the model is missing something (injury, level of opposition, pre-UFC record).")
        confidence = "low"

    fa = FightAnalysis(fight, hist.record(fight.a), hist.record(fight.b), snap_a, snap_b,
                       p_model, p_market, source, None, books, pms, confidence=confidence, notes=notes)
    fa.news_a, fa.news_b = for_fighter(fight.a, news), for_fighter(fight.b, news)
    fa.tone_a, fa.tone_b = tone(fight.a, news), tone(fight.b, news)

    if analyst and analyst.enabled:
        fa.llm = analyst.analyze(llm_context(fa))
    adjust = float(fa.llm.get("news_adjustment_a", 0) or 0) if fa.llm else 0.0
    adjust = max(-s.llm_max_adjust, min(s.llm_max_adjust, adjust))
    ew = 0.0
    if experts and experts.p_a is not None and s.expert_weight > 0:
        fa.p_experts, fa.experts_source = experts.p_a, experts.summary
        ew = E.scaled_weight(s.expert_weight, experts.n)
    age_gap = snap_a["age"] - snap_b["age"] if pd.notna(snap_a["age"]) and pd.notna(snap_b["age"]) else None
    fa.blend_inputs = {"model_weight": weight, "llm_adjust": adjust, "p_experts": fa.p_experts,
                       "expert_weight": ew, "threshold_mult": threshold_mult(confidence),
                       "age_gap": age_gap, "youth_adj": s.youth_adj}
    fa.p_final = E.final_prob(p_model, p_market, weight, adjust, fa.p_experts, ew,
                              age_gap_years=age_gap, youth_per_5y=s.youth_adj)

    sides = []
    real_books = {k: v for k, v in books.items() if k != "Reference"}
    threshold = s.edge_prefight * threshold_mult(confidence)
    shrink = E.disagreement_shrink(p_model, p_market)
    for side in (0, 1):
        p = fa.p_final if side == 0 else 1 - fa.p_final
        bp = best_price(real_books or books, side)
        if not bp:
            sides.append(SideVerdict())
            continue
        book, dec = bp
        st = E.stake(p, dec, s.bankroll, s.kelly_fraction, s.max_stake_pct, shrink)
        m_edge = None if p_market is None else E.edge(p_market if side == 0 else 1 - p_market, dec)
        sides.append(SideVerdict(book, dec, E.edge(p, dec), E.expected_value(p, dec), st.amount, st.fraction,
                                 m_edge, "line shopping" if m_edge is not None and m_edge >= threshold else "model"))
    fa.sides = tuple(sides)
    best = max((0, 1), key=lambda i: fa.sides[i].edge if fa.sides[i].edge is not None else -1)
    if fa.sides[best].edge is not None and fa.sides[best].edge >= threshold and real_books:
        fa.pick = best
    return fa


def llm_context(fa: FightAnalysis) -> dict:
    def stats(snap):
        return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in snap.items()}

    return {
        "fight": fa.fight.key, "weightclass": fa.fight.weightclass, "rounds": fa.fight.rounds,
        "title_fight": fa.fight.title,
        "fighter_a": {"name": fa.fight.a, "ufc_record": fa.record_a, "stats": stats(fa.snap_a)},
        "fighter_b": {"name": fa.fight.b, "ufc_record": fa.record_b, "stats": stats(fa.snap_b)},
        "model_p_a": round(fa.p_model, 3) if fa.p_model is not None else None,
        "market_p_a": round(fa.p_market, 3) if fa.p_market is not None else None,
        "market_source": fa.market_source,
        "notes": fa.notes,
        "news_a": [f"[{n.source}] {n.title}" for n in fa.news_a[:8]],
        "news_b": [f"[{n.source}] {n.title}" for n in fa.news_b[:8]],
    }


def analyze_card(card: Card, hist: History, pipe, s: Settings, odds: dict[str, FightOdds],
                 pms: dict[str, list[MarketProb]], news: list[NewsItem],
                 analyst: Analyst | None = None,
                 experts: dict[str, ExpertConsensus] | None = None) -> list[FightAnalysis]:
    on = pd.Timestamp(card.date)
    experts = experts or {}
    return [analyze_fight(f, hist, pipe, on, s, odds.get(f.key), pms.get(f.key, []), news, analyst, experts.get(f.key))
            for f in card.fights]
