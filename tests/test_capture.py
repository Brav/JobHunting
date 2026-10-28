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


def test_greenhouse_pages_are_read_from_its_api():
    from jobhunt.capture import fetch_and_parse

    calls = []
    data = {"absolute_url": "https://job-boards.greenhouse.io/gitlab/jobs/8638232002", "title": "Backend Engineer",
            "company_name": "GitLab", "location": {"name": "Remote, EMEA"}, "first_published": "2026-07-22T13:38:40-04:00",
            "content": "&lt;p&gt;Build &lt;b&gt;Rails&lt;/b&gt; services.&lt;/p&gt;"}

    def get(url, **kw):
        calls.append(url)
        return type("R", (), {"status_code": 200, "json": lambda self: data, "text": ""})()
    job, error = fetch_and_parse("https://job-boards.greenhouse.io/gitlab/jobs/8638232002?gh_src=abc", get)
    assert calls == ["https://boards-api.greenhouse.io/v1/boards/gitlab/jobs/8638232002"] and error is None
    assert (job["title"], job["company"], job["location"], job["posted_at"]) == \
        ("Backend Engineer", "GitLab", "Remote, EMEA", "2026-07-22")
    assert job["remote"] and "Build **Rails** services." in job["description"]
    assert job["url"] == "https://job-boards.greenhouse.io/gitlab/jobs/8638232002?gh_src=abc"


def test_a_captured_job_without_text_is_not_looked_up_on_linkedin(app_factory):
    calls = []
    app = app_factory(fetch_page=lambda external_id: calls.append(external_id))
    with TestClient(app) as c:
        token = c.get("/capture").text and app.state.form_token
        r = c.post("/capture/save", data={"token": token, "title": "Dev", "company": "Example",
                                          "url": "https://example.com/job/1"}, follow_redirects=False)
        job_url = r.headers["location"]
        assert "No description saved" in c.get(job_url).text
        c.get(f"{job_url}/cv-prompt")
        c.post(f"{job_url}/description")
    assert calls == []


def test_only_web_links_from_job_data_become_links(app_factory):
    app = app_factory()
    with TestClient(app) as c:
        with app.state.session_factory() as s:
            s.add(Job(external_id="remoteok:1", source="remoteok", title="Dev", company="Evil", location="",
                      url="javascript:alert(1)", apply_url="javascript:fetch('/data/export.zip')",
                      description="Text."))
            s.commit()
            (job,) = s.exec(select(Job)).all()
        for page in (f"/jobs/{job.id}", f"/jobs/{job.id}/cv-prompt", f"/focus?job={job.id}"):
            assert 'href="javascript:' not in c.get(page).text
        r = c.post("/capture/save", data={"token": app.state.form_token, "title": "Dev", "company": "Acme",
                                          "url": "https://acme.example/jobs/1", "apply_url": "javascript:alert(1)"},
                   follow_redirects=False)
        with app.state.session_factory() as s:
            saved = s.get(Job, int(r.headers["location"].rsplit("/", 1)[1]))
        assert saved.apply_url is None
        r = c.post("/capture/save", data={"token": app.state.form_token, "title": "Dev", "company": "Acme",
                                          "url": "javascript:alert(1)"})
        assert r.status_code == 422 and "web address" in r.text
