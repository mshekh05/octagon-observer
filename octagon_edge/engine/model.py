"""Pre-fight win-probability model: logistic regression on A-minus-B features.

Training rows are mirrored (A/B swapped, label flipped) and the model has no
intercept, so p(A beats B) == 1 - p(B beats A) by construction.
"""
from __future__ import annotations

from dataclasses import dataclass

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from ..config import DATA_DIR
from .features import FEATURES, diff_frame

MODEL_PATH = DATA_DIR / "model.joblib"
TRAIN_FROM = pd.Timestamp("2005-01-01")


def _mirror(rows: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray]:
    x = diff_frame(rows)
    y = rows["a_won"].to_numpy()
    return pd.concat([x, -x], ignore_index=True), np.concatenate([y, 1 - y])


def _fit(rows: pd.DataFrame, cols: list[str]):
    x, y = _mirror(rows)
    pipe = make_pipeline(StandardScaler(with_mean=False),
                         LogisticRegression(C=0.3, fit_intercept=False, max_iter=2000))
    pipe.fit(x[cols], y)
    return pipe


@dataclass
class BacktestResult:
    n_test: int
    model: dict
    elo_only: dict
    coefficients: dict

    def summary(self) -> str:
        fmt = lambda d: f"log loss {d['log_loss']:.4f} | Brier {d['brier']:.4f} | accuracy {d['accuracy']:.1%}"  # noqa: E731
        return "\n".join([
            f"Test fights: {self.n_test}",
            f"  Stats model : {fmt(self.model)}",
            f"  Elo only    : {fmt(self.elo_only)}",
            f"  Coin flip   : log loss {np.log(2):.4f} | Brier 0.2500",
        ])


def _metrics(y: np.ndarray, p: np.ndarray) -> dict:
    return {"log_loss": float(log_loss(y, p)), "brier": float(brier_score_loss(y, p)),
            "accuracy": float(accuracy_score(y, p > 0.5))}


def backtest(rows: pd.DataFrame, split: str = "2023-01-01") -> BacktestResult:
    rows = rows[rows["date"] >= TRAIN_FROM]
    train = rows[rows["date"] < split]
    # Only score fights where both fighters have UFC history; debuts are near coin flips.
    test = rows[(rows["date"] >= split) & (rows["a_n_fights"] > 0) & (rows["b_n_fights"] > 0)]
    all_cols = [f"d_{k}" for k in FEATURES]
    full = _fit(train, all_cols)
    elo = _fit(train, ["d_elo"])
    x_test = diff_frame(test)
    y = test["a_won"].to_numpy()
    p_full = full.predict_proba(x_test[all_cols])[:, 1]
    p_elo = elo.predict_proba(x_test[["d_elo"]])[:, 1]
    coefs = dict(zip(all_cols, full[-1].coef_[0].round(3)))
    return BacktestResult(len(test), _metrics(y, p_full), _metrics(y, p_elo), coefs)


def train(rows: pd.DataFrame, path=MODEL_PATH):
    pipe = _fit(rows[rows["date"] >= TRAIN_FROM], [f"d_{k}" for k in FEATURES])
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(pipe, path)
    return pipe


def load(path=MODEL_PATH):
    return joblib.load(path)


def predict(pipe, snap_a: dict, snap_b: dict) -> float:
    row = pd.DataFrame([{**{f"a_{k}": v for k, v in snap_a.items()},
                         **{f"b_{k}": v for k, v in snap_b.items()}}])
    x = diff_frame(row)[[f"d_{k}" for k in FEATURES]]
    return float(pipe.predict_proba(x)[:, 1][0])
