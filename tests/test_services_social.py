import pytest

from octagon_edge import services as S
from octagon_edge.db import Event, Fight, SocialPost, sessions
from octagon_edge.ingest.news import NewsItem


@pytest.fixture
def db(tmp_path):
    url = f"sqlite:///{tmp_path / 'test.db'}"
    with sessions(url)() as session:
        yield session


def _fight(db, a="Fighter A", b="Fighter B") -> Fight:
    ev = Event(slug="test-card", name="Test Card", date="2026-10-24", venue="Somewhere")
    db.add(ev)
    db.flush()
    f = Fight(event_id=ev.id, a=a, b=b)
    db.add(f)
    db.commit()
    return f


class FA:
    def __init__(self, news_a, news_b):
        self.news_a, self.news_b = news_a, news_b


def test_store_posts_skips_an_already_stored_link_instead_of_failing(db):
    f = _fight(db)
    dup = NewsItem("RSS", "A duplicate story", "https://example.com/dup", "text about Fighter A", [])
    # Pre-existing row with the same (fighter, link) the unique constraint is on.
    db.add(SocialPost(fighter=f.a, source="RSS", title="first time", link=dup.link, text="...", flags=[], sentiment=0))
    db.commit()

    # A later analysis cycle re-fetches the same item (stale `seen`, or a genuine race) alongside a new one.
    fresh = NewsItem("RSS", "A brand new story", "https://example.com/new", "more text about Fighter A", [])
    S._store_posts(db, f, FA(news_a=[dup, fresh], news_b=[]))
    db.commit()  # must not raise - the duplicate must not poison this transaction

    links = {row.link for row in db.query(SocialPost).filter(SocialPost.fighter == f.a)}
    assert links == {dup.link, fresh.link}  # the duplicate stayed as-is, the new one was added
