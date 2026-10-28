from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest
import requests

from jobhunt.sources.base import JobPosting, SourceBlocked, SourceError
from jobhunt.sources.linkedin import LinkedInSource, fetch_description, km_to_miles, postings_from_frame

COLUMNS = ["id", "site", "job_url", "title", "company", "location", "date_posted"]
GOOD = {"id": "li-4012345678", "site": "linkedin",
        "job_url": "https://www.linkedin.com/jobs/view/4012345678", "title": "Python Developer",
        "company": "Acme", "location": "Berlin, BE, Germany", "date_posted": date(2026, 10, 27)}


def frame(*rows, error=None):
    df = pd.DataFrame(list(rows), columns=COLUMNS)
    df.attrs = {"errors": {"linkedin": error} if error else {}, "warnings": {}}
    return df


def test_km_to_miles():
    assert [km_to_miles(k) for k in (0, 10, 25, 100, 200)] == [0, 6, 16, 62, 124]


def test_maps_fields():
    assert postings_from_frame(frame(GOOD)) == [JobPosting(
        "4012345678", "Python Developer", "Acme", "Berlin, BE, Germany",
        "https://www.linkedin.com/jobs/view/4012345678", date(2026, 10, 27))]


def test_missing_values_and_bad_rows():
    df = frame(
        {"id": None, "job_url": "https://www.linkedin.com/jobs/view/555?trk=x", "title": "Data Engineer",
         "company": float("nan"), "location": None, "date_posted": float("nan")},
        {"id": None, "job_url": "https://example.com/no-id", "title": "No ID"},
        {"id": "li-777", "job_url": "https://www.linkedin.com/jobs/view/777", "title": None},
    )
    assert postings_from_frame(df) == [JobPosting(
        "555", "Data Engineer", "", "", "https://www.linkedin.com/jobs/view/555", None)]


def test_search_passes_parameters():
    calls = []

    def scrape(**kw):
        calls.append(kw)
        return frame()

    assert LinkedInSource(scrape=scrape).search("python", "Berlin", 25, 48, 100) == []
    assert calls == [dict(site_name=["linkedin"], search_term="python", location="Berlin", distance=16,
                          hours_old=48, results_wanted=100, fetch_description=False)]


@pytest.mark.parametrize("message", ["response status code 429 (blocked: use a proxy)",
                                     "unexpected page with no jobs (blocked?)"])
def test_reported_block_raises_blocked_even_with_partial_results(message):
    source = LinkedInSource(scrape=lambda **kw: frame(GOOD, error=message))
    with pytest.raises(SourceBlocked, match="rate limited by LinkedIn"):
        source.search("python", "Berlin", 25, 48, 100)


def test_other_reported_error_raises_source_error():
    source = LinkedInSource(scrape=lambda **kw: frame(error="Connection reset by peer"))
    with pytest.raises(SourceError, match="Connection reset") as exc:
        source.search("python", "Berlin", 25, 48, 100)
    assert not isinstance(exc.value, SourceBlocked)


def test_exception_is_wrapped():
    def scrape(**kw):
        raise ValueError("boom")

    with pytest.raises(SourceError, match="boom"):
        LinkedInSource(scrape=scrape).search("python", "Berlin", 25, 48, 100)


JOB_HTML = ('<html><body><div class="show-more-less-html__markup show-more-less-html__markup--clamp">'
            '<p>Build <strong>PHP</strong> APIs.</p><ul><li>Laravel</li><li>Vue</li></ul></div></body></html>')


def page(status=200, url="https://www.linkedin.com/jobs/view/123", text=JOB_HTML):
    return SimpleNamespace(status_code=status, url=url, text=text)


def test_fetch_description_returns_markdown():
    urls = []

    def get(url, **kw):
        urls.append(url)
        return page()

    text = fetch_description("123", get=get)
    assert urls == ["https://www.linkedin.com/jobs/view/123"]
    assert "Build **PHP** APIs." in text and "Laravel" in text and "<p>" not in text


@pytest.mark.parametrize("status", [403, 429])
def test_fetch_description_blocked(status):
    with pytest.raises(SourceBlocked, match="rate limited by LinkedIn"):
        fetch_description("123", get=lambda url, **kw: page(status=status))


@pytest.mark.parametrize("response, message", [
    (page(status=404), "status 404"),
    (page(url="https://www.linkedin.com/jobs/search?x=1"), "not available"),
    (page(text="<html></html>"), "no description"),
])
def test_fetch_description_errors(response, message):
    with pytest.raises(SourceError, match=message) as exc:
        fetch_description("123", get=lambda url, **kw: response)
    assert not isinstance(exc.value, SourceBlocked)


def test_fetch_description_network_error():
    def get(url, **kw):
        raise requests.ConnectionError("connection reset")

    with pytest.raises(SourceError, match="connection reset"):
        fetch_description("123", get=get)
