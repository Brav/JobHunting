"""4 Day Week (4dayweek.io), jobs at companies with a four-day week, read through its public API."""

from datetime import date

from jobhunt import places
from jobhunt.sources.base import JobPosting
from jobhunt.sources.feeds import FeedPosting, _get_json, _markdown, pay_text, search_words

_PERIODS = {"month": "a month", "hour": "an hour"}  # year, yearly, annual and anything else: a year


def _location(item: dict) -> str:
    spots = [s for s in item.get("locations") or [] if s]
    if item.get("work_arrangement") == "remote":
        names = list(dict.fromkeys(s.get("country") or s.get("continent") for s in spots
                                   if s.get("country") or s.get("continent")))
        if not names:
            return "Remote, worldwide"
        return "Remote: " + ", ".join(names[:3]) + (f" +{len(names) - 3} more" if len(names) > 3 else "")
    first = spots[0] if spots else {}
    where = ", ".join(p for p in (first.get("city"), first.get("country")) if p)
    return f"{where} ({'hybrid' if item.get('work_arrangement') == 'hybrid' else 'on-site'})".strip()


def four_day_week(keywords: str, home_country: str, get) -> list[FeedPosting]:
    words = search_words(keywords)
    if not words:
        return []
    params = {"q": words, "limit": 100}
    if country := places.country_name(home_country):
        params["country"] = country
    found = []
    for item in _get_json(get, "https://4dayweek.io/api/v2/jobs", **params).get("data", []):
        period = _PERIODS.get(item.get("salary_period"), "a year")
        low, high = ((item.get(k) or 0) / 100 for k in ("salary_min", "salary_max"))  # the API gives cents
        posted = (item.get("posted_at") or "")[:10]
        found.append(FeedPosting(JobPosting(
            external_id=f"4dayweek:{item['id']}", title=item["title"].strip(),
            company=((item.get("company") or {}).get("name") or "").strip(), location=_location(item),
            url=item.get("url") or "", posted_at=date.fromisoformat(posted) if posted else None,
            is_remote=True if item.get("work_arrangement") == "remote" else None,
            description=_markdown(item.get("description")), source="4dayweek",
            salary_text=pay_text(low, high, item.get("salary_currency") or "USD", period)),
            tuple(s["name"] for s in item.get("skills") or () if s.get("name"))))
    return found
