from datetime import date

from fakes import url_get

from jobhunt.sources.devbg import company_from_link, devbg
from jobhunt.sources.feeds import USER_AGENT, matches_keywords

DEVBG = [
    {"id": 101, "title": {"rendered": "Senior PHP Developer &#8211; Laravel"},
     "link": "https://dev.bg/company/jobads/acme-labs-senior-php-developer-laravel/", "date_gmt": "2026-10-27T08:00:00",
     "content": {"rendered": "<p>Build Laravel APIs.</p>"},
     "class_list": ["post-101", "job_listings_tag-sofiya", "job_listings_tag-hybrid", "techstack-laravel",
                    "job_listing_category-full-stack-development"]},
    {"id": 102, "title": {"rendered": "Backend Engineer"}, "link": "https://dev.bg/company/jobads/backend-engineer/",
     "date_gmt": "2026-10-26T08:00:00", "content": {"rendered": "<p>Node.</p>"},
     "class_list": ["job_listings_tag-remote", "techstack-node-js"]},
    {"id": 103, "title": {"rendered": "Старши PHP разработчик"},
     "link": "https://dev.bg/company/jobads/acme-starshi-php-razrabotchik/", "date_gmt": "2026-10-25T08:00:00",
     "content": {"rendered": "<p>PHP.</p>"}, "class_list": ["job_listings_tag-plovdiv"]},
]


def test_devbg_postings():
    calls = []
    jobs = devbg("php remote sofia", url_get({"https://dev.bg/wp-json/wp/v2/job-listings": DEVBG}, calls))
    url, params, headers = calls[0]
    assert url == "https://dev.bg/wp-json/wp/v2/job-listings" and headers["User-Agent"] == USER_AGENT
    assert params == {"search": "php", "per_page": 100, "_fields": "id,title,link,date_gmt,content,class_list"}
    first, remote, cyrillic = (j.posting for j in jobs)
    assert (first.external_id, first.title, first.company) == ("devbg:101", "Senior PHP Developer – Laravel", "Acme Labs")
    assert first.location == "Sofia, Bulgaria (hybrid)" and first.posted_at == date(2026, 10, 27)
    assert first.source == "devbg" and "Laravel" in first.description
    assert jobs[0].tags == ("laravel", "full stack development")
    assert matches_keywords(jobs[0], "fullstack developer")
    assert (remote.company, remote.location, remote.is_remote) == ("", "Remote, Bulgaria", True)
    assert (cyrillic.company, cyrillic.location) == ("", "Plovdiv, Bulgaria")  # Review Focus 5


def test_company_from_link():
    assert company_from_link("https://dev.bg/company/jobads/ampeco-ai-native-engineer/", "AI-Native Engineer") == "Ampeco"
    assert company_from_link("https://dev.bg/company/jobads/backend-engineer/", "Backend Engineer") == ""


def test_no_search_words_makes_no_request():
    calls = []
    assert devbg("remote sofia", url_get({}, calls)) == []
    assert calls == []
