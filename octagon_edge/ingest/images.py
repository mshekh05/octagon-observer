"""Fighter photos: the fighter's UFC.com athlete page first, then Wikidata / Wikimedia Commons.

UFC.com has a current photo of every fighter on the roster (the page's og:image); the app
links to it rather than copying it. For fighters UFC.com doesn't know, the lookup finds the
fighter's Wikidata item (its description must say mixed martial arts, so a boxer with the
same name doesn't match) and uses the item's "image" (P18). A photo URL saved with the
fighter's notes overrides both. Results, including "no photo", are cached on disk; misses
are retried after a week.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from pathlib import Path
from urllib.parse import quote

import httpx

from ..config import CACHE_DIR

API = "https://www.wikidata.org/w/api.php"
UFC_ATHLETE = "https://www.ufc.com/athlete/"
CACHE = CACHE_DIR / "fighter_images.json"
# Wikimedia's robot policy wants a User-Agent that identifies the app and how to reach its owner
USER_AGENT = "OctagonEdge/0.1 (self-hosted personal app; https://github.com/mshekh05/octagon-observer) python-httpx"
RETRY_MISS_SEC = 7 * 24 * 3600
MMA_WORDS = ("mixed martial", "mma")
VERSION = 2  # cache entries from an older lookup are redone
OG_IMAGE = re.compile(r'<meta property="og:image" content="([^"]+/images/[^"]+)"')


def _load(cache: Path) -> dict:
    try:
        return json.loads(cache.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def ufc_slug(name: str) -> str:
    """"Lone'er Kavanagh" -> "loneer-kavanagh", the way UFC.com names athlete pages."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower().replace("'", "")
    return re.sub(r"[^a-z0-9]+", "-", plain).strip("-")


def _find_ufc(name: str, http: httpx.Client) -> dict | None:
    page = UFC_ATHLETE + ufc_slug(name)
    resp = http.get(page, headers={"User-Agent": USER_AGENT})
    if resp.status_code != 200:
        return None
    m = OG_IMAGE.search(resp.text)
    if not m:
        return None  # an unknown name lands on a search page with no athlete image
    return {"url": m.group(1).replace("https://ufc.com/", "https://www.ufc.com/"), "credit": page}


def _find_wikidata(name: str, http: httpx.Client) -> dict | None:
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
    """{"url": photo, "credit": where it's from} or None when no photo of this fighter was found."""
    data = _load(cache)
    hit = data.get(name)
    if hit and hit.get("v") == VERSION and (hit.get("url") or time.time() - hit.get("ts", 0) < RETRY_MISS_SEC):
        return {"url": hit["url"], "credit": hit.get("credit")} if hit.get("url") else None
    try:
        found = _find_ufc(name, http)
    except httpx.HTTPError:
        found = None
    found = found or _find_wikidata(name, http)
    data[name] = {**(found or {"url": None}), "ts": time.time(), "v": VERSION}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data))
    return found
