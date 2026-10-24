"""How a fight ends: method of victory, goes the distance, and the round of a finish.

P(fighter wins) comes from the engine's moneyline price. On top of it:
- P(method | this fighter wins): multinomial logistic regression on the winner's
  finishing stats against the loser's durability, with weight-class base rates.
- P(round | finish, method, scheduled rounds): smoothed historical shares.

So P(A by KO) = P(A wins) * P(KO | A wins), P(goes the distance) = sum over both
fighters of P(wins) * P(decision | wins), and P(A wins in round r) adds KO and
submission finishes in that round. Trained only on UFC fights since 2005.
"""
from __future__ import annotations

from dataclasses import dataclass

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from ..config import DATA_DIR
from .model import TRAIN_FROM

PROPS_MODEL_PATH = DATA_DIR / "props_model-v1.joblib"
METHODS = ("KO", "SUB", "DEC")
FEATS = ["w_ko", "w_sub", "w_kd15", "w_sub15", "w_td15", "w_slpm", "l_fin", "l_sapm", "elo_gap", "five", "wc_ko", "wc_sub"]


def _features(w: dict, l: dict, five: bool, wc: tuple[float, float]) -> dict:
    """Winner-oriented features: how the winner wins vs how the loser loses."""
    return {"w_ko": w["ko_win_rate"], "w_sub": w["sub_win_rate"], "w_kd15": w["kd15"], "w_sub15": w["sub15"],
            "w_td15": w["td15"], "w_slpm": w["slpm"], "l_fin": l["finished_rate"], "l_sapm": l["sapm"],
            "elo_gap": w["elo"] - l["elo"], "five": float(five), "wc_ko": wc[0], "wc_sub": wc[1]}


def _decided(rows: pd.DataFrame, fights: pd.DataFrame) -> pd.DataFrame:
    f = fights[["url", "method", "duration_sec", "scheduled_rounds"]]
    d = rows[rows["date"] >= TRAIN_FROM].merge(f, on="url")
    d = d[d["method"].isin(METHODS)].copy()
    d["round"] = np.ceil(d["duration_sec"] / 300).clip(lower=1).astype(int)
    return d.reset_index(drop=True)


def _snap(row, side: str) -> dict:
    keys = ("ko_win_rate", "sub_win_rate", "kd15", "sub15", "td15", "slpm", "finished_rate", "sapm", "elo")
    return {k: getattr(row, f"{side}_{k}") for k in keys}


def _wc_rates(d: pd.DataFrame) -> dict[str, tuple[float, float]]:
    """Weight class -> (KO share, submission share) of decided fights, shrunk to the overall shares."""
    overall = d["method"].value_counts(normalize=True)
    out = {}
    for wc, g in d.groupby("weightclass"):
        n = len(g)
        out[wc] = tuple(float((g["method"].eq(m).sum() + 50 * overall.get(m, 0)) / (n + 50)) for m in ("KO", "SUB"))
    out[""] = (float(overall.get("KO", 0)), float(overall.get("SUB", 0)))
    return out


def _matrix(d: pd.DataFrame, wc_rates: dict) -> tuple[pd.DataFrame, np.ndarray]:
    rows = []
    for r in d.itertuples(index=False):
        w, l = ("a", "b") if r.a_won == 1 else ("b", "a")
        rows.append(_features(_snap(r, w), _snap(r, l), r.scheduled_rounds == 5,
                              wc_rates.get(r.weightclass, wc_rates[""])))
    return pd.DataFrame(rows, columns=FEATS).fillna(0.0), d["method"].to_numpy()


def _round_shares(d: pd.DataFrame) -> dict[tuple[str, int], list[float]]:
    out = {}
    for m in ("KO", "SUB"):
        for sched in (3, 5):
            g = d[(d["method"] == m) & (d["scheduled_rounds"] == sched)]
            counts = np.array([(g["round"] == r).sum() for r in range(1, sched + 1)], dtype=float) + 1.0
            out[(m, sched)] = list(counts / counts.sum())
    return out


@dataclass
class PropModel:
    clf: object
    wc_rates: dict
    rounds: dict

    def method_given_win(self, w: dict, l: dict, five: bool, weightclass: str) -> dict[str, float]:
        x = pd.DataFrame([_features(w, l, five, self.wc_rates.get(weightclass, self.wc_rates[""]))], columns=FEATS).fillna(0.0)
        probs = self.clf.predict_proba(x)[0]
        return dict(zip(self.clf.classes_, (float(p) for p in probs)))

    def predict(self, snap_a: dict, snap_b: dict, p_a: float, weightclass: str, rounds: int) -> dict:
        five = rounds == 5
        out: dict = {}
        for side, (w, l), p_win in (("a", (snap_a, snap_b), p_a), ("b", (snap_b, snap_a), 1 - p_a)):
            for m, p in self.method_given_win(w, l, five, weightclass).items():
                out[f"{side}_{m.lower()}"] = p_win * p
        out["distance"] = out["a_dec"] + out["b_dec"]
        sched = 5 if five else 3
        ends = [0.0] * sched
        for side in ("a", "b"):
            wins = [0.0] * sched
            for m in ("KO", "SUB"):
                for r, share in enumerate(self.rounds[(m, sched)]):
                    wins[r] += out[f"{side}_{m.lower()}"] * share
            out[f"{side}_round"] = wins
            ends = [e + x for e, x in zip(ends, wins)]
        out["ends_in_round"] = ends  # P(the fight is finished in round r), r = 1..scheduled
        out["round_shares"] = {m: self.rounds[(m, sched)] for m in ("KO", "SUB")}
        return out


def combine(kalshi: dict | None, model: dict | None, rounds: int) -> dict:
    """The engine's prop prices: Kalshi where it has a market, the model where it doesn't.

    Against Kalshi's closing "goes the distance" price on 371 fights in 2026, the model
    scored log loss 0.673 vs Kalshi's 0.543, and every blend made Kalshi worse, so the
    market wins outright. Kalshi's round markets barely trade, so the round of a finish
    uses the model's historical round shares applied to Kalshi's KO/submission chances.
    Returns outcome -> (probability, source).
    """
    out: dict[str, tuple[float, str]] = {}
    km = (kalshi or {}).get("method")
    if km:
        out.update({k: (v, "Kalshi") for k, v in km.items()})
    elif model:
        out.update({k: (model[k], "model") for k in ("a_ko", "a_sub", "a_dec", "b_ko", "b_sub", "b_dec")})
    if (kalshi or {}).get("distance") is not None:
        out["distance"] = (kalshi["distance"], "Kalshi")
    elif km:
        out["distance"] = (km.get("a_dec", 0) + km.get("b_dec", 0), "Kalshi")
    elif model:
        out["distance"] = (model["distance"], "model")
    shares = (model or {}).get("round_shares")
    if shares and all(k in out for k in ("a_ko", "a_sub", "b_ko", "b_sub")):
        src = "Kalshi + model rounds" if km else "model"
        for side in ("a", "b"):
            for r in range(rounds):
                p = out[f"{side}_ko"][0] * shares["KO"][r] + out[f"{side}_sub"][0] * shares["SUB"][r]
                out[f"{side}_r{r + 1}"] = (p, src)
        for r in range(rounds):
            out[f"ends_r{r + 1}"] = (out[f"a_r{r + 1}"][0] + out[f"b_r{r + 1}"][0], src)
    return out


def _fit(d: pd.DataFrame):
    wc = _wc_rates(d)
    x, y = _matrix(d, wc)
    clf = make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=2000))
    clf.fit(x, y)
    return PropModel(clf, wc, _round_shares(d))


def train(rows: pd.DataFrame, fights: pd.DataFrame, path=PROPS_MODEL_PATH) -> PropModel:
    pm = _fit(_decided(rows, fights))
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(pm, path)
    return pm


def load(path=PROPS_MODEL_PATH) -> PropModel:
    return joblib.load(path)


def evaluate(rows: pd.DataFrame, fights: pd.DataFrame, split: str = "2023-01-01") -> str:
    """Method given the winner, on fights the model never saw, against weight-class base rates."""
    d = _decided(rows, fights)
    train_d, test = d[d["date"] < split], d[d["date"] >= split]
    pm = _fit(train_d)
    x, y = _matrix(test, pm.wc_rates)
    p = pm.clf.predict_proba(x)
    # sklearn orders classes alphabetically (DEC, KO, SUB); every probability column follows that order
    labels = list(pm.clf.classes_)
    ko_sub = np.array([pm.wc_rates.get(wc, pm.wc_rates[""]) for wc in test["weightclass"]])
    by_label = {"KO": ko_sub[:, 0], "SUB": ko_sub[:, 1], "DEC": 1 - ko_sub.sum(axis=1)}
    base = np.column_stack([by_label[c] for c in labels])
    ll_model = log_loss(y, p, labels=labels)
    ll_base = log_loss(y, base, labels=labels)
    acc = float(np.mean(np.array(labels)[p.argmax(axis=1)] == y))
    shares = pd.Series(y).value_counts(normalize=True).reindex(METHODS).fillna(0)
    return "\n".join([
        f"Method of victory, given the winner: {len(test)} fights since {split} (unseen)",
        f"  Prop model          : log loss {ll_model:.4f} | picks the method {acc:.1%}",
        f"  Weight-class rates  : log loss {ll_base:.4f}",
        f"  Actual mix          : KO {shares['KO']:.0%} · SUB {shares['SUB']:.0%} · DEC {shares['DEC']:.0%}",
    ])
