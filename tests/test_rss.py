from datetime import date

import pytest
from fakes import FakeResponse, url_get

from jobhunt.sources import rss
from jobhunt.sources.base import SourceError
from jobhunt.sources.feeds import USER_AGENT
from jobhunt.sources.rss import advertised_feed, feed_postings, feed_title, fetch_feed, is_feed, parse, split_title

WORDPRESS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:job_listing="https://example.com/job_listing"><channel><title>Acme Jobs</title>
<item><title>PHP Developer</title><link>https://jobs.example.com/job/php-developer/</link>
<guid isPermaLink="false">https://jobs.example.com/?p=11</guid>
<pubDate>Tue, 27 Oct 2026 10:00:00 +0000</pubDate>
<description>&lt;p&gt;Laravel and Vue.&lt;/p&gt;</description><category>PHP</category>
<job_listing:company>Acme</job_listing:company><job_listing:location>Belgrade</job_listing:location>
</item></channel></rss>"""

WWR = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>WWR</title>
<item><title>Acme: Senior PHP Engineer</title><link>https://example.com/remote/1</link>
<region>Anywhere in the World</region><pubDate>Mon, 26 Oct 2026 09:00:00 +0000</pubDate></item></channel></rss>"""

LARAJOBS = b"""<?xml version="1.0"?>
<rss version="2.0" xmlns:job="https://example.com/job"><channel><title>Lara</title>
<item><title>Laravel Developer</title><link>https://example.com/lara/1</link>
<job:company>Acme</job:company><job:location>Remote/USA</job:location></item></channel></rss>"""

TEAMTAILOR = b"""<?xml version="1.0"?>
<rss version="2.0" xmlns:tt="https://example.com/tt"><channel><title>Acme careers</title>
<item><title>Backend Developer</title><link>https://example.com/tt/1</link>
<tt:locations><tt:location><tt:city>Belgrade</tt:city><tt:country>Serbia</tt:country></tt:location></tt:locations>
</item></channel></rss>"""

ATOM = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Atom jobs</title>
<entry><title>Backend Developer at Acme (Remote, EU)</title><link href="https://example.com/jobs/1"/>
<id>tag:example.com,2026:1</id><updated>2026-10-26T08:00:00Z</updated>
<content type="html">&lt;p&gt;PHP.&lt;/p&gt;</content></entry></feed>"""


def test_wordpress_job_feed():
    (job,) = feed_postings(7, WORDPRESS)
    p = job.posting
    assert (p.external_id, p.title, p.company, p.location) == (
        "feed:7:https://jobs.example.com/?p=11", "PHP Developer", "Acme", "Belgrade")
    assert p.posted_at == date(2026, 10, 27) and "Laravel" in p.description and p.source == "feed"
    assert job.tags == ("PHP",) and p.url == "https://jobs.example.com/job/php-developer/"


def test_known_extensions_and_title_patterns():
    (wwr,) = feed_postings(1, WWR)
    assert (wwr.posting.title, wwr.posting.company, wwr.posting.location) == (
        "Senior PHP Engineer", "Acme", "Anywhere in the World")
    (lara,) = feed_postings(1, LARAJOBS)
    assert (lara.posting.company, lara.posting.location) == ("Acme", "Remote/USA")
    (tt,) = feed_postings(1, TEAMTAILOR)
    assert tt.posting.location == "Belgrade, Serbia"
    (atom,) = feed_postings(1, ATOM)
    assert (atom.posting.external_id, atom.posting.title, atom.posting.company, atom.posting.location) == (
        "feed:1:tag:example.com,2026:1", "Backend Developer", "Acme", "Remote, EU")
    assert atom.posting.posted_at == date(2026, 10, 26)


def test_split_title():
    assert split_title("PHP Developer at Acme") == ("PHP Developer", "Acme", "")
    assert split_title("Acme: PHP Developer") == ("PHP Developer", "Acme", "")
    assert split_title("PHP Developer – Acme (Berlin)") == ("PHP Developer", "Acme", "Berlin")
    assert split_title("Backend Engineer (PHP)") == ("Backend Engineer (PHP)", "", "")
    assert split_title("PHP Developer") == ("PHP Developer", "", "")


def test_items_missing_fields_are_skipped_or_kept_without_a_date():  # Review Focus 3
    content = (b'<rss version="2.0"><channel><title>J</title>'
               b"<item><link>https://example.com/a</link></item>"
               b"<item><title>No link</title></item>"
               b"<item><title>PHP Dev</title><link>https://example.com/b</link></item></channel></rss>")
    (job,) = feed_postings(3, content)
    assert job.posting.external_id == "feed:3:https://example.com/b" and job.posting.posted_at is None


def test_dates_stay_on_the_day_the_feed_wrote():
    content = (b'<rss version="2.0"><channel><title>J</title><item><title>PHP Dev</title>'
               b"<link>https://example.com/a</link><pubDate>Wed, 28 Oct 2026 00:00:00 +0100</pubDate>"
               b"</item></channel></rss>")
    assert feed_postings(1, content)[0].posting.posted_at == date(2026, 10, 28)


def test_not_a_feed_and_no_entities_from_a_doctype():
    assert not is_feed(b"<html><body>Jobs</body></html>")
    with pytest.raises(SourceError):
        parse(b"<html><body>Jobs</body></html>")
    evil = (b'<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
            b'<rss version="2.0"><channel><title>J</title><item><title>A &x;</title>'
            b"<link>https://example.com/a</link></item></channel></rss>")
    (job,) = feed_postings(1, evil)
    assert "root:" not in job.posting.title
    bracket = (b'<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY x "a]b">]>'
               b'<rss version="2.0"><channel><title>J</title><item><title>PHP Dev</title>'
               b"<link>https://example.com/a</link></item></channel></rss>")
    (job,) = feed_postings(1, bracket)
    assert job.posting.title == "PHP Dev" and feed_title(bracket) == "J"


def test_the_doctype_is_removed_before_feedparser_sees_the_bytes(monkeypatch):
    seen = []
    real = rss.feedparser.parse
    monkeypatch.setattr(rss.feedparser, "parse", lambda content: seen.append(content) or real(content))
    for subset in (b'SYSTEM "file:///etc/passwd"', b'[<!ENTITY x SYSTEM "file:///etc/passwd">]',
                   b'[<!ENTITY x "a]b">]'):
        parse(b'<?xml version="1.0"?><!DOCTYPE rss ' + subset + b'><rss version="2.0"><channel>'
              b"<title>J</title></channel></rss>")
    assert len(seen) == 3 and all(b"DOCTYPE" not in content and b"ENTITY" not in content for content in seen)


def test_advertised_feed_and_title_and_user_agent():
    page = '<html><head><link rel="alternate" type="application/rss+xml" href="/feed/"></head></html>'
    assert advertised_feed(page, "https://example.com/jobs/") == "https://example.com/feed/"
    atom = '<link rel="alternate" type="application/atom+xml" href="https://example.com/atom">'
    assert advertised_feed(atom, "https://example.com/") == "https://example.com/atom"
    assert advertised_feed("<html></html>", "https://example.com/") is None
    assert feed_title(WORDPRESS) == "Acme Jobs"
    calls = []
    fetch_feed(7, "https://example.com/feed", url_get({"https://example.com/feed": FakeResponse(content=WORDPRESS)}, calls))
    assert calls[0][2]["User-Agent"] == USER_AGENT
