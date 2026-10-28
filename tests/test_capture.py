import json

from fastapi.testclient import TestClient
from sqlmodel import select

from jobhunt.capture import extract_jsonld, job_from_jsonld
from jobhunt.models import Job

POSTING = {"@context": "https://schema.org", "@graph": [
    {"@type": "Organization", "name": "Ignore me"},
    {"@type": "JobPosting", "title": "Senior PHP Developer", "datePosted": "2026-10-20",
     "description": "<p>Build <b>Laravel</b> APIs.</p>",
     "hiringOrganization": {"@type": "Organization", "name": "Acme", "sameAs": "https://acme.example"},
     "jobLocationType": "TELECOMMUTE",
     "applicantLocationRequirements": [{"@type": "Country", "name": "Germany"}, {"@type": "Country", "name": "Serbia"}],
     "jobLocation": [{"@type": "Place", "address": {"addressLocality": "Berlin", "addressCountry": "DE"}}],
     "baseSalary": {"@type": "MonetaryAmount", "currency": "EUR",
                    "value": {"@type": "QuantitativeValue", "minValue": 60000, "maxValue": 75000, "unitText": "YEAR"}},
     "url": "https://jobs.acme.example/42", "employmentType": "FULL_TIME"}]}


def test_job_from_jsonld():
    job = job_from_jsonld([json.dumps(POSTING)], "https://acme.example/careers/42")
    assert (job["title"], job["company"], job["location"]) == ("Senior PHP Developer", "Acme", "Remote: Germany, Serbia")
    assert job["salary_text"] == "€60,000–€75,000 a year" and job["posted_at"] == "2026-10-20"
    assert "Laravel" in job["description"] and job["apply_url"] == "https://jobs.acme.example/42" and job["remote"]
    onsite = {"@type": "JobPosting", "title": "Dev", "hiringOrganization": "Globex",
              "jobLocation": {"address": {"addressLocality": "Madrid", "addressCountry": {"name": "Spain"}}}}
    assert job_from_jsonld([json.dumps([onsite])], "u")["location"] == "Madrid, Spain"
    assert job_from_jsonld(["not json", json.dumps({"@type": "Article"})], "u") is None
    html = f'<html><head><script type="application/ld+json">{json.dumps(POSTING)}</script></head></html>'
    assert extract_jsonld(html) == [json.dumps(POSTING)]


def test_capture_confirm_and_save(app_factory):
    app = app_factory()
    page = f'<script type="application/ld+json">{json.dumps(POSTING)}</script>'
    app.state.http_get = lambda url, **kw: type("R", (), {"status_code": 200, "text": page})()
    with TestClient(app) as c:
        assert "javascript:" in c.get("/capture").text
        r = c.post("/capture", data={"url": "https://acme.example/careers/42", "title": "Acme careers",
                                     "ld": json.dumps(POSTING)})
        assert r.status_code == 200 and 'value="Senior PHP Developer"' in r.text
        token = r.text.split('name="token" value="')[1].split('"')[0]
        fields = {"title": "Senior PHP Developer", "company": "Acme", "location": "Remote: Germany, Serbia",
                  "url": "https://acme.example/careers/42", "apply_url": "https://jobs.acme.example/42",
                  "description": "Build Laravel APIs.", "posted_at": "2026-10-20", "salary_text": "€60,000–€75,000 a year",
                  "remote": "1"}
        assert c.post("/capture/save", data=fields).status_code == 403
        assert c.post("/capture/save", data={**fields, "token": "wrong"}).status_code == 403
        r = c.post("/capture/save", data={**fields, "token": token}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/jobs/")
        r2 = c.post("/capture/save", data={**fields, "token": token}, follow_redirects=False)
        assert r2.headers["location"] == r.headers["location"]  # the same page captured twice is one job
        with app.state.session_factory() as s:
            (job,) = s.exec(select(Job)).all()
        assert (job.status, job.source, job.salary_max_eur, job.eligibility) == ("interested", "capture", 75000, "eligible")
        fetched = c.post("/capture", data={"url": "https://acme.example/careers/42"})
        assert 'value="Senior PHP Developer"' in fetched.text


def test_capture_without_details(app_factory):
    app = app_factory()

    def down(url, **kw):
        raise ConnectionError("no network")
    app.state.http_get = down
    with TestClient(app) as c:
        r = c.post("/capture", data={"url": "https://example.com/job", "title": "  Python Developer | Example  "})
        assert "Couldn&#39;t fetch the page" in r.text and 'value="Python Developer | Example"' in r.text
        assert "Only web addresses" in c.post("/capture", data={"url": "file:///etc/passwd"}).text
        token = r.text.split('name="token" value="')[1].split('"')[0]
        r = c.post("/capture/save", data={"token": token, "title": "Python Developer", "url": "https://example.com/job"})
        assert r.status_code == 422 and "Fill in the company." in r.text
