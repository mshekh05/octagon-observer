"""Historical UFC moneylines from the shortlikeafox/ultimate_ufc_dataset CSV (Apache-2.0).

One American price per fighter per bout, taken from BestFightOdds, for UFC
fights since 2010; also method-of-victory prices for most fights since 2014.
The dataset doesn't say whether a line is the opener or the close, so treat
it as "a widely available price before the fight".
"""
from __future__ import annotations

import time
from pathlib import Path

import httpx
import pandas as pd

from ..config import CACHE_DIR
from ..engine.odds_math import american_to_decimal, devig_power
from .names import normalize, same_fighter

URL = "https://raw.githubusercontent.com/shortlikeafox/ultimate_ufc_dataset/main/ufc-master.csv"
MAX_AGE_SEC = 7 * 24 * 3600


def download(dest: Path = CACHE_DIR / "odds_history", force: bool = False) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / "ufc-master.csv"
    if force or not path.exists() or time.time() - path.stat().st_mtime > MAX_AGE_SEC:
        with httpx.Client(timeout=60, follow_redirects=True) as client:
            resp = client.get(URL)
            resp.raise_for_status()
            path.write_bytes(resp.content)
    return path


def _dec(series: pd.Series) -> pd.Series:
    return series.map(lambda x: american_to_decimal(x) if pd.notna(x) and x != 0 else None)


def load(path: Path) -> pd.DataFrame:
    """One row per bout with both moneylines, as decimals; red = side 'r', blue = side 'b'."""
    raw = pd.read_csv(path)
    df = pd.DataFrame({
        "date": pd.to_datetime(raw["date"]),
        "r": raw["R_fighter"].astype(str).str.strip(),
        "b": raw["B_fighter"].astype(str).str.strip(),
        "r_dec": _dec(raw["R_odds"]), "b_dec": _dec(raw["B_odds"]),
        "gender": raw.get("gender", pd.Series("", index=raw.index)).fillna(""),
        "country": raw.get("country", pd.Series("", index=raw.index)).fillna("").str.strip(),
        "r_ko_dec": _dec(raw["r_ko_odds"]), "b_ko_dec": _dec(raw["b_ko_odds"]),
        "r_sub_dec": _dec(raw["r_sub_odds"]), "b_sub_dec": _dec(raw["b_sub_odds"]),
        "r_decision_dec": _dec(raw["r_dec_odds"]), "b_decision_dec": _dec(raw["b_dec_odds"]),
    })
    return df.dropna(subset=["r_dec", "b_dec"]).reset_index(drop=True)


def match(rows: pd.DataFrame, odds: pd.DataFrame, max_days: int = 1) -> pd.DataFrame:
    """Attach market prices to History.rows, oriented to each row's fighter_a.

    Returns one row per matched bout with url, dec_a, dec_b, p_market (de-vigged
    P(A wins)) and the extra columns (gender, country, method prices).
    """
    by_date: dict[pd.Timestamp, list[int]] = {}
    for i, d in enumerate(rows["date"]):
        by_date.setdefault(pd.Timestamp(d).normalize(), []).append(i)
    ra = rows["fighter_a"].tolist()
    rb = rows["fighter_b"].tolist()
    urls = rows["url"].tolist()
    keys_a = [normalize(n) for n in ra]
    keys_b = [normalize(n) for n in rb]

    out = []
    for o in odds.itertuples(index=False):
        day = pd.Timestamp(o.date).normalize()
        cand = [i for k in range(-max_days, max_days + 1) for i in by_date.get(day + pd.Timedelta(days=k), [])]
        hit = None
        nr, nb = normalize(o.r), normalize(o.b)
        for i in cand:  # exact names first, then the fuzzier surname match
            if {keys_a[i], keys_b[i]} == {nr, nb}:
                hit = (i, keys_a[i] == nr)
                break
        if hit is None:
            for i in cand:
                if same_fighter(ra[i], o.r) and same_fighter(rb[i], o.b):
                    hit = (i, True)
                    break
                if same_fighter(ra[i], o.b) and same_fighter(rb[i], o.r):
                    hit = (i, False)
                    break
        if hit is None:
            continue
        i, red_is_a = hit
        side = ("r", "b") if red_is_a else ("b", "r")
        dec_a, dec_b = getattr(o, f"{side[0]}_dec"), getattr(o, f"{side[1]}_dec")
        row = {"url": urls[i], "dec_a": dec_a, "dec_b": dec_b, "p_market": devig_power([dec_a, dec_b])[0],
               "gender": o.gender, "country": o.country}
        for m in ("ko", "sub", "decision"):
            row[f"{m}_dec_a"] = getattr(o, f"{side[0]}_{m}_dec")
            row[f"{m}_dec_b"] = getattr(o, f"{side[1]}_{m}_dec")
        out.append(row)
    return pd.DataFrame(out).drop_duplicates("url")
