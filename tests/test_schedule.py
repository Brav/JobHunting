from datetime import datetime, time
from zoneinfo import ZoneInfo

from jobhunt.schedule import (describe, from_env, from_form, latest_slot, next_slot, parse, parse_time, push_time,
                              times_of_day, to_text)

TZ = ZoneInfo("Europe/Belgrade")
OWNER = from_env(time(10, 0), 4)


def test_times_of_day():
    assert times_of_day(OWNER) == [time(h) for h in (2, 6, 10, 14, 18, 22)]  # the owner's runs, unchanged
    assert times_of_day(from_env(time(7, 30), 6)) == [time(1, 30), time(7, 30), time(13, 30), time(19, 30)]
    assert times_of_day(parse("every:00:00/6")) == [time(0), time(6), time(12), time(18)]
    assert times_of_day(parse("times:13:00,08:00")) == [time(8), time(13)]
    assert times_of_day(parse("every:07:30/24")) == [time(7, 30)]


def test_text_round_trip_and_bad_text():
    for text in ("times:08:00,13:00", "every:00:00/6"):
        assert to_text(parse(text)) == text
    for bad in (None, "", "nonsense", "every:00:00/5", "times:", "times:25:00", "every:xx/6", "every:10:00/0"):
        assert parse(bad) is None, bad


def test_describe():
    assert describe(parse("times:08:00,13:00")) == "08:00, 13:00"
    assert describe(parse("every:00:00/6")) == "Every 6 h from 00:00"
    assert describe(OWNER) == "Every 4 h from 10:00"


def test_parse_time():
    assert [parse_time(v) for v in ("8:30", "08:30", "08:30:00", " 08:30 ", "24:00", "8.30", "")] == \
        [time(8, 30), time(8, 30), time(8, 30), time(8, 30), None, None, None]


def test_form_choices_and_errors():
    assert from_form("default", [], "", "") == (None, [])
    assert from_form("times", ["13:00", "", "8:00"], "", "") == ("times:08:00,13:00", [])
    assert from_form("every", [], "00:00", "6") == ("every:00:00/6", [])
    assert from_form("times", ["", ""], "", "")[1] == ["Add at least one time"]
    assert from_form("times", ["8:00", "08:00"], "", "")[1] == ["A time appears twice"]
    assert from_form("times", ["noon"], "", "")[1] == ["Use times like 08:30"]
    many = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 30)]
    assert from_form("times", many, "", "")[1] == ["At most 24 times a day"]
    assert from_form("every", [], "00:00", "5")[1] == ["Choose how many hours"]
    assert from_form("every", [], "", "6")[1] == ["Use times like 08:30"]


def test_latest_slot():
    assert latest_slot(OWNER, datetime(2026, 10, 28, 9, 30), TZ) == datetime(2026, 10, 28, 9, 0)    # 10:30 → 10:00
    assert latest_slot(OWNER, datetime(2026, 10, 28, 8, 59), TZ) == datetime(2026, 10, 28, 5, 0)    # 09:59 → 06:00
    assert latest_slot(OWNER, datetime(2026, 10, 27, 23, 30), TZ) == datetime(2026, 10, 27, 21, 0)  # 00:30 → 22:00 before
    assert latest_slot(OWNER, datetime(2026, 7, 1, 8, 30), TZ) == datetime(2026, 7, 1, 8, 0)        # 10:30 CEST


def test_latest_and_next_slot_across_midnight():
    s = parse("times:00:00,12:00")
    # Belgrade is UTC+1 after 25 Oct: local 00:00 on the 29th = 23:00 UTC on the 28th; 23:10 UTC = 00:10 local
    assert latest_slot(s, datetime(2026, 10, 28, 23, 10), TZ) == datetime(2026, 10, 28, 23, 0)
    assert latest_slot(s, datetime(2026, 10, 28, 23, 0, 30), TZ) == datetime(2026, 10, 28, 23, 0)
    assert latest_slot(s, datetime(2026, 10, 28, 22, 59), TZ) == datetime(2026, 10, 28, 11, 0)
    assert next_slot(s, datetime(2026, 10, 28, 22, 59), TZ) == datetime(2026, 10, 28, 23, 0)
    assert next_slot(s, datetime(2026, 10, 28, 23, 0), TZ) == datetime(2026, 10, 29, 11, 0)  # strictly after now


def test_latest_slot_on_the_dst_fall_back_day():
    # 2026-10-25: clocks go back from 03:00 CEST to 02:00 CET, so 02:00–03:00 local happens twice.
    def slot(utc_hour, utc_minute=0, day=25):
        return latest_slot(OWNER, datetime(2026, 10, day, utc_hour, utc_minute), TZ)

    assert slot(23, 59, day=24) == datetime(2026, 10, 24, 20, 0)  # 01:59 CEST → 22:00 CEST the day before
    assert slot(0, 30) == datetime(2026, 10, 25, 0, 0)  # 02:30 CEST → 02:00 CEST
    assert slot(1, 30) == datetime(2026, 10, 25, 0, 0)  # 02:30 CET (the repeated hour) → still 02:00 CEST
    assert slot(4, 59) == datetime(2026, 10, 25, 0, 0)  # 05:59 CET → 02:00 CEST
    assert slot(5, 0) == datetime(2026, 10, 25, 5, 0)   # 06:00 CET
    assert slot(9, 30) == datetime(2026, 10, 25, 9, 0)  # 10:30 CET → 10:00 CET


def test_push_time():
    assert push_time(OWNER) == time(10, 30)
    assert push_time(parse("times:23:45,08:00")) == time(8, 30)
    assert push_time(parse("times:23:45")) == time(0, 15)


def test_once_a_day_from_the_environment_stays_valid():
    assert from_form("every", [], "10:00", "24") == ("every:10:00/24", [])
    assert from_form("every", [], "10:00", "5")[1] == ["Choose how many hours"]
