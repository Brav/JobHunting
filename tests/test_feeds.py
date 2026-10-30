from datetime import date, datetime, timedelta

from sqlmodel import select

from fakes import FakeClock, FakeSource, add_listing, make_posting as p
from jobhunt.models import Job, Match
from jobhunt.preferences import load_feed_errors
from jobhunt.runner import run_daily
from jobhunt.sources.base import JobPosting, SourceError
from jobhunt.sources.combined import CombinedSource
from jobhunt.sources.feeds import (FeedPosting, hn_whos_hiring, himalayas, is_place_word, matches_keywords,
                                   remoteok, search_words)

NOW = datetime(2026, 10, 28, 9, 0)

REMOTEOK = [{"last_updated": "1", "legal": "API Terms of Service: Please link back"},
            {"id": "1137573", "date": "2026-10-26T08:00:12+00:00", "company": "Huzzle", "position": "Senior PHP Developer",
             "tags": ["php", "laravel", "senior"], "description": "<p>Build <b>Laravel</b> APIs. Remote worldwide.</p>",
             "location": "", "url": "https://remoteOK.com/remote-jobs/remote-php-huzzle-1137573",
             "apply_url": "https://remoteOK.com/remote-jobs/remote-php-huzzle-1137573", "salary_min": 60000, "salary_max": 80000}]
HIMALAYAS = {"jobs": [{"title": "Senior Fullstack Engineer", "companyName": "lemon.io", "employmentType": "Contractor",
                       "locationRestrictions": ["Albania", "Serbia", "Spain", "Poland"], "description": "<p>PHP and Vue.</p>",
                       "pubDate": "1792087770", "applicationLink": "https://himalayas.app/companies/lemon-io/jobs/x",
                       "guid": "https://himalayas.app/companies/lemon-io/jobs/x", "minSalary": None, "maxSalary": None,
                       "currency": "USD", "salaryPeriod": "annual"},
                      {"title": "PHP Developer", "companyName": "Anywhere Inc", "locationRestrictions": [],
                       "description": "<p>PHP.</p>", "pubDate": "1792087770", "applicationLink": "https://himalayas.app/y",
                       "guid": "https://himalayas.app/y", "minSalary": 50000, "maxSalary": 70000, "currency": "EUR",
                       "salaryPeriod": "annual"}]}
HN_SEARCH = {"hits": [{"objectID": "49922568", "title": "Ask HN: Who wants to be hired? (October 2026)"},
                      {"objectID": "49922569", "title": "Ask HN: Who is hiring? (October 2026)"}]}
HN_THREAD = {"id": 49922569, "children": [
    {"id": 49922584, "created_at": "2026-10-01T15:02:37.000Z",
     "text": "PrairieLearn (Remote US) — Full-Stack Software Engineer — TypeScript &#x2F; Postgres &#x2F; React<p>We build tools.</p>"},
    {"id": 49922600, "created_at": "2026-10-01T16:00:00.000Z",
     "text": "Acme | Senior PHP Engineer | Remote (EU) | Full-time<p>Laravel and Vue. Apply at jobs@acme.example</p>"},
    {"id": 49922601, "created_at": "2026-10-01T16:00:00.000Z", "text": None}]}


class FakeResponse:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status

    def json(self):
        return self.data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"status {self.status_code}")


def fake_get(calls=None):
    def get(url, params=None, **kw):
        if calls is not None:
            calls.append(url)
        if "remoteok" in url:
            return FakeResponse(REMOTEOK)
        if "himalayas" in url:
            return FakeResponse(HIMALAYAS)
        if "search_by_date" in url:
            return FakeResponse(HN_SEARCH)
        return FakeResponse(HN_THREAD)
    return get


def test_remoteok():
    (job,) = remoteok(fake_get())
    assert job.posting.external_id == "remoteok:1137573" and job.posting.source == "remoteok"
    assert (job.posting.title, job.posting.company, job.posting.posted_at) == ("Senior PHP Developer", "Huzzle", date(2026, 10, 26))
    assert "Laravel" in job.posting.description and job.posting.salary_text == "$60,000–$80,000 a year"
    assert job.posting.url.startswith("https://remoteOK.com/")


def test_himalayas_states_where_it_hires():
    serbia, anywhere = himalayas("php remote", fake_get())
    assert serbia.posting.location == "Remote: Albania, Serbia, Spain +1 more"
    assert "We hire in Albania, Serbia, Spain, Poland." in serbia.posting.description
    assert anywhere.posting.location == "Remote, worldwide" and "Remote worldwide." in anywhere.posting.description
    assert anywhere.posting.salary_text == "€50,000–€70,000 a year"


def test_hn_whos_hiring():
    prairie, acme = hn_whos_hiring(fake_get())
    assert (prairie.posting.company, prairie.posting.title, prairie.posting.location) == \
        ("PrairieLearn", "Full-Stack Software Engineer", "Remote US")
    assert (acme.posting.company, acme.posting.title, acme.posting.location) == ("Acme", "Senior PHP Engineer", "Remote (EU)")
    assert acme.posting.external_id == "hn:49922600" and acme.posting.url == "https://news.ycombinator.com/item?id=49922600"


def test_keyword_matching():
    php, fullstack = remoteok(fake_get())[0], himalayas("x", fake_get())[0]
    assert matches_keywords(php, "php remote") and not matches_keywords(php, "python remote")
    assert matches_keywords(fullstack, "fullstack developer remote")  # Fullstack Engineer: engineer ~ developer
    assert not matches_keywords(php, '"data engineer" NOT senior')


def test_feed_jobs_are_stored_with_description_and_not_duplicated(session_factory):
    sf = session_factory
    add_listing(sf, "A", keywords="php remote", sites='["linkedin", "remoteok", "himalayas"]')
    primary = FakeSource({"php remote": [[p("1", title="Senior PHP Developer", company="Huzzle")]]})
    combined = CombinedSource(primary, get=fake_get(), clock=lambda: NOW)
    clock = FakeClock(NOW)
    run_daily(sf, combined, trigger="manual", clock=clock, sleep=clock.sleep)
    with sf() as s:
        jobs = {j.title: j for j in s.exec(select(Job))}
        assert jobs["Senior PHP Developer"].source == "linkedin" and jobs["Senior PHP Developer"].external_id == "1"
        hima = jobs["PHP Developer"]
        assert hima.source == "himalayas" and hima.description and hima.salary_max_eur == 70000
        assert hima.eligibility == "eligible"  # "Remote worldwide."
        assert len(s.exec(select(Match)).all()) == 3


def test_keywords_must_be_near_the_top_and_not_in_links():
    from jobhunt.sources.base import JobPosting
    from jobhunt.sources.feeds import FeedPosting

    def feed(title, description, tags=()):
        return FeedPosting(JobPosting("x", title, "Lemon.io", "Remote", "u", None, description=description), tags)
    blurb = "We are a design studio. " * 30 + "Our clients hire PHP, Python and Go developers."
    assert not matches_keywords(feed("Senior UI Designer", blurb), "php remote")
    assert not matches_keywords(feed("Finance Manager", "Apply at https://example.com/index.php today."), "php remote")
    assert matches_keywords(feed("Backend Engineer", "You will build PHP 8 services with Laravel."), "php remote")


def test_feed_failures_are_kept_and_shown(app_factory):
    from fastapi.testclient import TestClient

    state = {"down": True}

    def flaky(url, **kw):
        if state["down"]:
            raise SourceError("Remote OK answered 503")
        return fake_get()(url, **kw)
    combined = CombinedSource(FakeSource({"php remote": [[p("1")]]}), get=flaky, clock=lambda: NOW, ttl=timedelta(0))
    app = app_factory(source=combined)
    with TestClient(app) as c:
        sf = app.state.session_factory
        add_listing(sf, "A", keywords="php remote")
        clock = FakeClock(NOW)
        run_daily(sf, combined, trigger="manual", clock=clock, sleep=clock.sleep)
        with sf() as s:
            assert load_feed_errors(s)["remoteok"]["error"] == "Remote OK answered 503"
        assert "Remote OK answered 503" in c.get("/settings").text
        assert "Remote OK isn't answering" in c.get("/").text
        state["down"] = False
        run_daily(sf, combined, trigger="manual", clock=clock, sleep=clock.sleep)
        with sf() as s:
            assert load_feed_errors(s) == {}
        assert "Remote OK isn't answering" not in c.get("/").text


def test_source_filter_and_page(app_factory):
    from fastapi.testclient import TestClient

    from jobhunt.filters import JobFilters, filtered_jobs
    from jobhunt.timeutil import utcnow
    app = app_factory()
    with TestClient(app) as c:
        sf = app.state.session_factory
        with sf() as s:
            s.add(Job(external_id="1", source=None, title="Old LinkedIn job", company="A", location="", url="u"))
            s.add(Job(external_id="remoteok:2", source="remoteok", title="Feed job", company="B", location="", url="u"))
            s.commit()
            titles = lambda f: sorted(j.title for j in filtered_jobs(s, f, utcnow())[0])
            assert titles(JobFilters(source="remoteok", blocked="show", eligibility=("check",))) == ["Feed job"]
            assert titles(JobFilters(source="linkedin", blocked="show", eligibility=("check",))) == ["Old LinkedIn job"]
        page = c.get("/jobs?source=remoteok&eligibility=check").text
        assert '<option value="remoteok" selected>Remote OK</option>' in page and "Source: Remote OK" in page
        assert "Old LinkedIn job" not in page and "Feed job" in page


def test_place_words():
    for word in ("serbia", "Srbija", "SOFIA", "Novi Sad", "софия", "EU", "Europe", "Niš", "nis", "Plovdiv"):
        assert is_place_word(word), word
    for word in ("php", "fullstack", "remote", "us", "developer", "laravel"):
        assert not is_place_word(word), word


def test_search_words_leave_out_places_remote_and_operators():
    assert search_words("php remote serbia srbija") == "php"
    assert search_words('fullstack developer remote "Novi Sad"') == "fullstack developer"
    assert search_words("serbia srbija remote") == ""


def test_place_words_are_not_required_but_not_still_excludes():
    def feed(title):
        return FeedPosting(JobPosting("x", title, "Acme", "", "u", None, description=""), ())
    assert matches_keywords(feed("Senior PHP Developer"), "php remote serbia srbija")
    assert matches_keywords(feed("PHP Developer"), '"Novi Sad" php')
    assert not matches_keywords(feed("PHP Developer in Bulgaria"), "php NOT bulgaria")
    assert not matches_keywords(feed("Sofia office manager"), "sofia remote")  # only place words: nothing


def test_himalayas_is_searched_without_place_words():
    seen = {}

    def get(url, params=None, **kw):
        seen.update(params or {})
        return FakeResponse(HIMALAYAS)
    himalayas("php remote serbia srbija", get)
    assert seen["q"] == "php"
