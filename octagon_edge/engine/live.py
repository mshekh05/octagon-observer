"""In-fight win probability from the round-by-round state.

Trained on historical UFC rounds: given the pre-fight probability and what has
happened so far (rounds won, knockdowns, takedowns, and optionally strikes and
control), how often did fighter A go on to win? Round "winners" in history are
a stats proxy (strikes, knockdowns, takedowns, control), which is also what a
viewer judges when tapping "A won the round".
"""
from __future__ import annotations

from dataclasses import dataclass

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss

from ..config import DATA_DIR
from .edge import logit, sigmoid

LIVE_MODEL_PATH = DATA_DIR / "live_model.joblib"
MINIMAL = ["logit_pre", "rw", "rw_x_frac", "kd", "td"]
FULL = MINIMAL + ["sig10", "ctrl_min"]


def round_score(sig: float, kd: float, td: float, ctrl_sec: float) -> float:
    return sig + 4 * kd + 2 * td + ctrl_sec / 20


@dataclass
class RoundInput:
    winner: int | None  # 0 = A, 1 = B, None = even
    kd_a: int = 0
    kd_b: int = 0
    td_a: int = 0
    td_b: int = 0
    sig_a: int | None = None
    sig_b: int | None = None
    ctrl_a: int = 0
    ctrl_b: int = 0


def state_features(p_pre: float, rounds: list[RoundInput], scheduled: int) -> dict:
    r = len(rounds)
    rw = sum(1 if x.winner == 0 else -1 if x.winner == 1 else 0 for x in rounds)
    has_sig = all(x.sig_a is not None and x.sig_b is not None for x in rounds) and r > 0
    return {
        "logit_pre": logit(p_pre),
        "rw": rw,
        "rw_x_frac": rw * r / scheduled,
        "kd": sum(x.kd_a - x.kd_b for x in rounds),
        "td": sum(x.td_a - x.td_b for x in rounds),
        "sig10": sum((x.sig_a or 0) - (x.sig_b or 0) for x in rounds) / 10 if has_sig else 0.0,
        "ctrl_min": sum(x.ctrl_a - x.ctrl_b for x in rounds) / 60,
        "_has_sig": has_sig,
    }


def build_dataset(fights: pd.DataFrame, rounds: pd.DataFrame, p_pre: dict[str, float]) -> pd.DataFrame:
    """One row per completed round of each decided fight, oriented to fighter A."""
    rs = {(r.event, r.bout, r.fighter, r.round): r for r in rounds.itertuples(index=False)}
    rows = []
    for f in fights.itertuples(index=False):
        if f.result not in ("A", "B") or f.url not in p_pre:
            continue
        sched = int(f.scheduled_rounds)
        last = int(f.duration_sec // 300) + (1 if f.duration_sec % 300 else 0)
        completed = sched if f.method == "DEC" else last - 1
        seq: list[RoundInput] = []
        for r in range(1, completed + 1):
            ra, rb = rs.get((f.event, f.bout, f.fighter_a, r)), rs.get((f.event, f.bout, f.fighter_b, r))
            if ra is None or rb is None:
                break
            sa, sb = round_score(ra.sig_l, ra.kd, ra.td_l, ra.ctrl), round_score(rb.sig_l, rb.kd, rb.td_l, rb.ctrl)
            seq.append(RoundInput(0 if sa > sb else 1 if sb > sa else None, int(ra.kd), int(rb.kd),
                                  int(ra.td_l), int(rb.td_l), int(ra.sig_l), int(rb.sig_l), int(ra.ctrl), int(rb.ctrl)))
            feats = state_features(p_pre[f.url], seq, sched)
            feats.pop("_has_sig")
            rows.append({**feats, "round": r, "date": f.date, "a_won": int(f.result == "A"),
                         "p_pre": p_pre[f.url]})
    return pd.DataFrame(rows)


def _mirror(df: pd.DataFrame, cols: list[str]):
    x = df[cols].to_numpy()
    y = df["a_won"].to_numpy()
    return np.vstack([x, -x]), np.concatenate([y, 1 - y])


def _fit(df: pd.DataFrame, cols: list[str]) -> LogisticRegression:
    x, y = _mirror(df, cols)
    m = LogisticRegression(C=1.0, fit_intercept=False, max_iter=2000)
    m.fit(x, y)
    return m


@dataclass
class LiveModel:
    minimal: LogisticRegression
    full: LogisticRegression

    def predict(self, p_pre: float, rounds: list[RoundInput], scheduled: int) -> float:
        if not rounds:
            return p_pre
        f = state_features(p_pre, rounds, scheduled)
        cols, model = (FULL, self.full) if f["_has_sig"] else (MINIMAL, self.minimal)
        return float(model.predict_proba(np.array([[f[c] for c in cols]]))[:, 1][0])


def train(df: pd.DataFrame, path=LIVE_MODEL_PATH) -> LiveModel:
    lm = LiveModel(_fit(df, MINIMAL), _fit(df, FULL))
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(lm, path)
    return lm


def load(path=LIVE_MODEL_PATH) -> LiveModel:
    return joblib.load(path)


def evaluate(df: pd.DataFrame, split: str = "2023-01-01") -> str:
    train_df, test = df[df["date"] < split], df[df["date"] >= split]
    lm = LiveModel(_fit(train_df, MINIMAL), _fit(train_df, FULL))
    lines = [f"Live model, rounds from fights since {split} (unseen):"]
    for r in sorted(test["round"].unique())[:4]:
        t = test[test["round"] == r]
        y = t["a_won"].to_numpy()
        pre = np.clip(t["p_pre"].to_numpy(), 0.01, 0.99)
        p_min = lm.minimal.predict_proba(t[MINIMAL].to_numpy())[:, 1]
        p_full = lm.full.predict_proba(t[FULL].to_numpy())[:, 1]
        lines.append(f"  after round {r}: n={len(t):4d}  pre-fight {log_loss(y, pre):.3f}  "
                     f"winner+kd+td {log_loss(y, p_min):.3f}  +strikes/control {log_loss(y, p_full):.3f}")
    return "\n".join(lines)


def blend_with_market(p_rounds: float, p_market: float | None, market_weight: float = 0.6) -> float:
    if p_market is None:
        return p_rounds
    return sigmoid(market_weight * logit(p_market) + (1 - market_weight) * logit(p_rounds))
