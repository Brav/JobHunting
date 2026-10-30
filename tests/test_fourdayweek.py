from datetime import date

from fakes import url_get

from jobhunt.places import country_name
from jobhunt.sources.feeds import USER_AGENT
from jobhunt.sources.fourdayweek import four_day_week

FOURDAYWEEK = {"data": [
    {"id": 9001, "title": "Senior PHP Developer", "url": "https://4dayweek.io/remote-job/senior-php-developer-acme",
     "company": {"name": "Acme"}, "work_arrangement": "remote",
     "locations": [{"country": c, "continent": "Europe", "work_arrangement": "remote"}
                   for c in ("Serbia", "Portugal", "Spain", "Poland")],
     "skills": [{"name": "PHP"}, {"name": "Laravel"}], "salary_min": 6000000, "salary_max": 8000000,
     "salary_currency": "EUR", "salary_period": "year", "posted_at": "2026-10-27T10:00:00Z",
     "description": "Build Laravel APIs."},
    {"id": 9002, "title": "Fullstack Engineer", "url": "https://4dayweek.io/job/9002", "company": {"name": "Beta"},
     "work_arrangement": "hybrid", "locations": [{"country": "Germany", "city": "Berlin", "work_arrangement": "hybrid"}],
     "skills": [], "salary_min": None, "salary_max": None, "posted_at": "2026-10-26T10:00:00Z", "description": "Vue."},
    {"id": 9003, "title": "Vue Developer", "url": "https://4dayweek.io/job/9003", "company": {"name": "Gamma"},
     "work_arrangement": "remote", "locations": [{"continent": "Europe"}], "skills": [], "posted_at": None,
     "description": "Vue."}],
    "page": 1, "limit": 100, "total": 3, "has_more": False}


def test_four_day_week_postings():
    calls = []
    jobs = four_day_week("php remote serbia", "RS", url_get({"https://4dayweek.io/api/v2/jobs": FOURDAYWEEK}, calls))
    assert calls[0][1] == {"q": "php", "country": "Serbia", "limit": 100}
    assert calls[0][2]["User-Agent"] == USER_AGENT
    acme, beta, gamma = (j.posting for j in jobs)
    assert (acme.external_id, acme.company, acme.is_remote) == ("4dayweek:9001", "Acme", True)
    assert acme.location == "Remote: Serbia, Portugal, Spain +1 more"
    assert acme.salary_text == "€60,000–€80,000 a year" and acme.posted_at == date(2026, 10, 27)
    assert jobs[0].tags == ("PHP", "Laravel") and acme.source == "4dayweek"
    assert beta.location == "Berlin, Germany (hybrid)" and beta.salary_text is None
    assert gamma.location == "Remote: Europe" and gamma.posted_at is None


def test_no_home_country_leaves_the_parameter_out():
    calls = []
    four_day_week("php", "", url_get({"https://4dayweek.io/api/v2/jobs": FOURDAYWEEK}, calls))
    assert "country" not in calls[0][1]


def test_no_search_words_makes_no_request():
    calls = []
    assert four_day_week("remote serbia", "RS", url_get({}, calls)) == []
    assert calls == []


def test_country_name():
    assert [country_name(c) for c in ("RS", "GB", "US", "NL", "")] == [
        "Serbia", "United Kingdom", "United States", "Netherlands", ""]
