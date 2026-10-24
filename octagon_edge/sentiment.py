"""Sentiment scoring for fan chatter, in [-1, 1].

Uses the cardiffnlp Twitter RoBERTa model when the optional `[ml]` extra is
installed (pip install -e '.[ml]'), otherwise a small MMA word list.
Treat it as a hype/contrarian indicator, never as a direct probability input.
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache

log = logging.getLogger(__name__)
MODEL = "cardiffnlp/twitter-roberta-base-sentiment-latest"

POSITIVE = re.compile(r"\b(lock|easy work|dominat\w*|smash\w*|levels above|too good|confident|sharp|"
                      r"goat|beast|killer|best shape|looks great|elite|ez|cruise)\b", re.I)
NEGATIVE = re.compile(r"\b(washed|overrated|slow|chin is gone|declin\w*|cooked|exposed|struggl\w*|"
                      r"worried|old|done|finished|bum|fraud|gassed|ring rust)\b", re.I)


@lru_cache(maxsize=1)
def _pipeline():
    try:
        from transformers import pipeline  # type: ignore

        return pipeline("sentiment-analysis", model=MODEL, truncation=True, max_length=256)
    except Exception as exc:  # noqa: BLE001 - optional dependency
        log.info("Transformer sentiment unavailable (%s); using word list", exc.__class__.__name__)
        return None


def lexicon_score(text: str) -> float:
    pos, neg = len(POSITIVE.findall(text)), len(NEGATIVE.findall(text))
    return 0.0 if pos + neg == 0 else (pos - neg) / (pos + neg)


def score(texts: list[str], use_model: bool = True) -> list[float]:
    if not texts:
        return []
    pipe = _pipeline() if use_model else None
    if pipe is None:
        return [lexicon_score(t) for t in texts]
    out = []
    for res in pipe([t[:1000] for t in texts], batch_size=16):
        label = res["label"].lower()
        out.append(res["score"] if label.startswith("pos") else -res["score"] if label.startswith("neg") else 0.0)
    return out


def aggregate(scores: list[float], sources: list[str]) -> dict:
    """Summary for one fighter."""
    if not scores:
        return {"n": 0, "mean": 0.0, "positive_share": None, "by_source": {}}
    by: dict[str, list[float]] = {}
    for sc, src in zip(scores, sources):
        by.setdefault(src.split("/")[0] if src.startswith("r/") else src, []).append(sc)
    polar = [s for s in scores if abs(s) > 0.2]
    return {
        "n": len(scores),
        "mean": round(sum(scores) / len(scores), 3),
        "positive_share": round(sum(s > 0 for s in polar) / len(polar), 3) if polar else None,
        "by_source": {k: {"n": len(v), "mean": round(sum(v) / len(v), 3)} for k, v in by.items()},
    }
