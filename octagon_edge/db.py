"""Database schema (SQLAlchemy 2.0). Postgres in Docker, SQLite for dev and tests."""
from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint,
                        create_engine, select)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from .config import DATA_DIR


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    type_annotation_map = {datetime: DateTime(timezone=True), dict: JSON, list: JSON}


class Event(Base):
    __tablename__ = "event"
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    date: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD (event local date)
    venue: Mapped[str] = mapped_column(String(200), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    fights: Mapped[list["Fight"]] = relationship(back_populates="event", order_by="Fight.order")


class Fight(Base):
    __tablename__ = "fight"
    __table_args__ = (UniqueConstraint("event_id", "a", "b"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("event.id"))
    order: Mapped[int] = mapped_column(Integer, default=0)
    a: Mapped[str] = mapped_column(String(120))
    b: Mapped[str] = mapped_column(String(120))
    weightclass: Mapped[str] = mapped_column(String(60), default="")
    segment: Mapped[str] = mapped_column(String(20), default="main")
    rounds: Mapped[int] = mapped_column(Integer, default=3)
    title: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(12), default="scheduled")  # scheduled | live | finished
    winner: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0 = A, 1 = B, None = draw/NC/unknown
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    event: Mapped[Event] = relationship(back_populates="fights")

    @property
    def key(self) -> str:
        return f"{self.a} vs {self.b}"


class Prediction(Base):
    __tablename__ = "prediction"
    id: Mapped[int] = mapped_column(primary_key=True)
    fight_id: Mapped[int] = mapped_column(ForeignKey("fight.id"), index=True)
    ts: Mapped[datetime] = mapped_column(default=utcnow)
    p_model: Mapped[float | None] = mapped_column(Float, nullable=True)
    p_market: Mapped[float | None] = mapped_column(Float, nullable=True)
    p_final: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_source: Mapped[str] = mapped_column(String(120), default="")
    confidence: Mapped[str] = mapped_column(String(10), default="normal")
    pick_side: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pick_book: Mapped[str | None] = mapped_column(String(60), nullable=True)
    pick_decimal: Mapped[float | None] = mapped_column(Float, nullable=True)
    pick_edge: Mapped[float | None] = mapped_column(Float, nullable=True)
    stake: Mapped[float | None] = mapped_column(Float, nullable=True)
    details: Mapped[dict] = mapped_column(default=dict)  # snapshots, records, notes, llm, sides


class OddsSnapshot(Base):
    __tablename__ = "odds_snapshot"
    id: Mapped[int] = mapped_column(primary_key=True)
    fight_id: Mapped[int] = mapped_column(ForeignKey("fight.id"), index=True)
    ts: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    source: Mapped[str] = mapped_column(String(30))  # odds_api | Kalshi | Polymarket
    book: Mapped[str | None] = mapped_column(String(60), nullable=True)
    dec_a: Mapped[float | None] = mapped_column(Float, nullable=True)
    dec_b: Mapped[float | None] = mapped_column(Float, nullable=True)
    p_a: Mapped[float | None] = mapped_column(Float, nullable=True)


class AlertRow(Base):
    __tablename__ = "alert"
    id: Mapped[int] = mapped_column(primary_key=True)
    fight_id: Mapped[int | None] = mapped_column(ForeignKey("fight.id"), nullable=True, index=True)
    ts: Mapped[datetime] = mapped_column(default=utcnow)
    kind: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    urgent: Mapped[bool] = mapped_column(Boolean, default=False)


class LiveRound(Base):
    __tablename__ = "live_round"
    __table_args__ = (UniqueConstraint("fight_id", "round"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    fight_id: Mapped[int] = mapped_column(ForeignKey("fight.id"), index=True)
    round: Mapped[int] = mapped_column(Integer)
    winner: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0 A, 1 B, None even
    kd_a: Mapped[int] = mapped_column(Integer, default=0)
    kd_b: Mapped[int] = mapped_column(Integer, default=0)
    td_a: Mapped[int] = mapped_column(Integer, default=0)
    td_b: Mapped[int] = mapped_column(Integer, default=0)
    sig_a: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sig_b: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ctrl_a: Mapped[int] = mapped_column(Integer, default=0)  # seconds
    ctrl_b: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(20), default="manual")
    ts: Mapped[datetime] = mapped_column(default=utcnow)


class LiveEstimate(Base):
    __tablename__ = "live_estimate"
    id: Mapped[int] = mapped_column(primary_key=True)
    fight_id: Mapped[int] = mapped_column(ForeignKey("fight.id"), index=True)
    ts: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    after_round: Mapped[int] = mapped_column(Integer, default=0)
    p_rounds: Mapped[float | None] = mapped_column(Float, nullable=True)  # from round-state model
    p_market: Mapped[float | None] = mapped_column(Float, nullable=True)
    p_live: Mapped[float] = mapped_column(Float)


class Bet(Base):
    __tablename__ = "bet"
    id: Mapped[int] = mapped_column(primary_key=True)
    fight_id: Mapped[int] = mapped_column(ForeignKey("fight.id"), index=True)
    side: Mapped[int] = mapped_column(Integer)
    book: Mapped[str] = mapped_column(String(60), default="")
    decimal: Mapped[float] = mapped_column(Float)
    stake: Mapped[float] = mapped_column(Float)
    placed_at: Mapped[datetime] = mapped_column(default=utcnow)
    live: Mapped[bool] = mapped_column(Boolean, default=False)
    closing_decimal: Mapped[float | None] = mapped_column(Float, nullable=True)
    result: Mapped[str] = mapped_column(String(8), default="open")  # open | won | lost | void
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")


class SocialPost(Base):
    __tablename__ = "social_post"
    __table_args__ = (UniqueConstraint("fighter", "link"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    fighter: Mapped[str] = mapped_column(String(120), index=True)
    source: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[str] = mapped_column(String(500))
    text: Mapped[str] = mapped_column(Text, default="")
    flags: Mapped[list] = mapped_column(default=list)
    sentiment: Mapped[float | None] = mapped_column(Float, nullable=True)  # -1..1
    ts: Mapped[datetime] = mapped_column(default=utcnow)


class PushSubscription(Base):
    __tablename__ = "push_subscription"
    id: Mapped[int] = mapped_column(primary_key=True)
    endpoint: Mapped[str] = mapped_column(String(600), unique=True)
    p256dh: Mapped[str] = mapped_column(String(200))
    auth: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Setting(Base):
    __tablename__ = "setting"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[dict] = mapped_column(default=dict)


def database_url() -> str:
    return os.environ.get("DATABASE_URL") or f"sqlite:///{DATA_DIR / 'octagon.db'}"


_engines: dict[str, sessionmaker] = {}


def sessions(url: str | None = None) -> sessionmaker[Session]:
    url = url or database_url()
    if url not in _engines:
        if url.startswith("sqlite:///"):
            DATA_DIR.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, pool_pre_ping=True,
                               connect_args={"check_same_thread": False} if url.startswith("sqlite") else {})
        Base.metadata.create_all(engine)
        _engines[url] = sessionmaker(engine, expire_on_commit=False)
    return _engines[url]


def get_setting(db: Session, key: str, default=None):
    row = db.get(Setting, key)
    return row.value.get("v", default) if row else default


def put_setting(db: Session, key: str, value) -> None:
    row = db.get(Setting, key)
    if row:
        row.value = {"v": value}
    else:
        db.add(Setting(key=key, value={"v": value}))


def latest(db: Session, model, fight_id: int):
    return db.scalars(select(model).where(model.fight_id == fight_id).order_by(model.id.desc()).limit(1)).first()
