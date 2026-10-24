"""Fighter photos from Wikidata / Wikimedia Commons (freely licensed, public API).

Each lookup finds the fighter's Wikidata item (its description must say mixed martial
arts, so a boxer with the same name doesn't match), and uses the item's "image" (P18):
a photo editors chose to show that person. Coverage is good for well-known fighters
and thin for newer ones, which get initials in the app instead. Results, including
"no photo", are cached on disk; misses are retried after a week.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.parse import quote

import httpx

from ..config import CACHE_DIR

API = "https://www.wikidata.org/w/api.php"
CACHE = CACHE_DIR / "fighter_images.json"
# Wikimedia's robot policy wants a User-Agent that identifies the app and how to reach its owner
USER_AGENT = "OctagonEdge/0.1 (self-hosted personal app; https://github.com/mshekh05/octagon-observer) python-httpx"
RETRY_MISS_SEC = 7 * 24 * 3600
MMA_WORDS = ("mixed martial", "mma")


def _load(cache: Path) -> dict:
    try:
        return json.loads(cache.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def _find(name: str, http: httpx.Client) -> dict | None:
    headers = {"User-Agent": USER_AGENT}
    found = http.get(API, headers=headers, params={"action": "wbsearchentities", "search": name, "language": "en",
                                                  "type": "item", "limit": 5, "format": "json"})
    found.raise_for_status()
    items = [x for x in found.json().get("search", [])
             if any(w in (x.get("description") or "").lower() for w in MMA_WORDS)]
    if not items:
        return None
    qid = items[0]["id"]
    ent = http.get(API, headers=headers, params={"action": "wbgetentities", "ids": qid, "props": "claims", "format": "json"})
    ent.raise_for_status()
    claims = ent.json()["entities"][qid].get("claims", {}).get("P18", [])
    if not claims:
        return None
    file = claims[0]["mainsnak"]["datavalue"]["value"]
    return {"url": f"https://commons.wikimedia.org/wiki/Special:FilePath/{quote(file)}?width=320",
            "credit": f"https://commons.wikimedia.org/wiki/File:{quote(file.replace(' ', '_'))}"}


def lookup(name: str, http: httpx.Client, cache: Path = CACHE) -> dict | None:
    """{"url": photo, "credit": its Commons page} or None when there's no free photo of this fighter."""
    data = _load(cache)
    hit = data.get(name)
    if hit and (hit.get("url") or time.time() - hit.get("ts", 0) < RETRY_MISS_SEC):
        return {"url": hit["url"], "credit": hit.get("credit")} if hit.get("url") else None
    found = _find(name, http)
    data[name] = {**(found or {"url": None}), "ts": time.time()}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data))
    return found
