from datetime import date, datetime
from urllib.parse import quote

from fastapi.testclient import TestClient

import pytest

from fakes import FakeClock, FakeResponse, FakeSource, add_listing, make_posting as p, url_get
from jobhunt.blocks import company_key
from jobhunt.runner import run_daily
from jobhunt.sites import OwnSite
from jobhunt.sources.combined import CombinedSource
from jobhunt.timeutil import utcnow
from jobhunt.watchlist import board_from_url, board_in_page, detect_board, fetch_board, slug_candidates

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

TEAMTAILOR_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:tt="https://teamtailor.com/locations"><channel><title>Acme jobs</title>
<item><title>Node Developer</title><link>https://acme.teamtailor.com/jobs/1-node-developer</link>
<pubDate>Fri, 23 Oct 2026 10:00:00 +0000</pubDate><description>Node.</description>
<tt:locations><tt:location><tt:city>Belgrade</tt:city><tt:country>Serbia</tt:country></tt:location></tt:locations>
<remoteStatus>fully</remoteStatus></item></channel></rss>"""


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
                              own=lambda: [OwnSite("board:1", "Acme", "greenhouse", "https://job-boards.greenhouse.io/acme", "acme")],
                              clock=lambda: NOW)
    found = combined.search("php remote", "europe", 200, 168, 100, sites=("linkedin", "board:1"))
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


@pytest.mark.parametrize("url, expected", [
    ("https://boards.greenhouse.io/acme", ("greenhouse", "acme")),
    ("https://job-boards.greenhouse.io/acme/jobs/42", ("greenhouse", "acme")),
    ("https://jobs.lever.co/acme-co", ("lever", "acme-co")),
    ("https://jobs.ashbyhq.com/acme", ("ashby", "acme")),
    ("https://apply.workable.com/acme/", ("workable", "acme")),
    ("https://jobs.smartrecruiters.com/AcmeGroup", ("smartrecruiters", "AcmeGroup")),
    ("https://careers.smartrecruiters.com/AcmeGroup/", ("smartrecruiters", "AcmeGroup")),
    ("https://acme.recruitee.com/", ("recruitee", "acme")),
    ("https://acme.jobs.personio.de/", ("personio", "acme")),
    ("https://acme.jobs.personio.com/job/1", ("personio", "acme")),
    ("https://acme.teamtailor.com/jobs", ("teamtailor", "acme")),
    ("https://acme.breezy.hr/", ("breezy", "acme")),
    ("https://apply.workable.com/j/00C85BAC46", None),
    ("https://www.recruitee.com/", None),
    ("https://example.com/careers", None),
    ("https://jobs.lever.co./acme-co", ("lever", "acme-co")),
    ("https://acme.recruitee.com./", ("recruitee", "acme")),
    ("https://[a-z]+", None),
])
def test_board_from_url(url, expected):
    assert board_from_url(url) == expected


def test_board_in_page():
    assert board_in_page('<script src="https://boards.greenhouse.io/embed/job_board/js?for=acme"></script>') == (
        "greenhouse", "acme")
    assert board_in_page('<a href="https://jobs.lever.co/acme">Jobs</a>') == ("lever", "acme")
    assert board_in_page('<iframe src="//acme.recruitee.com/embed"></iframe>') == ("recruitee", "acme")
    assert board_in_page("<p>No jobs here</p>") is None
    assert board_in_page('<script>var p = "//[a-z]+";</script><a href="https://jobs.lever.co/acme">') == ("lever", "acme")


def test_new_parsers():
    workable = {"name": "Acme", "jobs": [{"title": "PHP Developer", "shortcode": "AB12", "city": "Belgrade",
                "country": "Serbia", "telecommuting": True, "url": "https://apply.workable.com/j/AB12",
                "published_on": "2026-10-20", "description": "<p>Laravel.</p>"}]}
    (w,) = fetch_board("workable", "acme", "Acme", url_get({"https://apply.workable.com/api/v1/widget/accounts/acme": workable}))
    assert (w.posting.external_id, w.posting.location, w.posting.source) == (
        "workable:acme:AB12", "Belgrade, Serbia (remote)", "workable")
    assert w.posting.posted_at == date(2026, 10, 20) and "Laravel" in w.posting.description
    smart = {"content": [{"id": "744000158334825", "name": "Backend Developer", "releasedDate": "2026-10-21T09:00:00.000Z",
             "location": {"city": "Belgrade", "country": "rs", "remote": False, "hybrid": True,
                          "fullLocation": "Belgrade, Serbia"}}]}
    (sr,) = fetch_board("smartrecruiters", "AcmeGroup", "Acme",
                        url_get({"https://api.smartrecruiters.com/v1/companies/AcmeGroup/postings": smart}))
    assert sr.posting.url == "https://jobs.smartrecruiters.com/AcmeGroup/744000158334825"
    assert sr.posting.location == "Belgrade, Serbia (hybrid)"
    recruitee = {"offers": [{"id": 7, "title": "Vue Developer", "careers_url": "https://acme.recruitee.com/o/vue-developer",
                 "city": "Novi Sad", "country": "Serbia", "remote": True, "hybrid": False,
                 "published_at": "2026-10-22 08:00:00 UTC", "description": "<p>Nuxt.</p>"}]}
    (rc,) = fetch_board("recruitee", "acme", "Acme", url_get({"https://acme.recruitee.com/api/offers/": recruitee}))
    assert (rc.posting.location, rc.posting.posted_at) == ("Novi Sad, Serbia (remote)", date(2026, 10, 22))
    personio = [{"id": 1234, "name": "Fullstack Developer", "office": "Belgrade", "employment_type": "permanent"}]
    (pe,) = fetch_board("personio", "acme", "Acme", url_get({"https://acme.jobs.personio.com/search.json": personio}))
    assert (pe.posting.url, pe.posting.location, pe.posting.posted_at) == (
        "https://acme.jobs.personio.com/job/1234", "Belgrade", None)
    (tt,) = fetch_board("teamtailor", "acme", "Acme",
                        url_get({"https://acme.teamtailor.com/jobs.rss": FakeResponse(content=TEAMTAILOR_RSS)}))
    assert tt.posting.location == "Belgrade, Serbia (remote)" and tt.posting.source == "teamtailor"
    breezy = [{"id": "x1", "name": "Node Developer", "url": "https://acme.breezy.hr/p/x1-node-developer",
               "published_date": "2026-10-23T10:00:00.000Z",
               "location": {"city": "Belgrade", "country": {"name": "Serbia"}, "is_remote": False}}]
    (br,) = fetch_board("breezy", "acme", "Acme", url_get({"https://acme.breezy.hr/json": breezy}))
    assert (br.posting.external_id, br.posting.location) == ("breezy:acme:x1", "Belgrade, Serbia")


def test_detect_board_guesses_only_greenhouse_lever_and_ashby():
    calls = []
    detect_board("Acme", url_get({}, calls))
    assert calls and all(any(h in url for h in ("greenhouse", "lever", "ashby")) for url, _, _ in calls)
