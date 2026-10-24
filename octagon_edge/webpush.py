"""Web Push (VAPID) to the installed PWA. Keys are generated once into data/vapid.json."""
from __future__ import annotations

import base64
import json
import logging
import os

from cryptography.hazmat.primitives import serialization
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import DATA_DIR
from .db import PushSubscription

log = logging.getLogger(__name__)
VAPID_FILE = DATA_DIR / "vapid.json"
PRIVATE_PEM = DATA_DIR / "vapid_private.pem"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def ensure_keys() -> str:
    """Create the VAPID key pair if missing; returns the public key for the browser."""
    if VAPID_FILE.exists() and PRIVATE_PEM.exists():
        return json.loads(VAPID_FILE.read_text())["public_key"]
    from py_vapid import Vapid01

    v = Vapid01()
    v.generate_keys()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    v.save_key(str(PRIVATE_PEM))
    raw = v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    public = _b64url(raw)
    VAPID_FILE.write_text(json.dumps({"public_key": public}))
    return public


def subject() -> str:
    return os.environ.get("VAPID_SUBJECT") or "mailto:octagon-edge@localhost"


def send_all(db: Session, title: str, body: str, url: str = "/", urgent: bool = False) -> int:
    """Push to every subscribed device; drops subscriptions the push service says are gone."""
    from pywebpush import WebPushException, webpush

    subs = db.scalars(select(PushSubscription)).all()
    if not subs:
        return 0
    ensure_keys()
    payload = json.dumps({"title": title, "body": body, "url": url, "urgent": urgent})
    sent = 0
    for s in subs:
        try:
            webpush({"endpoint": s.endpoint, "keys": {"p256dh": s.p256dh, "auth": s.auth}}, payload,
                    vapid_private_key=str(PRIVATE_PEM), vapid_claims={"sub": subject()},
                    ttl=600 if urgent else 3600, headers={"Urgency": "high" if urgent else "normal"})
            sent += 1
        except WebPushException as exc:
            code = getattr(exc.response, "status_code", None)
            if code in (404, 410):
                db.delete(s)
                log.info("Removed expired push subscription")
            else:
                log.warning("Web push failed (%s): %s", code, exc)
    db.commit()
    return sent
