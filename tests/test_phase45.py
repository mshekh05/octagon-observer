from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from octagon_edge.engine import live as L
from octagon_edge.ingest.social import parse_bluesky, parse_youtube_comments
from octagon_edge.sentiment import aggregate, lexicon_score, score


def _fights():
    base = {"event": "E", "date": pd.Timestamp("2024-01-01"), "scheduled_rounds": 3.0}
    return pd.DataFrame([
        {**base, "url": "u1", "bout": "Ann vs. Bea", "fighter_a": "Ann", "fighter_b": "Bea", "result": "A",
         "method": "DEC", "duration_sec": 900},
        {**base, "url": "u2", "bout": "Cat vs. Dee", "fighter_a": "Cat", "fighter_b": "Dee", "result": "B",
         "method": "KO", "duration_sec": 400},  # finished in round 2: only round 1 is a completed state
    ])


def _rounds():
    rows = []
    for bout, a, b, n in (("Ann vs. Bea", "Ann", "Bea", 3), ("Cat vs. Dee", "Cat", "Dee", 2)):
        for r in range(1, n + 1):
            rows.append({"event": "E", "bout": bout, "fighter": a, "round": r, "kd": 1 if r == 1 else 0,
                         "sub_att": 0, "ctrl": 30, "sig_l": 20, "sig_a": 40, "td_l": 1, "td_a": 2})
            rows.append({"event": "E", "bout": bout, "fighter": b, "round": r, "kd": 0,
                         "sub_att": 0, "ctrl": 0, "sig_l": 10, "sig_a": 30, "td_l": 0, "td_a": 1})
    return pd.DataFrame(rows)


def test_live_dataset_rows_only_for_completed_rounds():
    df = L.build_dataset(_fights(), _rounds(), {"u1": 0.6, "u2": 0.5})
    assert len(df) == 3 + 1
    first = df.iloc[0]
    assert first["rw"] == 1 and first["kd"] == 1 and first["a_won"] == 1
    assert df[df["round"] == 3]["rw"].iloc[0] == 3


def test_live_model_moves_with_rounds_and_is_symmetric():
    df = pd.concat([L.build_dataset(_fights(), _rounds(), {"u1": 0.6, "u2": 0.5})] * 20, ignore_index=True)
    # add mirrored-outcome rows so both classes exist for each state
    lm = L.LiveModel(L._fit(df, L.MINIMAL), L._fit(df, L.FULL))
    up = lm.predict(0.5, [L.RoundInput(winner=0)], 3)
    down = lm.predict(0.5, [L.RoundInput(winner=1)], 3)
    assert up > 0.5 > down and up + down == pytest.approx(1)
    assert lm.predict(0.42, [], 3) == 0.42


def test_predict_from_prior_keeps_the_prior_and_adds_round_evidence():
    df = pd.concat([L.build_dataset(_fights(), _rounds(), {"u1": 0.6, "u2": 0.5})] * 20, ignore_index=True)
    lm = L.LiveModel(L._fit(df, L.MINIMAL), L._fit(df, L.FULL))
    won_r1 = [L.RoundInput(winner=0)]
    assert lm.predict_from_prior(0.7, [], 3) == 0.7
    # A 50% prior gets exactly the round evidence; a stronger prior keeps its lead.
    shift = lm.round_evidence(won_r1, 3)
    assert lm.predict_from_prior(0.5, won_r1, 3) == pytest.approx(L.sigmoid(shift))
    assert lm.predict_from_prior(0.7, won_r1, 3) > lm.predict_from_prior(0.5, won_r1, 3) > 0.5


def test_blend_with_market():
    assert L.blend_with_market(0.7, None) == 0.7
    assert 0.5 < L.blend_with_market(0.7, 0.5) < 0.6


def test_sentiment_lexicon_and_aggregate():
    assert lexicon_score("he looks washed and slow") < 0 < lexicon_score("too good, easy work, a lock")
    assert score(["neutral words here"], use_model=False) == [0.0]
    agg = aggregate([0.8, -0.5, 0.0], ["r/MMA", "Bluesky", "r/MMAbetting"])
    assert agg["n"] == 3 and agg["positive_share"] == 0.5 and agg["by_source"]["r"]["n"] == 2


def test_bluesky_and_youtube_parsers():
    bsky = {"posts": [{"uri": "at://did:plc:x/app.bsky.feed.post/3abc", "author": {"handle": "fan.bsky.social"},
                       "record": {"text": "Evloev missed weight?? wow"}}]}
    item = parse_bluesky(bsky, "Movsar Evloev")[0]
    assert item.link == "https://bsky.app/profile/fan.bsky.social/post/3abc" and "weight" in item.flags
    yt = {"items": [{"snippet": {"topLevelComment": {"snippet": {"textOriginal": "Volk by KO, injury rumours are fake",
                                                                 "videoId": "vid1"}}}}]}
    c = parse_youtube_comments(yt, "UFC 333 predictions")[0]
    assert c.link.endswith("v=vid1") and "injury" in c.flags


def test_worker_event_window(monkeypatch):
    from octagon_edge.worker import Worker

    class Ev:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    w = Worker.__new__(Worker)
    monkeypatch.setenv("EVENT_WINDOW_START_UTC", str(datetime.now(timezone.utc).hour))  # window open now, at any hour
    assert w.event_window(Ev())
    Ev.date = (datetime.now(timezone.utc) + timedelta(days=3)).strftime("%Y-%m-%d")
    assert not w.event_window(Ev())
