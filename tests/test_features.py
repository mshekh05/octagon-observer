import pandas as pd

from octagon_edge.engine.features import History
from octagon_edge.engine.model import _fit, predict
from octagon_edge.engine.features import FEATURES


def tiny_history():
    fights = pd.DataFrame([
        {"event": "E1", "bout": "Ann vs. Bea", "date": pd.Timestamp("2020-01-01"), "fighter_a": "Ann",
         "fighter_b": "Bea", "result": "A", "method": "KO", "duration_sec": 300},
        {"event": "E2", "bout": "Ann vs. Cat", "date": pd.Timestamp("2020-06-01"), "fighter_a": "Ann",
         "fighter_b": "Cat", "result": "B", "method": "DEC", "duration_sec": 900},
    ])
    fights["weightclass"] = "Flyweight"
    fights["url"] = ["u1", "u2"]
    totals = pd.DataFrame([
        {"event": "E1", "bout": "Ann vs. Bea", "fighter": "Ann", "kd": 1, "sub_att": 0, "ctrl": 0, "sig_l": 30, "sig_a": 50, "td_l": 0, "td_a": 0},
        {"event": "E1", "bout": "Ann vs. Bea", "fighter": "Bea", "kd": 0, "sub_att": 0, "ctrl": 0, "sig_l": 5, "sig_a": 20, "td_l": 0, "td_a": 2},
    ])
    fighters = pd.DataFrame({"height_in": [64.0], "reach_in": [65.0], "stance": [""],
                             "dob": [pd.Timestamp("1995-01-01")]}, index=pd.Index(["Ann"], name="name"))
    return History(fights, totals, fighters)


def test_snapshots_use_only_prior_fights():
    h = tiny_history()
    first, second = h.rows.iloc[0], h.rows.iloc[1]
    assert first["a_n_fights"] == 0 and first["a_elo"] == 1500  # nothing known before the debut
    assert second["a_n_fights"] == 1 and second["a_elo"] > 1500  # sees the first win, not the loss
    assert second["a_slpm"] > 3.6  # 30 strikes in 5 minutes pulls the shrunk rate above the prior
    assert h.record("Ann") == "1-1 (UFC)"


def test_model_is_symmetric():
    h = tiny_history()
    rows = pd.concat([h.rows] * 5, ignore_index=True)
    pipe = _fit(rows, [f"d_{k}" for k in FEATURES])
    on = pd.Timestamp("2021-01-01")
    a, c = h.current("Ann", on), h.current("Cat", on)
    assert abs(predict(pipe, a, c) + predict(pipe, c, a) - 1) < 1e-9
