from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from sqlmodel import update

from fakes import FakeClock, FakeSource, add_listing, job_id, make_posting as p
from jobhunt.digest import build_digest, push_digest
from jobhunt.models import Contact, Job
from jobhunt.preferences import Preferences, load_preferences
from jobhunt.runner import run_daily
from jobhunt.timeutil import local_date, utcnow

UTC_TZ = ZoneInfo("UTC")


def seeded(sf, now):
    add_listing(sf, "A", keywords="python")
    clock = FakeClock(now)
    run_daily(sf, FakeSource({"python": [[p("1", title="Good Job", location="Belgrade, Serbia"),
                                          p("2", title="UK Job", location="London, England, UK", is_remote=True),
                                          p("3", title="Blocked Job", location="Belgrade, Serbia")]]}),
              trigger="manual", clock=clock, sleep=clock.sleep)
    with sf() as s:
        s.exec(update(Job).where(Job.external_id == "3").values(blocked_by="Company blocked"))
        s.commit()


def test_digest_contents(session_factory):
    sf = session_factory
    now = utcnow()
    seeded(sf, now)
    with sf() as s:
        s.add(Contact(name="Ana", company="Acme", follow_up_on=local_date(now, UTC_TZ)))
        s.commit()
        digest = build_digest(s, Preferences(), now - timedelta(hours=24), now, local_date(now, UTC_TZ),
                              UTC_TZ)
        assert [j.title for j in digest.new_jobs] == ["Good Job"]
        assert [c.name for c in digest.contacts_due] == ["Ana"]
        assert digest.summary() == "1 new job you can take · 1 contact to follow up"
        later = build_digest(s, Preferences(), now + timedelta(minutes=1), now, local_date(now, UTC_TZ),
                             UTC_TZ)
        assert later.new_jobs == []


def test_push_only_when_configured_and_not_empty(session_factory):
    sf = session_factory
    now = utcnow()
    seeded(sf, now)
    calls = []
    post = lambda url, **kw: calls.append((url, kw))
    tz = UTC_TZ
    with sf() as s:
        digest = build_digest(s, Preferences(), now - timedelta(hours=24), now, local_date(now, tz), tz)
        empty = build_digest(s, Preferences(), now + timedelta(hours=1), now, local_date(now, tz), tz)
    assert push_digest(Preferences(), digest, "http://127.0.0.1:8765", post) is False
    assert push_digest(Preferences(ntfy_url="https://ntfy.sh/x"), empty, "http://127.0.0.1:8765", post) is False
    assert push_digest(Preferences(ntfy_url="https://ntfy.sh/x"), digest, "http://127.0.0.1:8765", post) is True
    url, kw = calls[0]
    assert url == "https://ntfy.sh/x" and kw["data"] == "1 new job you can take".encode()
    assert kw["headers"]["Click"] == "http://127.0.0.1:8765/digest"


def test_digest_page_and_mark_read(app_factory):
    app = app_factory()
    with TestClient(app) as c:
        seeded(app.state.session_factory, utcnow())
        page = c.get("/digest").text
        assert "Good Job" in page and "UK Job" not in page and "Blocked Job" not in page
        assert c.post("/digest/read", follow_redirects=False).status_code == 303
        with app.state.session_factory() as s:
            assert load_preferences(s).last_digest_at
        assert "Good Job" not in c.get("/digest").text
        assert 'href="/digest"' in c.get("/").text
