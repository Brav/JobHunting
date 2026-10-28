from datetime import date, datetime
from urllib.parse import quote

from fastapi.testclient import TestClient

from fakes import FakeClock, FakeSource, add_listing, make_posting as p
from jobhunt.blocks import company_key
from jobhunt.runner import run_daily
from jobhunt.sources.feeds import CombinedSource
from jobhunt.timeutil import utcnow
from jobhunt.watchlist import detect_board, fetch_board, slug_candidates

NOW = datetime(2026, 10, 28, 9, 0)
GREENHOUSE = {"jobs": [{"id": 42, "title": "Senior PHP Engineer", "location": {"name": "Remote, Europe"},
                        "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/42",
                        "first_published": "2026-10-20T10:00:00-04:00", "content": "&lt;p&gt;Laravel and Vue.&lt;/p&gt;"}]}
LEVER = [{"id": "ab-1", "text": "PHP Developer", "categories": {"location": "Belgrade", "commitment": "Full time"},
          "hostedUrl": "https://jobs.lever.co/acme-co/ab-1", "createdAt": 1792500000000, "workplaceType": "remote",
          "descriptionPlain": "Build PHP services."}]
ASHBY = {"jobs": [{"id": "x-1", "title": "Backend Engineer (PHP)", "location": "Remote - European Union", "isRemote": True,
                   "jobUrl": "https://jobs.ashbyhq.com/acme/x-1", "publishedAt": "2026-10-21T14:29:08.532+00:00",
                   "descriptionPlain": "PHP 8.", "compensation": {"compensationTierSummary": "€60K – €80K"}}]}


class Response:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status

    def json(self):
        return self.data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"status {self.status_code}")


def board_get(boards):
    """boards: {(ats, token): data}; anything else answers 404."""
    def get(url, params=None, **kw):
        for (ats, token), data in boards.items():
            if ats in url and f"/{token}" in url:
                return Response(data)
        return Response({"error": "not found"}, 404)
    return get


def test_parsers():
    (gh,) = fetch_board("greenhouse", "acme", "Acme", board_get({("greenhouse", "acme"): GREENHOUSE}))
    assert gh.posting.external_id == "greenhouse:acme:42" and gh.posting.location == "Remote, Europe"
    assert gh.posting.posted_at == date(2026, 10, 20) and "Laravel" in gh.posting.description and gh.posting.company == "Acme"
    (lv,) = fetch_board("lever", "acme-co", "Acme", board_get({("lever", "acme-co"): LEVER}))
    assert lv.posting.title == "PHP Developer" and lv.posting.location == "Belgrade (remote)" and lv.posting.source == "lever"
    (ab,) = fetch_board("ashby", "acme", "Acme", board_get({("ashby", "acme"): ASHBY}))
    assert ab.posting.salary_text == "€60K – €80K" and ab.posting.location == "Remote - European Union"


def test_detect_board():
    assert slug_candidates("Acme & Co") == ["acmeco", "acme-co", "acme"]
    assert detect_board("Acme & Co", board_get({("lever", "acme-co"): LEVER})) == ("lever", "acme-co")
    assert detect_board("Nobody", board_get({})) is None


def test_watched_boards_join_the_search(session_factory):
    sf = session_factory
    add_listing(sf, "A", keywords="php remote")
    combined = CombinedSource(FakeSource({"php remote": [[]]}), get=board_get({("greenhouse", "acme"): GREENHOUSE}),
                              enabled=lambda: (), watched=lambda: [("greenhouse", "acme", "Acme")], clock=lambda: NOW)
    found = combined.search("php remote", "europe", 200, 168, 100)
    assert [x.external_id for x in found] == ["greenhouse:acme:42"]


def test_watch_from_the_company_page(app_factory):
    app = app_factory()
    app.state.http_get = board_get({("greenhouse", "acme"): GREENHOUSE})
    with TestClient(app) as c:
        sf = app.state.session_factory
        add_listing(sf, "A", keywords="python")
        clock = FakeClock(utcnow())
        run_daily(sf, FakeSource({"python": [[p("1", company="Acme")]]}), trigger="manual", clock=clock, sleep=clock.sleep)
        url = f"/companies/{quote(company_key('Acme'))}"
        assert "Watch for new jobs" in c.get(url).text
        c.post(f"{url}/watch", data={"name": "Acme"})
        assert "Watching Greenhouse board “acme”" in c.get(url).text
        c.post(f"{url}/unwatch")
        assert "Watch for new jobs" in c.get(url).text
        r = c.post(f"{url}/watch", data={"name": "Acme", "ats": "lever", "token": "nope"})
        assert "find a Greenhouse, Lever or Ashby board" in r.text
