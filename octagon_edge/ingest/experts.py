"""Aggregated handicapper picks (SplitPicks' "Kings Table" for one event).

No documented API - this is a single hand-styled HTML page with fixed tables,
one column per handicapper. Rows are matched to a fight by the surnames
picked, not by position: the page lists fights earliest-prelim-first while
the rest of this app lists main-event-first, and that order isn't guaranteed
to stay the same from event to event.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx

from .card import Card

log = logging.getLogger(__name__)

USER_AGENT = "octagon-edge/0.1 (personal fight analytics)"
DEFAULT_WEIGHT = 0.55  # a handicapper with no visible record still beats a coin flip


@dataclass(frozen=True)
class ExpertConsensus:
    fight_key: str
    p_a: float | None  # weighted share of handicappers picking fighter A
    n: int  # handicappers who made a pick on this fight
    summary: str


_TABLE = re.compile(r"<table[^>]*>(.*?)</table>", re.S)
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)
_TAG = re.compile(r"<[^>]+>")
_YTD_PCT = re.compile(r"YTD.*?\(([\d.]+)%\)")


def _text(cell: str) -> str:
    return _TAG.sub("", cell).replace("&nbsp;", "").strip()


def _tables(html: str) -> list[list[list[str]]]:
    return [[[_text(c) for c in _CELL.findall(r)] for r in _ROW.findall(t)] for t in _TABLE.findall(html)]


def fetch_html(url: str, http: httpx.Client) -> str:
    resp = http.get(url, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return resp.text


def parse(html: str, card: Card) -> dict[str, ExpertConsensus]:
    votes: dict[str, list[tuple[int, float]]] = {f.key: [] for f in card.fights}  # fight_key -> [(side, weight)]

    for rows in _tables(html):
        rows = [r for r in rows if any(r)]
        if len(rows) < 4:  # header + at least one pick row + the two record rows
            continue
        header, record_rows, pick_rows = rows[0], rows[-2:], rows[1:-2]
        weights = [float(m.group(1)) / 100 if (m := _YTD_PCT.search(cell)) else DEFAULT_WEIGHT
                  for cell in record_rows[-1]]
        for row in pick_rows:
            names = {c for c in row if c}
            if not names:
                continue
            fight = next((f for f in card.fights if all(f.side_of(n) is not None for n in names)), None)
            if not fight:
                log.debug("SplitPicks row matched no fight: %s", names)
                continue
            for i, pick in enumerate(row):
                if not pick or i >= len(header):
                    continue
                side = fight.side_of(pick)
                if side is not None:
                    votes[fight.key].append((side, weights[i] if i < len(weights) else DEFAULT_WEIGHT))

    out: dict[str, ExpertConsensus] = {}
    for f in card.fights:
        vs = votes[f.key]
        wa = sum(w for side, w in vs if side == 0)
        wb = sum(w for side, w in vs if side == 1)
        p_a = wa / (wa + wb) if vs else None
        for_a = sum(1 for side, _ in vs if side == 0)
        summary = f"{for_a}/{len(vs)} handicappers for {f.a}" if vs else "no picks found"
        out[f.key] = ExpertConsensus(f.key, p_a, len(vs), summary)
    return out


def fetch_consensus(url: str, card: Card, http: httpx.Client) -> dict[str, ExpertConsensus]:
    return parse(fetch_html(url, http), card)
