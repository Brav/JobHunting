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
    ("* €500 home office budget\n* Salary: €60,000 per year + €5k sign-on bonus", 60000, 60000),
    ("Plata: 2000 EUR neto", eur(2000, times=12), eur(2000, times=12)),
    ("Salary €65k plus a €2,000 relocation bonus", 65000, 65000),
    # From real jobs: a bonus on top is still pay, and a lone yearly amount needs no word for pay.
    ("**Salary:** £35,000 - £65,000 + Bonus + Pension + Benefits", eur(35000, "GBP"), eur(65000, "GBP")),
    ("📍Berlin | 💶 €70,000 - €100,000 + Bonus! | 🌍 Remote Working", 70000, 100000),
    ("Up to £50,000 + benefits!", eur(50000, "GBP"), eur(50000, "GBP")),
    ("**Einstiegsgehalt**: Bis zu 65.000 Euro je nach Vorerfahrung", 65000, 65000),
    ("RAL up to € 35.000.", 35000, 35000),
    ("* Od 2000 EUR/mesiac + atraktívne výkonnostné bonusy", eur(2000, times=12), eur(2000, times=12)),
])
def test_parses(text, low, high):
    found = parse_salary(text)
    assert found is not None
    assert found.min_eur == pytest.approx(low, rel=0.01) and found.max_eur == pytest.approx(high, rel=0.01)


@pytest.mark.parametrize("text", ["Founded in 2015, we have 10,000 employees.", "Call +44 20 7946 0958.",
                                  "We serve 500 customers.", "Python 3.12 and PHP 8.3", "Raised $40M in 2024",
                                  "", "€500 home office budget", "€5k sign-on bonus", "€2,000 relocation bonus",
                                  "Referral bonus of €1,000", "1.000 € Weiterbildungsbudget", "Budget of €200 for books",
                                  "Join our team din 2015", "Tickets for Euro 2024", "USD 401(k) matching",
                                  "Laptop and €100 monthly gym allowance",
                                  "* Partial reimbursement of vacation costs (2,400 RON)",
                                  "ticket restaurant 8 €, welfare", "Food vouchers ( €102.26 month per person)",
                                  "**Personal Development** - £2,500 *training budget to acquire certifications",
                                  "We've raised $781M in funding and our last valuation was $22B"])
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
