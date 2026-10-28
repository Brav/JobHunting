from fakes import FakeSource, make_posting as p
from jobhunt.smoke import main
from jobhunt.sources.base import SourceBlocked


def test_prints_jobs(capsys):
    src = FakeSource({"python": [[p("1", title="Django Dev")]]})
    assert main(["python", "Belgrade, Serbia", "10"], source=src) == 0
    out = capsys.readouterr().out
    assert "1 | - | Django Dev | Acme | Belgrade" in out and "1 jobs" in out
    assert (src.calls[0]["radius_km"], src.calls[0]["window_hours"]) == (10, 168)


def test_reports_errors(capsys):
    src = FakeSource({"python": [SourceBlocked("rate limited by LinkedIn")]})
    assert main(["python", "Belgrade, Serbia"], source=src) == 1
    assert "rate limited by LinkedIn" in capsys.readouterr().err
