from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from jobhunt.config import load_settings
from jobhunt.timeutil import local_date, local_day_bounds_utc, local_to_utc, to_local

TZ = ZoneInfo("Europe/Belgrade")


def test_defaults():
    s = load_settings({})
    assert (s.tz, s.run_at, s.db_path, s.results_per_listing) == (
        TZ, time(10, 0), Path("/app/data/jobhunt.db"), 100)


def test_overrides():
    s = load_settings({"TZ": "UTC", "JOBHUNT_RUN_AT": "07:30",
                       "JOBHUNT_DB_PATH": "/tmp/x.db", "JOBHUNT_RESULTS_PER_LISTING": "25"})
    assert (s.tz, s.run_at, s.db_path, s.results_per_listing) == (
        ZoneInfo("UTC"), time(7, 30), Path("/tmp/x.db"), 25)


@pytest.mark.parametrize("bad", ["25:00", "10", "ten"])
def test_bad_run_at(bad):
    with pytest.raises(ValueError):
        load_settings({"JOBHUNT_RUN_AT": bad})


def test_time_helpers():
    assert to_local(datetime(2026, 10, 27, 23, 30), TZ) == datetime(2026, 10, 28, 0, 30, tzinfo=TZ)
    assert local_date(datetime(2026, 10, 27, 23, 30), TZ) == date(2026, 10, 28)
    assert local_to_utc(date(2026, 10, 28), time(10, 0), TZ) == datetime(2026, 10, 28, 9, 0)
    assert local_to_utc(date(2026, 7, 1), time(10, 0), TZ) == datetime(2026, 7, 1, 8, 0)
    assert local_day_bounds_utc(date(2026, 10, 28), TZ) == (
        datetime(2026, 10, 27, 23, 0), datetime(2026, 10, 28, 23, 0))


def test_schedule_and_cv_settings():
    s = load_settings({})
    assert (s.run_every_hours, s.cv_path) == (4, Path("/app/cv/original.pdf"))
    s = load_settings({"JOBHUNT_RUN_EVERY_HOURS": "6", "JOBHUNT_CV_PATH": "/x/cv.pdf"})
    assert (s.run_every_hours, s.cv_path) == (6, Path("/x/cv.pdf"))


@pytest.mark.parametrize("bad", ["5", "0", "25", "x"])
def test_bad_run_interval(bad):
    with pytest.raises(ValueError):
        load_settings({"JOBHUNT_RUN_EVERY_HOURS": bad})


def test_deepl_key_setting():
    assert load_settings({}).deepl_auth_key == ""
    assert load_settings({"DEEPL_AUTH_KEY": "  abc:fx\n"}).deepl_auth_key == "abc:fx"
