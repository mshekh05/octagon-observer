"""Alert rules with de-duplication. Pure logic: no network, easy to test."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .engine import edge as E
from .engine.odds_math import best_price, consensus_fair_prob, decimal_to_american


@dataclass
class Alert:
    kind: str
    fight: str
    title: str
    body: str
    urgent: bool = False
    key: str = ""
    score: float = 0.0
    # set on value alerts, so they can be recorded as paper picks
    side: int | None = None
    book: str = ""
    decimal: float | None = None
    p: float | None = None
    p_market: float | None = None
    source: str = ""


@dataclass
class FightState:
    p_model: float
    a: str
    b: str
    first_market: float | None = None
    pm_history: list[tuple[float, float]] = field(default_factory=list)  # (ts, p_a)
    # Blend inputs from the latest analysis, so alerts price a fight the same way the app does.
    # model_weight None means "use the engine's configured weight".
    model_weight: float | None = None
    llm_adjust: float = 0.0
    p_experts: float | None = None
    expert_weight: float = 0.0
    threshold_mult: float = 1.0
    age_gap: float | None = None  # A's age minus B's
    youth_adj: float = 0.0

    def apply_blend_inputs(self, inputs: dict | None) -> None:
        if not inputs:
            return
        self.model_weight = inputs.get("model_weight", self.model_weight)
        self.llm_adjust = inputs.get("llm_adjust", 0.0) or 0.0
        self.p_experts = inputs.get("p_experts")
        self.expert_weight = inputs.get("expert_weight", 0.0) or 0.0
        self.threshold_mult = inputs.get("threshold_mult", 1.0) or 1.0
        self.age_gap = inputs.get("age_gap")
        self.youth_adj = inputs.get("youth_adj", 0.0) or 0.0


def _am(dec: float) -> str:
    a = decimal_to_american(dec)
    return f"+{a}" if a > 0 else str(a)


class AlertEngine:
    def __init__(self, bankroll: float, kelly: float, max_pct: float, edge_pre: float,
                 edge_live: float, model_weight: float, cooldown_sec: int = 600,
                 line_move: float = 0.05, swing: float = 0.08, swing_window_sec: int = 300,
                 usable_books: tuple[str, ...] = (), home_book: str = ""):
        self.bankroll, self.kelly, self.max_pct = bankroll, kelly, max_pct
        self.edge_pre, self.edge_live, self.model_weight = edge_pre, edge_live, model_weight
        self.cooldown, self.line_move, self.swing, self.swing_window = cooldown_sec, line_move, swing, swing_window_sec
        self.sent: dict[str, tuple[float, float]] = {}  # key -> (ts, score)
        self.usable_books = tuple(usable_books)
        self.home_book = home_book

    def _value(self, kind, fight, name, p, book, dec, threshold, why, urgent, shrink=1.0) -> Alert | None:
        e = E.edge(p, dec)
        if e < threshold:
            return None
        st = E.stake(p, dec, self.bankroll, self.kelly, self.max_pct, shrink)
        body = (f"{name} {_am(dec)} at {book}\n"
                f"Our prob {p * 100:.1f}% vs price {100 / dec:.1f}% → edge {e * 100:+.1f} pts, EV {E.expected_value(p, dec) * 100:+.1f}%\n"
                f"Stake ≤ {st.amount:,.0f} ({st.fraction * 100:.1f}% bankroll)\n{why}")
        bet_at = E.bet_at_decimal(p, threshold)
        if self.home_book and book != self.home_book and bet_at:
            body += f"\nAt {self.home_book}: worth it at {_am(bet_at)} or better."
        return Alert(kind, fight, f"{'LIVE ' if urgent else ''}VALUE: {name} {_am(dec)}", body, urgent,
                     key=f"{kind}|{fight}|{name}", score=e)

    def evaluate(self, fight: str, st: FightState, books: dict[str, tuple[float, float]],
                 pm_p: float | None, live: bool, targets: dict[str, float], now: float | None = None,
                 fresh_books: bool = True) -> list[Alert]:
        """fresh_books: the book prices were fetched this poll. Live value compares them with the
        prediction market, which updates every few seconds; against minutes-old book prices that
        is a stale line, not a bet, so live value is only checked when the books are fresh."""
        now = now or time.time()
        out: list[Alert] = []
        names = (st.a, st.b)
        if pm_p is not None:
            st.pm_history.append((now, pm_p))
            st.pm_history = [(t, p) for t, p in st.pm_history if now - t <= self.swing_window]
            oldest = st.pm_history[0][1]
            if abs(pm_p - oldest) >= self.swing:
                side = 0 if pm_p > oldest else 1
                out.append(Alert("swing", fight, f"SWING: {names[side]} surging",
                                 f"Prediction market moved {oldest * 100:.0f}% → {pm_p * 100:.0f}% for {st.a} "
                                 f"in {self.swing_window // 60} min. Check live lines.", True,
                                 key=f"swing|{fight}|{side}", score=abs(pm_p - oldest)))

        fair = consensus_fair_prob(books) if books else None
        if fair is not None:
            if st.first_market is None:
                st.first_market = fair
            elif abs(fair - st.first_market) >= self.line_move:
                side = 0 if fair > st.first_market else 1
                out.append(Alert("move", fight, f"LINE MOVE: {names[side]} steamed",
                                 f"{st.a} fair prob {st.first_market * 100:.0f}% → {fair * 100:.0f}% since watch began.",
                                 False, key=f"move|{fight}|{side}", score=abs(fair - st.first_market)))
                st.first_market = fair  # next line-move alert only after a further move

        # the consensus above uses every book; prices to bet come only from books you can use
        shop_books = {k: v for k, v in books.items() if not self.usable_books or k in self.usable_books}
        for side in (0, 1):
            bp = best_price(shop_books, side) if shop_books else None
            if not bp:
                continue
            book, dec = bp
            name = names[side]
            if live:
                if pm_p is None or not fresh_books:
                    continue
                p = pm_p if side == 0 else 1 - pm_p
                a = self._value("live", fight, name, p, book, dec, self.edge_live,
                                "Book price lags the prediction market.", True)
            else:
                weight = self.model_weight if st.model_weight is None else st.model_weight
                blended = E.final_prob(st.p_model, fair, weight, st.llm_adjust, st.p_experts, st.expert_weight,
                                       age_gap_years=st.age_gap, youth_per_5y=st.youth_adj)
                p = blended if side == 0 else 1 - blended
                if weight > 0:
                    why = f"Model {st.p_model * 100 if side == 0 else (1 - st.p_model) * 100:.0f}% blended with market."
                    shrink = E.disagreement_shrink(st.p_model, fair)
                else:  # the model only explains; the price comes from the market and the youth shift
                    why = f"Market {fair * 100 if side == 0 else (1 - fair) * 100:.0f}% after the youth adjustment."
                    shrink = 1.0
                if dec < 2:
                    why += " Favourites tend to shorten toward the close, so this price may not last."
                a = self._value("pre", fight, name, p, book, dec, self.edge_pre * st.threshold_mult, why, False,
                                shrink)
            if a:
                ref = pm_p if live else fair
                a.side, a.book, a.decimal, a.p = side, book, dec, p
                a.p_market = None if ref is None else ref if side == 0 else 1 - ref
                if live:
                    a.source = "market lag"
                else:
                    shop = a.p_market is not None and E.edge(a.p_market, dec) >= self.edge_pre * st.threshold_mult
                    a.source = "line shopping" if shop else "model" if weight > 0 else "youth"
                out.append(a)
            target = targets.get(name)
            if target and dec >= target:
                out.append(Alert("target", fight, f"TARGET HIT: {name} {_am(dec)}",
                                 f"{name} is {_am(dec)} at {book} (your target {_am(target)}).", True,
                                 key=f"target|{fight}|{name}", score=dec))
        return [a for a in out if self._should_send(a, now)]

    def _should_send(self, a: Alert, now: float) -> bool:
        prev = self.sent.get(a.key)
        if prev:
            ts, score = prev
            # Re-alert inside the cooldown only if the opportunity grew materially; live prices and
            # swings move every poll, so those wait out the cooldown.
            grew = a.kind not in ("live", "swing") and a.score >= score + 0.02
            if now - ts < self.cooldown and not grew:
                return False
        self.sent[a.key] = (now, a.score)
        return True
