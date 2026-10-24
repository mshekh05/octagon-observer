"""Does the SplitPicks expert consensus add anything on top of the market and the model?

Data: past Kings Tables (ingest.experts_history), Kalshi closing prices for the same
bouts (ingest.kalshi_history), and the stats model scored out of sample (trained only
on fights before the year it predicts).

The engine's own formula is tested: final_prob(model, market, MODEL_WEIGHT, youth)
with and without the expert nudge at several EXPERT_WEIGHT values. The weight is
chosen by leave-one-event-out cross-validation, and uncertainty comes from
resampling whole events (fights on one card share news and judging).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import edge as E
from .market_backtest import walk_forward

WEIGHTS = (0.0, 0.05, 0.10, 0.15, 0.20, 0.30, 0.50)


def _ll(y: np.ndarray, p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


@dataclass
class ExpertBacktest:
    data: pd.DataFrame  # url, event, y, p_model, p_market, share_a, n, age_gap
    losses: dict[float, np.ndarray]  # weight -> per-fight log loss
    cv_loss: np.ndarray  # per-fight loss when the weight is picked without that fight's event
    cv_weights: list[float]
    configured: float

    @property
    def best_weight(self) -> float:
        return min(self.losses, key=lambda w: self.losses[w].mean())

    @property
    def suggested_weight(self) -> float:
        """The best in-sample weight, but 0 unless the cross-validated choice actually beats no experts."""
        return self.best_weight if self.cv_loss.mean() < self.losses[0.0].mean() else 0.0

    def _event_ci(self, diff: np.ndarray, seed: int = 11) -> tuple[float, float]:
        events = self.data["event"].to_numpy()
        names = np.unique(events)
        idx = {e: np.flatnonzero(events == e) for e in names}
        rng = np.random.default_rng(seed)
        boots = []
        for _ in range(2000):
            pick = np.concatenate([idx[e] for e in rng.choice(names, len(names))])
            boots.append(diff[pick].mean())
        return float(np.quantile(boots, 0.05)), float(np.quantile(boots, 0.95))

    def summary(self) -> str:
        d = self.data
        y = d["y"].to_numpy()
        fav_market = d["p_market"].to_numpy() > 0.5
        pick_experts = d["share_a"].to_numpy() > 0.5
        split = d["share_a"].to_numpy() == 0.5
        acc = lambda pick: float(np.mean(pick[~split] == (y[~split] == 1)))  # noqa: E731
        disagree = (pick_experts != fav_market) & ~split
        lines = [
            f"Fights with expert picks and a Kalshi closing price: {len(d)} across {d['event'].nunique()} events "
            f"({d['date'].min().date()} to {d['date'].max().date()}), {int(d['n'].sum())} picks",
            "",
            "Who picks the winner (fights where the experts weren't split evenly):",
            f"  Kalshi favourite : {acc(fav_market):.1%}",
            f"  Expert consensus : {acc(pick_experts):.1%}",
            f"  Stats model      : {acc(d['p_model'].to_numpy() > 0.5):.1%}",
            f"  When experts and Kalshi disagree ({int(disagree.sum())} fights): experts right "
            f"{float(np.mean(pick_experts[disagree] == (y[disagree] == 1))):.1%}" if disagree.any() else "",
            "",
            f"Kalshi closing price alone: log loss {_ll(y, d['p_market'].to_numpy()).mean():.4f}",
            "Engine log loss by EXPERT_WEIGHT (market + model + youth, then the expert nudge):",
        ]
        base = self.losses[0.0]
        for w, loss in self.losses.items():
            diff = loss - base
            lo, hi = self._event_ci(diff) if w else (0.0, 0.0)
            note = "" if not w else f"  vs none {diff.mean() * 1000:+.2f} (90% CI {lo * 1000:+.2f} to {hi * 1000:+.2f})"
            mark = " <- configured" if abs(w - self.configured) < 1e-9 else ""
            lines.append(f"  {w:4.2f}  {loss.mean():.4f}{note}{mark}")
        cv_diff = self.cv_loss - base
        lo, hi = self._event_ci(cv_diff)
        lines += [
            "",
            f"Leave-one-event-out (weight chosen without the held-out event; picked "
            f"{', '.join(f'{w:.2f}' for w in sorted(set(self.cv_weights)))}):",
            f"  log loss {self.cv_loss.mean():.4f} vs no experts {base.mean():.4f}: {cv_diff.mean() * 1000:+.2f} "
            f"thousandths (90% CI {lo * 1000:+.2f} to {hi * 1000:+.2f})",
            "",
            f"Suggested EXPERT_WEIGHT {self.suggested_weight:.2f}",
            "(negative = experts help; thousandths of log loss per fight)",
        ]
        return "\n".join(line for line in lines if line is not None)


def _engine(d: pd.DataFrame, model_weight: float, youth: float, w: float) -> np.ndarray:
    out = []
    for r in d.itertuples():
        ew = E.scaled_weight(w, r.n) if w else 0.0
        out.append(E.final_prob(r.p_model, r.p_market, model_weight, p_experts=r.share_a, expert_weight=ew,
                                age_gap_years=None if np.isnan(r.age_gap) else r.age_gap, youth_per_5y=youth))
    return np.array(out)


def run(rows: pd.DataFrame, experts: pd.DataFrame, kalshi: pd.DataFrame, model_weight: float,
        youth_adj: float, configured: float, weights=WEIGHTS) -> ExpertBacktest:
    first_year = int(pd.Timestamp(rows.merge(experts, on="url")["date"].min()).year)
    p_model = walk_forward(rows, start_year=first_year)
    d = (rows[["url", "date", "a_won", "a_age", "b_age"]].merge(experts, on="url").merge(kalshi, on="url")
         .rename(columns={"a_won": "y", "p_kalshi": "p_market"}))
    d["p_model"] = d["url"].map(p_model)
    d["age_gap"] = d["a_age"] - d["b_age"]
    d = d.dropna(subset=["p_model", "p_market"]).sort_values("date").reset_index(drop=True)
    y = d["y"].to_numpy()
    losses = {w: _ll(y, _engine(d, model_weight, youth_adj, w)) for w in weights}

    events = d["event"].to_numpy()
    cv_loss = np.empty(len(d))
    cv_weights = []
    for e in np.unique(events):
        held = events == e
        w = min(weights, key=lambda w: losses[w][~held].mean())
        cv_loss[held] = losses[w][held]
        cv_weights.append(w)
    return ExpertBacktest(d, losses, cv_loss, cv_weights, configured)
