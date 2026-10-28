import pytest
from fastapi.testclient import TestClient
from sqlmodel import update

from fakes import FakeClock, FakeSource, add_listing, make_posting as p
from jobhunt import checks
from jobhunt.filters import JobFilters, filtered_jobs
from jobhunt.models import Job
from jobhunt.runner import run_daily
from jobhunt.salary import RATES, parse_salary
from jobhunt.timeutil import utcnow


def eur(amount, currency="EUR", times=1):
    return amount * times * RATES[currency]


@pytest.mark.parametrize("text, low, high", [
    ("€50K - €60K", 50000, 60000),
    ("Salary: 45–55K € gross", 45000, 55000),
    ("£500-600/day, outside IR35", eur(500, "GBP", 220), eur(600, "GBP", 220)),
    ("PLN 20 000 - 25 000 netto (B2B) / month", eur(20000, "PLN", 12), eur(25000, "PLN", 12)),
    ("50.000 - 60.000 € brutto", 50000, 60000),
    ("$120,000–$150,000 base", eur(120000, "USD"), eur(150000, "USD")),
    ("Gehalt: 65.000 € p.a.", 65000, 65000),
    ("€50,000.00/yr - €60,000.00/yr", 50000, 60000),
    ("🤖 Forward Deployed Engineer (IA & Agentes) | €50K - €60K", 50000, 60000),
])
def test_parses(text, low, high):
    found = parse_salary(text)
    assert found is not None
    assert found.min_eur == pytest.approx(low, rel=0.01) and found.max_eur == pytest.approx(high, rel=0.01)


@pytest.mark.parametrize("text", ["Founded in 2015, we have 10,000 employees.", "Call +44 20 7946 0958.",
                                  "We serve 500 customers.", "Python 3.12 and PHP 8.3", "Raised $40M in 2024",
                                  ""])
def test_ignores_numbers_that_are_not_pay(text):
    assert parse_salary(text) is None


def test_filters_and_display(app_factory):
    app = app_factory()
    with TestClient(app) as c:
        sf = app.state.session_factory
        add_listing(sf, "A", keywords="python")
        clock = FakeClock(utcnow())
        run_daily(sf, FakeSource({"python": [[p("1", title="Paid Dev | €50K - €60K"), p("2", title="Quiet Dev")]]}),
                  trigger="manual", clock=clock, sleep=clock.sleep)
        with sf() as s:
            titles = lambda f: sorted(j.title for j in filtered_jobs(s, f, utcnow())[0])
            assert titles(JobFilters(has_salary="yes")) == ["Paid Dev | €50K - €60K"]
            assert titles(JobFilters(salary_from="55")) == ["Paid Dev | €50K - €60K"]
            assert titles(JobFilters(salary_from="70")) == []
        assert "€50–60k" in c.get("/jobs").text
