import socket

import pytest
from fakes import FakeResponse, url_get
from test_rss import WORDPRESS
from test_watchlist import GREENHOUSE

from jobhunt.site_check import (ADDRESS, BLOCKED, NO_HOST, NO_JOBS, NOTHING, PRIVATE, ROBOTS_DISALLOWED,
                                ROBOTS_UNREADABLE, TERMS, Refused, check_saving, check_site, normalise_url)
from jobhunt.sources.feeds import USER_AGENT

PUBLIC = lambda host, *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))]
PRIVATE_NET = lambda host, *a, **k: [(2, 1, 6, "", ("10.0.0.5", 0))]
OPEN = FakeResponse(text="User-agent: *\nDisallow:\n")


def refused(url, get=None, resolve=PUBLIC, name=""):
    with pytest.raises(Refused) as e:
        check_site(url, name, get or url_get({}), resolve)
    return str(e.value)


def test_address_rules():
    assert refused("ftp://example.com/feed") == ADDRESS
    assert refused("https://intranet.example/feed", resolve=PRIVATE_NET) == PRIVATE
    assert normalise_url("  larajobs.com/feed ") == "https://larajobs.com/feed"  # Review Focus 1
    assert normalise_url("example.com/go?to=https://x") == "https://example.com/go?to=https://x"
    assert normalise_url("HTTP://example.com/feed") == "HTTP://example.com/feed"


def test_a_malformed_address_is_refused_not_an_error():
    assert refused("https://[::1/feed") == ADDRESS
    with pytest.raises(Refused) as e:
        check_saving("[::1/feed", url_get({}), PUBLIC)
    assert str(e.value) == ADDRESS

    def overlong(host, *a, **k):
        raise UnicodeError("label empty or too long")  # what getaddrinfo raises for a DNS label over 63 characters
    assert refused("https://" + "a" * 70 + ".example.com/feed", resolve=overlong) == NO_HOST


def test_a_malformed_address_in_the_page_is_passed_over():
    script = FakeResponse(text='<html><script>var pattern = "//[a-z]+";</script></html>')
    link = FakeResponse(text='<link rel="alternate" type="application/rss+xml" href="http://[bad/feed">')
    for page in (script, link):
        assert refused("https://example.com/careers", url_get({"https://example.com/robots.txt": OPEN,
                                                               "https://example.com/careers": page})) == NOTHING


def test_an_unknown_host_is_not_found():
    def nowhere(host, *a, **k):
        raise socket.gaierror("no such host")
    assert refused("https://nowhere.example/feed", resolve=nowhere) == NO_HOST == "Couldn't find that site."


@pytest.mark.parametrize("url", [
    "https://www.linkedin.com/jobs/search?keywords=php", "https://rs.indeed.com/jobs?q=php",
    "https://www.glassdoor.co.uk/Job/x", "https://www.jobs.bg/front_job_search.php", "https://karieri.bg/",
    "https://joberty.com/", "https://www.joberty.rs/", "https://remotive.com/remote-jobs/feed",
    "https://wellfound.com/jobs", "https://www.welcometothejungle.com/en/jobs", "https://jobgether.com/",
    "https://vuejobs.com/feed", "https://www.jobs.bg./x", "https://linkedin.com./jobs"])
def test_terms_refusal_list(url):
    assert refused(url) == TERMS


def test_robots():
    feed = FakeResponse(content=WORDPRESS)  # from test_rss
    rules = FakeResponse(text="User-agent: *\nDisallow: /jobs?q=\n")
    assert refused("https://example.com/jobs?q=php", url_get({"https://example.com/robots.txt": rules})) == ROBOTS_DISALLOWED
    assert check_site("https://example.com/jobs", "", url_get({"https://example.com/robots.txt": rules,
                                                               "https://example.com/jobs": feed}), PUBLIC).kind == "feed"
    assert refused("https://example.com/feed", url_get({"https://example.com/robots.txt": FakeResponse(status=503)})) == ROBOTS_UNREADABLE
    assert refused("https://example.com/feed", url_get({"https://example.com/robots.txt": OSError("timeout")})) == ROBOTS_UNREADABLE
    calls = []
    check_site("https://example.com/feed", "", url_get({"https://example.com/robots.txt": FakeResponse(status=404),
                                                        "https://example.com/feed": feed}, calls), PUBLIC)
    assert calls[0][0] == "https://example.com/robots.txt" and calls[0][2]["User-Agent"] == USER_AGENT


@pytest.mark.parametrize("answer, reason", [
    (FakeResponse(status=403), BLOCKED), (FakeResponse(status=429), BLOCKED), (FakeResponse(status=401), BLOCKED),
    (FakeResponse(text="<title>Just a moment...</title>"), BLOCKED), (FakeResponse(text='<div id="cf-challenge">'), BLOCKED),
    (FakeResponse(status=500), "The site answered 500."), (ConnectionError("refused"), "Couldn't reach the site: refused"),
    (FakeResponse(text="<html><title>Acme</title><p>Hello</p></html>"), NOTHING),
    (FakeResponse(content=b'<rss version="2.0"><channel><title>Empty</title></channel></rss>'), NO_JOBS)])
def test_test_fetch_and_recognition(answer, reason):
    assert refused("https://example.com/page", url_get({"https://example.com/robots.txt": OPEN,
                                                        "https://example.com/page": answer})) == reason


def test_a_redirect_is_checked_like_the_address():  # Review Focus 2
    moved = FakeResponse(content=WORDPRESS, url="https://www.linkedin.com/jobs/feed")
    assert refused("https://example.com/feed", url_get({"https://example.com/robots.txt": OPEN,
                                                        "https://example.com/feed": moved})) == TERMS
    inside = FakeResponse(content=WORDPRESS, url="https://intranet.example/feed")
    resolve = lambda host, *a, **k: PRIVATE_NET(host) if host == "intranet.example" else PUBLIC(host)
    assert refused("https://example.com/feed", url_get({"https://example.com/robots.txt": OPEN,
                                                        "https://example.com/feed": inside}), resolve) == PRIVATE


def test_a_redirect_is_judged_before_the_status_and_challenge_are_read():
    def fetch(answer, resolve=PUBLIC):
        return refused("https://example.com/feed", url_get({"https://example.com/robots.txt": OPEN,
                                                            "https://example.com/feed": answer}), resolve)
    assert fetch(FakeResponse(status=503, text="<title>Just a moment...</title>")) == BLOCKED
    assert fetch(FakeResponse(status=999, url="https://www.linkedin.com/jobs")) == TERMS
    resolve = lambda host, *a, **k: PRIVATE_NET(host) if host == "intranet.example" else PUBLIC(host)
    assert fetch(FakeResponse(status=404, url="https://intranet.example/feed"), resolve) == PRIVATE


def test_a_feed_is_previewed_with_five_jobs_and_its_title():
    items = "".join(f"<item><title>PHP Dev {n}</title><link>https://example.com/{n}</link></item>" for n in range(7))
    content = f'<rss version="2.0"><channel><title>Example jobs</title>{items}</channel></rss>'.encode()
    found = check_site("example.com/feed", "", url_get({"https://example.com/robots.txt": OPEN,
                                                        "https://example.com/feed": FakeResponse(content=content)}), PUBLIC)
    assert (found.kind, found.url, found.name, len(found.jobs)) == ("feed", "https://example.com/feed", "Example jobs", 5)


def test_a_page_advertising_a_feed_uses_the_feed_and_checks_its_robots():
    page = FakeResponse(text='<link rel="alternate" type="application/rss+xml" href="/feed/">')
    routes = {"https://example.com/robots.txt": OPEN, "https://example.com/jobs": page,
              "https://example.com/feed/": FakeResponse(content=WORDPRESS)}
    assert check_site("https://example.com/jobs", "Mine", url_get(routes), PUBLIC).url == "https://example.com/feed/"
    routes["https://example.com/robots.txt"] = FakeResponse(text="User-agent: *\nDisallow: /feed/\n")
    assert refused("https://example.com/jobs", url_get(routes)) == ROBOTS_DISALLOWED


def test_careers_pages():
    lever = [{"id": "ab-1", "text": "PHP Developer", "categories": {"location": "Belgrade"},
              "hostedUrl": "https://jobs.lever.co/acme-co/ab-1", "createdAt": 1792500000000}]
    routes = {"https://jobs.lever.co/robots.txt": OPEN, "https://jobs.lever.co/acme-co": FakeResponse(text="<html></html>"),
              "https://api.lever.co/v0/postings/acme-co": lever}
    found = check_site("https://jobs.lever.co/acme-co", "", url_get(routes), PUBLIC)
    assert (found.kind, found.token, found.name, len(found.jobs)) == ("lever", "acme-co", "Acme Co", 1)
    embed = FakeResponse(text='<title>Acme</title><script src="https://boards.greenhouse.io/embed/job_board/js?for=acme">')
    routes = {"https://acme.example/robots.txt": OPEN, "https://acme.example/careers": embed,
              "https://boards-api.greenhouse.io/v1/boards/acme/jobs": GREENHOUSE}  # sample from test_watchlist
    found = check_site("https://acme.example/careers", "", url_get(routes), PUBLIC)
    assert (found.kind, found.token, found.name, found.url) == ("greenhouse", "acme", "Acme", "https://acme.example/careers")
    assert check_site("https://acme.example/careers", "Acme Corp", url_get(routes), PUBLIC).name == "Acme Corp"


def test_saving_repeats_only_the_first_three_checks():
    calls = []
    check_saving("https://example.com/feed", url_get({"https://example.com/robots.txt": OPEN}, calls), PUBLIC)
    assert [c[0] for c in calls] == ["https://example.com/robots.txt"]
