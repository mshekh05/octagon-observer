"""Save UFC.com Fight Tracker JSON (from a HAR export or a direct URL) for Phase 4.

The tracker's data feed is undocumented, so on event night we capture what the
page actually loads and build the live-stats parser from real samples.
"""
from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .config import DATA_DIR

TRACKER_DIR = DATA_DIR / "tracker"
INTERESTING = re.compile(r"fight/live|/api/v\d+/(fight|event)|fighttracker|livestats", re.I)


def _save(url: str, body: str, dest: Path) -> Path | None:
    try:
        data = json.loads(body)
    except ValueError:
        return None
    dest.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    slug = re.sub(r"[^A-Za-z0-9]+", "_", url.split("?")[0])[-60:]
    path = dest / f"{stamp}_{slug}.json"
    path.write_text(json.dumps({"url": url, "captured_at": stamp, "data": data}, indent=1))
    return path


def from_har(har_path: Path, dest: Path = TRACKER_DIR) -> list[Path]:
    har = json.loads(har_path.read_text())
    saved = []
    for entry in har.get("log", {}).get("entries", []):
        url = entry.get("request", {}).get("url", "")
        content = entry.get("response", {}).get("content", {})
        if not INTERESTING.search(url) or "json" not in content.get("mimeType", ""):
            continue
        text = content.get("text", "")
        if content.get("encoding") == "base64":
            text = base64.b64decode(text).decode("utf-8", "ignore")
        path = _save(url, text, dest)
        if path:
            saved.append(path)
    return saved


def from_url(url: str, dest: Path = TRACKER_DIR) -> list[Path]:
    resp = httpx.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    path = _save(url, resp.text, dest)
    return [path] if path else []
