from datetime import date, datetime

from sqlmodel import select

from fakes import FakeClock, FakeSource, add_listing, make_posting as p
from jobhunt.models import Job, Match
from jobhunt.runner import run_daily
from jobhunt.sources.base import SourceError
from jobhunt.sources.feeds import CombinedSource, hn_whos_hiring, himalayas, matches_keywords, remoteok

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


def test_combined_source_adds_matches_and_survives_feed_errors():
    calls = []
    primary = FakeSource({"php remote": [[p("1", title="LinkedIn PHP Dev")]]})
    combined = CombinedSource(primary, get=fake_get(calls), enabled=lambda: ("remoteok", "himalayas"), clock=lambda: NOW)
    found = combined.search("php remote", "europe", 200, 168, 100)
    assert {x.source for x in found} == {"linkedin", "remoteok", "himalayas"} and len(found) == 4
    combined.search("php remote", "europe", 200, 168, 100)
    assert calls.count("https://remoteok.com/api") == 1  # fetched once, then cached

    def broken(url, **kw):
        raise SourceError("down")
    failing = CombinedSource(FakeSource({"php remote": [[p("1")]]}), get=broken, enabled=lambda: ("remoteok",),
                             clock=lambda: NOW)
    assert [x.external_id for x in failing.search("php remote", "europe", 200, 168, 100)] == ["1"]
    assert "remoteok" in failing.errors


def test_feed_jobs_are_stored_with_description_and_not_duplicated(session_factory):
    sf = session_factory
    add_listing(sf, "A", keywords="php remote")
    primary = FakeSource({"php remote": [[p("1", title="Senior PHP Developer", company="Huzzle")]]})
    combined = CombinedSource(primary, get=fake_get(), enabled=lambda: ("remoteok", "himalayas"), clock=lambda: NOW)
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
