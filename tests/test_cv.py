import hashlib

import pytest

from fakes import make_pdf
from jobhunt.cv import CVError, build_cv_prompt, read_cv_text
from jobhunt.models import Job

JOB = Job(id=1, external_id="123", title="Senior PHP Developer", company="Acme", location="Remote",
          url="https://www.linkedin.com/jobs/view/123")


def test_read_cv_text_in_reading_order_and_leaves_file_unchanged(tmp_path):
    path = tmp_path / "cv.pdf"
    path.write_bytes(make_pdf([(72, 760, "Jane Doe"), (72, 740, "Senior Developer"),
                               (72, 700, "Acme Corp"), (430, 700, "Nov 2024 - Present"), (72, 680, "Built APIs.")]))
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    text = read_cv_text(path)
    assert [line for line in text.splitlines() if line] == [
        "Jane Doe", "Senior Developer", "Acme Corp | Nov 2024 - Present", "Built APIs."]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_missing_or_broken_cv(tmp_path):
    with pytest.raises(CVError, match="CV not found at"):
        read_cv_text(tmp_path / "nope.pdf")
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf")
    with pytest.raises(CVError, match="Couldn't read the CV at"):
        read_cv_text(bad)


def test_prompt_contains_rules_job_and_cv():
    prompt = build_cv_prompt(JOB, "Build Laravel APIs.", "Jane Doe\nPHP")
    assert prompt.startswith("Please tailor my CV to the job below.")
    assert prompt.index("## Job") < prompt.index("Build Laravel APIs.") < prompt.index("## My current CV")
    for text in ("Use only facts from my CV", "Title: Senior PHP Developer", "Company: Acme", "Location: Remote",
                 "Link: https://www.linkedin.com/jobs/view/123", "downloadable PDF (or Word)"):
        assert text in prompt
    assert prompt.endswith("Jane Doe\nPHP")
    assert "<<<JOB DESCRIPTION\nBuild Laravel APIs.\nJOB DESCRIPTION>>>" in prompt and "the employer's text" in prompt


def test_prompt_without_description():
    assert "Description unavailable — ask me to paste it if you need it." in build_cv_prompt(JOB, None, "Jane Doe")
