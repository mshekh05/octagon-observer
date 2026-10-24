"""Does the stats model add anything to the betting market? Walk-forward backtest against real prices.

For every year from `start_year`, the stats model is trained only on fights
before that year and scored on that year's fights, so every prediction is
out-of-sample. The market side is the de-vigged historical moneyline.

Reports:
- log loss / Brier / accuracy for the model, the market, and blends
- the blend weight the data supports (logistic regression of the result on
  logit(model) and logit(market)), fitted on early years and checked on later ones
- simulated flat-stake ROI of the "edge >= threshold" bet rule, with a bootstrap
  interval, at the historical price (one price per fighter, so no line shopping)
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss

from . import model as M
from .edge import logit, sigmoid, youth_adjust
from .features import FEATURES, diff_frame


def walk_forward(rows: pd.DataFrame, start_year: int = 2014, cols: list[str] | None = None,
                 fit=None) -> pd.Series:
    """Out-of-sample P(A wins) for every row from `start_year`, keyed by url."""
    cols = cols or [f"d_{k}" for k in FEATURES]
    fit = fit or M._fit
    rows = rows[rows["date"] >= M.TRAIN_FROM]
    out = []
    for year in range(start_year, rows["date"].dt.year.max() + 1):
        train = rows[rows["date"] < f"{year}-01-01"]
        test = rows[rows["date"].dt.year == year]
        if test.empty:
            continue
        pipe = fit(train, cols)
        x = diff_frame(test, [c.removeprefix("d_") for c in cols])[cols]
        out.append(pd.Series(pipe.predict_proba(x)[:, 1], index=test["url"].to_numpy()))
    return pd.concat(out)


def _mirror_fit(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """No-intercept logistic fit on mirrored rows, so the result stays symmetric in A/B."""
    lr = LogisticRegression(fit_intercept=False, C=1e6, max_iter=1000)
    lr.fit(np.vstack([x, -x]), np.concatenate([y, 1 - y]))
    return lr.coef_[0]


def blend_coefs(p_model: np.ndarray, p_market: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """(a, b) in sigmoid(a*logit(model) + b*logit(market)) that best predicts results."""
    x = np.column_stack([np.vectorize(logit)(p_model), np.vectorize(logit)(p_market)])
    a, b = _mirror_fit(x, y)
    return float(a), float(b)


def apply_coefs(p_model: np.ndarray, p_market: np.ndarray, a: float, b: float) -> np.ndarray:
    return np.vectorize(sigmoid)(a * np.vectorize(logit)(p_model) + b * np.vectorize(logit)(p_market))


def blend_at(p_model: np.ndarray, p_market: np.ndarray, w: float) -> np.ndarray:
    return apply_coefs(p_model, p_market, w, 1 - w)


def metrics(y: np.ndarray, p: np.ndarray) -> dict:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return {"log_loss": float(log_loss(y, p, labels=[0, 1])), "brier": float(brier_score_loss(y, p)),
            "accuracy": float(np.mean((p > 0.5) == y))}


@dataclass
class BetSim:
    rule: str
    n_bets: int
    hit_rate: float
    roi: float
    roi_lo: float
    roi_hi: float

    def line(self) -> str:
        if not self.n_bets:
            return f"  {self.rule:34s} no bets"
        return (f"  {self.rule:34s} bets {self.n_bets:4d}  won {self.hit_rate:5.1%}  "
                f"ROI {self.roi:+6.1%}  (90% CI {self.roi_lo:+.1%} to {self.roi_hi:+.1%})")


def simulate(p: np.ndarray, dec_a: np.ndarray, dec_b: np.ndarray, y: np.ndarray, rule: str,
             threshold: float = 0.04, by: str = "edge", max_gap: float | None = None,
             p_market: np.ndarray | None = None, seed: int = 7) -> BetSim:
    """Flat 1-unit bets on the better side when it clears `threshold`.

    by="edge": p - 1/decimal (probability points); by="ev": p*decimal - 1.
    max_gap skips fights where |p - p_market| exceeds it (a sign the model is missing something).
    """
    profits = []
    for i in range(len(p)):
        if max_gap is not None and p_market is not None and abs(p[i] - p_market[i]) > max_gap:
            continue
        sides = ((p[i], dec_a[i], y[i] == 1), (1 - p[i], dec_b[i], y[i] == 0))
        scores = [(q - 1 / d) if by == "edge" else (q * d - 1) for q, d, _ in sides]
        k = int(np.argmax(scores))
        if scores[k] < threshold:
            continue
        _, d, won = sides[k]
        profits.append(d - 1 if won else -1.0)
    if not profits:
        return BetSim(rule, 0, 0.0, 0.0, 0.0, 0.0)
    arr = np.array(profits)
    rng = np.random.default_rng(seed)
    boots = rng.choice(arr, size=(2000, len(arr)), replace=True).mean(axis=1)
    return BetSim(rule, len(arr), float(np.mean(arr > 0)), float(arr.mean()),
                  float(np.quantile(boots, 0.05)), float(np.quantile(boots, 0.95)))


@dataclass
class MarketBacktest:
    data: pd.DataFrame  # url, date, y, p_model, p_market, dec_a, dec_b, extras
    coefs_all: tuple[float, float]
    coefs_early: tuple[float, float]
    holdout_from: str
    configured_weight: float
    youth_adj: float = 0.0

    def _with_youth(self, p: np.ndarray, mask: np.ndarray) -> np.ndarray:
        gap = (self.data["a_age"] - self.data["b_age"]).to_numpy()[mask]
        return np.array([youth_adjust(q, None if np.isnan(g) else g, self.youth_adj) for q, g in zip(p, gap)])

    @property
    def suggested_weight(self) -> float:
        """Model share of the fitted blend, as MODEL_WEIGHT; 0 when the model adds nothing."""
        a, b = self.coefs_all
        return float(min(max(a / (a + b), 0.0), 1.0)) if a + b > 0 else 0.0

    def summary(self, thresholds=(0.02, 0.04, 0.06)) -> str:
        d = self.data
        y = d["y"].to_numpy()
        pm, pk = d["p_model"].to_numpy(), d["p_market"].to_numpy()
        hold = (d["date"] >= self.holdout_from).to_numpy()
        a, b = self.coefs_early
        fmt = lambda m: f"log loss {m['log_loss']:.4f} | Brier {m['brier']:.4f} | accuracy {m['accuracy']:.1%}"  # noqa: E731
        lines = [f"Fights with a historical price: {len(d)} ({d['date'].min().date()} to {d['date'].max().date()})",
                 "", "All years (model always out-of-sample):",
                 f"  Stats model     : {fmt(metrics(y, pm))}",
                 f"  Market (de-vig) : {fmt(metrics(y, pk))}",
                 f"  Blend @ {self.configured_weight:.2f}    : {fmt(metrics(y, blend_at(pm, pk, self.configured_weight)))}",
                 "", f"Blend fitted on fights before {self.holdout_from}: model x{a:.2f}, market x{b:.2f}",
                 f"Holdout ({hold.sum()} fights from {self.holdout_from}):",
                 f"  Market alone    : {fmt(metrics(y[hold], pk[hold]))}",
                 f"  Fitted blend    : {fmt(metrics(y[hold], apply_coefs(pm[hold], pk[hold], a, b)))}",
                 f"  Blend @ {self.configured_weight:.2f}    : {fmt(metrics(y[hold], blend_at(pm[hold], pk[hold], self.configured_weight)))}",
                 f"  ... + youth {self.youth_adj:.2f}: {fmt(metrics(y[hold], self._with_youth(blend_at(pm[hold], pk[hold], self.configured_weight), hold)))}",
                 "", f"Fitted on all years: model x{self.coefs_all[0]:.2f}, market x{self.coefs_all[1]:.2f}"
                 f"  -> suggested MODEL_WEIGHT {self.suggested_weight:.2f}",
                 "", f"Simulated flat bets at the historical price (holdout years, youth {self.youth_adj:.2f}):"]
        da, db = d["dec_a"].to_numpy()[hold], d["dec_b"].to_numpy()[hold]
        for w in sorted({self.configured_weight, round(self.suggested_weight, 2)}):
            p = self._with_youth(blend_at(pm[hold], pk[hold], w), hold)  # as the engine prices it
            for t in thresholds:
                lines.append(simulate(p, da, db, y[hold], f"weight {w:.2f}, edge >= {t:.0%}", t).line())
            lines.append(simulate(p, da, db, y[hold], f"weight {w:.2f}, EV >= 8%", 0.08, by="ev").line())
            lines.append(simulate(p, da, db, y[hold], f"weight {w:.2f}, edge >= 4%, gap <= 10", 0.04,
                                  max_gap=0.10, p_market=pk[hold]).line())
        return "\n".join(lines)


def run(rows: pd.DataFrame, matched: pd.DataFrame, configured_weight: float, start_year: int = 2014,
        holdout_from: str = "2021-01-01", cols: list[str] | None = None, fit=None,
        youth_adj: float = 0.0) -> MarketBacktest:
    p_model = walk_forward(rows, start_year, cols, fit)
    base = rows[["url", "date", "a_won", "a_age", "b_age", "a_n_fights", "b_n_fights", "weightclass"]]
    d = base.merge(matched, on="url").rename(columns={"a_won": "y"})
    d = d[d["url"].isin(p_model.index)].copy()
    d["p_model"] = d["url"].map(p_model)
    d = d.dropna(subset=["p_model", "p_market"]).sort_values("date").reset_index(drop=True)
    y = d["y"].to_numpy()
    early = (d["date"] < holdout_from).to_numpy()
    coefs_early = blend_coefs(d["p_model"].to_numpy()[early], d["p_market"].to_numpy()[early], y[early])
    coefs_all = blend_coefs(d["p_model"].to_numpy(), d["p_market"].to_numpy(), y)
    return MarketBacktest(d, coefs_all, coefs_early, holdout_from, configured_weight, youth_adj)
