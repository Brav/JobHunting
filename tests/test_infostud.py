from datetime import date

import pytest
from fakes import FakeResponse, url_get

from jobhunt.sources.infostud import FEEDS, clean_link, infostud, job_line

HELLOWORLD = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Acme feed</title>
<item><title>PHP Developer</title>
<link>https://www.helloworld.rs/posao/PHP-Developer/Acme-d.o.o./764781?utm_source=rss&amp;utm_medium=feed&amp;ref=x</link>
<description>Acme d.o.o. - Beograd | Hibrid</description><pubDate>Wed, 28 Oct 2026 00:00:00 +0100</pubDate></item>
<item><title>Laravel Developer</title>
<link>https://www.helloworld.rs/posao/Laravel-Developer/Beta-Tech/764900?utm_source=rss</link>
<description>Beta - Tech - Novi Sad | Rad od kuće</description><pubDate>Wed, 28 Oct 2026 00:00:00 +0100</pubDate></item>
</channel></rss>""".encode()

INFOSTUD = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Example feed</title>
<item><title>PHP Developer</title>
<link>https://poslovi.infostud.com/posao/php-developer/acme/764781?utm_campaign=x</link>
<description>Acme d.o.o. - Beograd</description><pubDate>Tue, 27 Oct 2026 00:00:00 +0100</pubDate></item>
<item><title>Vue Developer</title>
<link>https://poslovi.infostud.com/posao/vue-developer/gamma/765000</link>
<description>Gamma</description><pubDate>Tue, 27 Oct 2026 00:00:00 +0100</pubDate></item>
</channel></rss>""".encode()


def test_infostud_postings():
    get = url_get({FEEDS[0]: FakeResponse(content=HELLOWORLD), FEEDS[1]: FakeResponse(content=INFOSTUD)})
    jobs = [j.posting for j in infostud(get)]
    assert [j.external_id for j in jobs] == ["infostud:764781", "infostud:764900", "infostud:765000"]
    php, laravel, vue = jobs
    assert php.url == "https://www.helloworld.rs/posao/PHP-Developer/Acme-d.o.o./764781?ref=x"
    assert (php.company, php.location, php.description) == ("Acme d.o.o.", "Beograd, Serbia (hybrid)",
                                                            "Acme d.o.o. - Beograd | Hibrid")
    assert php.posted_at == date(2026, 10, 28) and php.source == "infostud"
    assert (laravel.company, laravel.location, laravel.is_remote) == ("Beta - Tech", "Remote, Serbia", True)
    assert (vue.company, vue.location) == ("Gamma", "Serbia")  # Review Focus 4


def test_one_feed_down_still_reads_the_other():  # Review Focus 4
    get = url_get({FEEDS[0]: RuntimeError("down"), FEEDS[1]: FakeResponse(content=INFOSTUD)})
    assert [j.posting.external_id for j in infostud(get)] == ["infostud:764781", "infostud:765000"]
    with pytest.raises(RuntimeError):
        infostud(url_get({FEEDS[0]: RuntimeError("down"), FEEDS[1]: RuntimeError("down too")}))


def test_job_line():
    assert job_line("Acme - Niš | Remote") == ("Acme", "Niš", "remote")
    assert job_line("Acme - Beograd | rad od kuce") == ("Acme", "Beograd", "remote")
    assert job_line("Acme") == ("Acme", "", "")


def test_clean_link():
    assert clean_link("https://example.com/posao/1?utm_source=rss&a=1") == "https://example.com/posao/1?a=1"
    assert clean_link("https://example.com/posao/1?utm_source=rss") == "https://example.com/posao/1"
