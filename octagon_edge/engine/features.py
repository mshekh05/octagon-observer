"""Point-in-time fighter features built by replaying UFC history in date order.

Every snapshot only uses fights *before* the bout it describes, so training
rows carry no information from the future.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# League-average priors used to shrink small samples (per minute / ratios).
PRIOR_MIN = 15.0  # pseudo-minutes of average performance mixed into every fighter
PRIOR = {
    "slpm": 3.6, "sapm": 3.6, "str_acc": 0.46, "str_def": 0.54,
    "td15": 1.3, "td_acc": 0.36, "td_def": 0.64, "sub15": 0.4, "kd15": 0.2, "ctrl_pct": 0.15,
}
ELO_START = 1500.0

FEATURES = [
    "elo", "n_fights", "win_rate", "streak", "last3", "slpm", "sapm", "str_acc", "str_def",
    "td15", "td_acc", "td_def", "sub15", "kd15", "ctrl_pct", "ko_win_rate", "sub_win_rate",
    "finished_rate", "layoff_days", "age", "reach_in", "height_in",
]


@dataclass
class Career:
    fights: int = 0
    wins: int = 0
    losses: int = 0
    ko_wins: int = 0
    sub_wins: int = 0
    finished: int = 0
    stat_sec: float = 0.0
    sig_l: float = 0.0
    sig_a: float = 0.0
    opp_sig_l: float = 0.0
    opp_sig_a: float = 0.0
    td_l: float = 0.0
    td_a: float = 0.0
    opp_td_l: float = 0.0
    opp_td_a: float = 0.0
    sub_att: float = 0.0
    kd: float = 0.0
    ctrl: float = 0.0
    elo: float = ELO_START
    streak: int = 0
    recent: list[float] = field(default_factory=list)
    last_date: pd.Timestamp | None = None
    history: list[dict] = field(default_factory=list)


def _shrink(num: float, den: float, prior: float, prior_den: float) -> float:
    return (num + prior * prior_den) / (den + prior_den)


def snapshot(c: Career, bio: dict, on: pd.Timestamp) -> dict:
    mins = c.stat_sec / 60
    per15 = mins / 15
    p15 = PRIOR_MIN / 15
    snap = {
        "elo": c.elo,
        "n_fights": c.fights,
        "win_rate": (c.wins + 1) / (c.fights + 2),
        "streak": c.streak,
        "last3": float(np.mean(c.recent[-3:])) if c.recent else 0.5,
        "slpm": _shrink(c.sig_l, mins, PRIOR["slpm"], PRIOR_MIN),
        "sapm": _shrink(c.opp_sig_l, mins, PRIOR["sapm"], PRIOR_MIN),
        "str_acc": _shrink(c.sig_l, c.sig_a, PRIOR["str_acc"], 40),
        "str_def": 1 - _shrink(c.opp_sig_l, c.opp_sig_a, 1 - PRIOR["str_def"], 40),
        "td15": _shrink(c.td_l, per15, PRIOR["td15"], p15),
        "td_acc": _shrink(c.td_l, c.td_a, PRIOR["td_acc"], 5),
        "td_def": 1 - _shrink(c.opp_td_l, c.opp_td_a, 1 - PRIOR["td_def"], 5),
        "sub15": _shrink(c.sub_att, per15, PRIOR["sub15"], p15),
        "kd15": _shrink(c.kd, per15, PRIOR["kd15"], p15),
        "ctrl_pct": _shrink(c.ctrl, c.stat_sec, PRIOR["ctrl_pct"], PRIOR_MIN * 60),
        "ko_win_rate": (c.ko_wins + 0.5) / (c.fights + 2),
        "sub_win_rate": (c.sub_wins + 0.3) / (c.fights + 2),
        "finished_rate": (c.finished + 0.3) / (c.fights + 2),
        "layoff_days": (on - c.last_date).days if c.last_date is not None else np.nan,
        "age": (on - bio["dob"]).days / 365.25 if pd.notna(bio.get("dob")) else np.nan,
        "reach_in": bio.get("reach_in", np.nan),
        "height_in": bio.get("height_in", np.nan),
    }
    return snap


def _elo_update(a: Career, b: Career, score_a: float, method: str) -> None:
    exp_a = 1 / (1 + 10 ** ((b.elo - a.elo) / 400))
    k = lambda c: 40.0 if c.fights < 5 else 28.0  # noqa: E731
    mult = 1.15 if method in ("KO", "SUB") else 1.0
    a.elo += k(a) * mult * (score_a - exp_a)
    b.elo += k(b) * mult * ((1 - score_a) - (1 - exp_a))


def _add_stats(c: Career, mine: dict | None, theirs: dict | None, secs: float) -> None:
    if mine is None or theirs is None:
        return
    c.stat_sec += secs
    c.sig_l += mine["sig_l"]
    c.sig_a += mine["sig_a"]
    c.opp_sig_l += theirs["sig_l"]
    c.opp_sig_a += theirs["sig_a"]
    c.td_l += mine["td_l"]
    c.td_a += mine["td_a"]
    c.opp_td_l += theirs["td_l"]
    c.opp_td_a += theirs["td_a"]
    c.sub_att += mine["sub_att"]
    c.kd += mine["kd"]
    c.ctrl += mine["ctrl"]


def _record_result(c: Career, won: bool | None, method: str, date: pd.Timestamp, opp: str) -> None:
    c.fights += 1
    if won is None:
        c.recent.append(0.5)
        c.streak = 0
    elif won:
        c.wins += 1
        c.ko_wins += method == "KO"
        c.sub_wins += method == "SUB"
        c.streak = c.streak + 1 if c.streak > 0 else 1
        c.recent.append(1.0)
    else:
        c.losses += 1
        c.finished += method in ("KO", "SUB")
        c.streak = c.streak - 1 if c.streak < 0 else -1
        c.recent.append(0.0)
    c.last_date = date
    c.history.append({"date": date, "opponent": opp, "won": won, "method": method})


class History:
    """Replays all fights; exposes training rows and current careers."""

    def __init__(self, fights: pd.DataFrame, totals: pd.DataFrame, fighters: pd.DataFrame):
        self.fighters = fighters
        self.careers: dict[str, Career] = {}
        tot = {(r.event, r.bout, r.fighter): r for r in totals.itertuples(index=False)}
        rows = []
        for f in fights.itertuples(index=False):
            ca = self.careers.setdefault(f.fighter_a, Career())
            cb = self.careers.setdefault(f.fighter_b, Career())
            sa = snapshot(ca, self.bio(f.fighter_a), f.date)
            sb = snapshot(cb, self.bio(f.fighter_b), f.date)
            if f.result in ("A", "B"):
                rows.append({"url": f.url, "date": f.date, "fighter_a": f.fighter_a, "fighter_b": f.fighter_b,
                             "a_won": int(f.result == "A"), "weightclass": f.weightclass,
                             **{f"a_{k}": v for k, v in sa.items()},
                             **{f"b_{k}": v for k, v in sb.items()}})
            # update careers after the snapshot
            ta = tot.get((f.event, f.bout, f.fighter_a))
            tb = tot.get((f.event, f.bout, f.fighter_b))
            ta = ta._asdict() if ta is not None else None
            tb = tb._asdict() if tb is not None else None
            _add_stats(ca, ta, tb, f.duration_sec)
            _add_stats(cb, tb, ta, f.duration_sec)
            if f.result == "NC":
                continue
            score_a = {"A": 1.0, "B": 0.0, "D": 0.5}[f.result]
            _elo_update(ca, cb, score_a, f.method)
            won_a = None if f.result == "D" else f.result == "A"
            _record_result(ca, won_a, f.method, f.date, f.fighter_b)
            _record_result(cb, None if won_a is None else not won_a, f.method, f.date, f.fighter_a)
        self.rows = pd.DataFrame(rows)

    def bio(self, name: str) -> dict:
        if name in self.fighters.index:
            return self.fighters.loc[name].to_dict()
        return {}

    def current(self, name: str, on: pd.Timestamp) -> dict:
        return snapshot(self.careers.get(name, Career()), self.bio(name), on)

    def record(self, name: str) -> str:
        c = self.careers.get(name)
        if not c:
            return "0-0 (UFC)"
        draws = c.fights - c.wins - c.losses
        return f"{c.wins}-{c.losses}" + (f"-{draws}" if draws else "") + " (UFC)"


def diff_frame(rows: pd.DataFrame) -> pd.DataFrame:
    """A-minus-B differences for every feature; missing bio values become 0."""
    out = pd.DataFrame({f"d_{k}": rows[f"a_{k}"] - rows[f"b_{k}"] for k in FEATURES})
    out["d_layoff_days"] = out["d_layoff_days"].clip(-1500, 1500)
    return out.fillna(0.0)
