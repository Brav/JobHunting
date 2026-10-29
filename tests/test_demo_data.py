import importlib.util
from datetime import date
from pathlib import Path

from sqlmodel import select

from jobhunt.db import make_engine, make_session_factory
from jobhunt.eligibility import profile_saved
from jobhunt.models import Job, Listing, Run

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "demo_data.py"


def load_demo():
    spec = importlib.util.spec_from_file_location("demo_data", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_demo_data_is_made_up_and_complete(tmp_path):
    load_demo().build_demo(tmp_path / "demo.db", date(2026, 10, 29))
    with make_session_factory(make_engine(tmp_path / "demo.db"))() as s:
        jobs = s.exec(select(Job)).all()
        assert len(jobs) >= 24 and sum(j.status == "applied" for j in jobs) >= 5
        assert {j.eligibility for j in jobs} >= {"eligible", "relocate", "check", "not_eligible"}
        assert sum(j.salary_max_eur is not None for j in jobs) >= 3
        assert profile_saved(s) and len(s.exec(select(Listing)).all()) >= 2
        assert [r.status for r in s.exec(select(Run)).all()] == ["ok"]
        text = " ".join(f"{j.title} {j.company} {j.location} {j.description}" for j in jobs)
    for place in ("Belgrade", "Serbia"):  # the old built-in profile's home: the demo is someone else entirely
        assert place not in text
