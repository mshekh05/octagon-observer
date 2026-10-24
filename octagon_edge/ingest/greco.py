"""Historical UFC data from the Greco1899/scrape_ufc_stats CSVs (scraped from ufcstats.com).

The repository is GPL-3.0; we only download its CSV data, no code is copied.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import httpx
import pandas as pd

from ..config import CACHE_DIR

BASE = "https://raw.githubusercontent.com/Greco1899/scrape_ufc_stats/main"
FILES = [
    "ufc_event_details", "ufc_fight_results", "ufc_fight_stats", "ufc_fighter_tott",
]
MAX_AGE_SEC = 12 * 3600


def download(dest: Path = CACHE_DIR / "greco", force: bool = False) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        for name in FILES:
            path = dest / f"{name}.csv"
            if not force and path.exists() and time.time() - path.stat().st_mtime < MAX_AGE_SEC:
                continue
            resp = client.get(f"{BASE}/{name}.csv")
            resp.raise_for_status()
            path.write_bytes(resp.content)
    return dest


def _strip(df: pd.DataFrame) -> pd.DataFrame:
    for col in df.columns:
        if pd.api.types.is_string_dtype(df[col]) or df[col].dtype == object:
            df[col] = df[col].astype("string").str.strip().astype(object)
    return df


def _clock(value: str) -> int | None:
    m = re.fullmatch(r"(\d+):(\d{2})", str(value).strip())
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def _of(value: str) -> tuple[int, int]:
    m = re.fullmatch(r"(\d+) of (\d+)", str(value).strip())
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def _inches(value: str) -> float | None:
    s = str(value).strip()
    m = re.fullmatch(r"(\d+)' (\d+)\"", s)
    if m:
        return int(m.group(1)) * 12 + int(m.group(2))
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\"", s)
    return float(m.group(1)) if m else None


def load_fights(src: Path) -> pd.DataFrame:
    """One row per bout: fighter_a, fighter_b, winner side, method, duration."""
    events = _strip(pd.read_csv(src / "ufc_event_details.csv"))
    events["date"] = pd.to_datetime(events["DATE"], format="%B %d, %Y")
    results = _strip(pd.read_csv(src / "ufc_fight_results.csv"))
    df = results.merge(events[["EVENT", "date"]], on="EVENT", how="left")
    df = df.dropna(subset=["date"])
    names = df["BOUT"].str.split(r"\s+vs\.\s+", n=1, expand=True, regex=True)
    df["fighter_a"], df["fighter_b"] = names[0].str.strip(), names[1].str.strip()
    outcome = df["OUTCOME"].str.upper()
    df["result"] = outcome.map({"W/L": "A", "L/W": "B", "D/D": "D", "NC/NC": "NC"}).fillna("NC")
    method = df["METHOD"].fillna("").str.upper()
    df["method"] = method.map(
        lambda m: "KO" if "KO" in m else "SUB" if "SUB" in m else "DEC" if "DEC" in m else "OTHER")
    rnd = pd.to_numeric(df["ROUND"], errors="coerce").fillna(1).astype(int)
    secs = df["TIME"].map(_clock).fillna(300)
    df["duration_sec"] = (rnd - 1) * 300 + secs
    df["scheduled_rounds"] = df["TIME FORMAT"].fillna("").str.extract(r"(\d+) Rnd")[0].astype(float).fillna(3)
    df["title"] = df["WEIGHTCLASS"].fillna("").str.contains("Title")
    df["weightclass"] = df["WEIGHTCLASS"].fillna("").str.replace(r"\s*(UFC|Title|Bout|Interim|Tournament)\s*", " ", regex=True).str.strip()
    df = df.rename(columns={"EVENT": "event", "BOUT": "bout", "URL": "url"})
    df = df.dropna(subset=["fighter_a", "fighter_b"])
    cols = ["url", "event", "bout", "date", "fighter_a", "fighter_b", "result", "method",
            "duration_sec", "scheduled_rounds", "title", "weightclass"]
    return df[cols].sort_values("date", kind="stable").reset_index(drop=True)


def load_round_stats(src: Path) -> pd.DataFrame:
    """Per bout, per round, per fighter stats."""
    st = _strip(pd.read_csv(src / "ufc_fight_stats.csv"))
    st = st[st["ROUND"].str.startswith("Round", na=False)]
    out = pd.DataFrame({
        "event": st["EVENT"], "bout": st["BOUT"], "fighter": st["FIGHTER"],
        "round": st["ROUND"].str.extract(r"(\d+)")[0].astype(int),
        "kd": pd.to_numeric(st["KD"], errors="coerce").fillna(0),
        "sub_att": pd.to_numeric(st["SUB.ATT"], errors="coerce").fillna(0),
        "ctrl": st["CTRL"].map(_clock).fillna(0),
    })
    for col, key in (("SIG.STR.", "sig"), ("TD", "td")):
        pairs = st[col].map(_of)
        out[f"{key}_l"] = pairs.map(lambda p: p[0])
        out[f"{key}_a"] = pairs.map(lambda p: p[1])
    return out


def load_fight_totals(src: Path) -> pd.DataFrame:
    """Per bout, per fighter: summed round stats."""
    rounds = load_round_stats(src).drop(columns="round")
    return rounds.groupby(["event", "bout", "fighter"], as_index=False).sum(numeric_only=True)


def load_fighters(src: Path) -> pd.DataFrame:
    tott = _strip(pd.read_csv(src / "ufc_fighter_tott.csv"))
    out = pd.DataFrame({
        "name": tott["FIGHTER"],
        "height_in": tott["HEIGHT"].map(_inches),
        "reach_in": tott["REACH"].map(_inches),
        "stance": tott["STANCE"].fillna(""),
        "dob": pd.to_datetime(tott["DOB"], format="%b %d, %Y", errors="coerce"),
        "url": tott["URL"],
    })
    # Same-name fighters exist; keep the one with the most data.
    out["filled"] = out[["height_in", "reach_in", "dob"]].notna().sum(axis=1)
    out = out.sort_values("filled", ascending=False).drop_duplicates("name").drop(columns="filled")
    return out.set_index("name")
