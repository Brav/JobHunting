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


# --- the current CV: the .env file, else an upload (PDF or Word) ---

import io
import zipfile
from datetime import datetime
from types import SimpleNamespace

from jobhunt.cv import MAX_CV_BYTES, CurrentCV, current_cv, store_upload

NOW = datetime(2026, 10, 29, 9, 0)
PDF = b"%PDF-1.4\n% a tiny test file\n"


def docx_bytes(*paragraphs):
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(f'<w:p><w:r><w:t xml:space="preserve">{p}</w:t></w:r></w:p>' if p else "<w:p/>" for p in paragraphs)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", f'<w:document xmlns:w="{ns}"><w:body>{body}</w:body></w:document>')
    return buffer.getvalue()


def settings_for(tmp_path, cv_path=None):
    return SimpleNamespace(db_path=tmp_path / "jobhunt.db", cv_path=cv_path)


def test_environment_cv_wins_when_it_exists(tmp_path, session_factory):
    env_cv = tmp_path / "Mine.pdf"
    env_cv.write_bytes(PDF)
    with session_factory() as s:
        store_upload(s, tmp_path, "Uploaded.pdf", PDF, NOW)
        assert current_cv(s, settings_for(tmp_path, env_cv)) == CurrentCV(env_cv, "env", "Mine.pdf")


def test_upload_used_when_the_environment_cv_is_missing(tmp_path, session_factory):
    with session_factory() as s:
        store_upload(s, tmp_path, "Uploaded.pdf", PDF, NOW)
        assert current_cv(s, settings_for(tmp_path, tmp_path / "gone.pdf")) == \
            CurrentCV(tmp_path / "cv" / "cv.pdf", "upload", "Uploaded.pdf")


def test_no_cv(tmp_path, session_factory):
    with session_factory() as s:
        assert current_cv(s, settings_for(tmp_path)) == CurrentCV(None, None, "")


def test_pdf_stored_unchanged(tmp_path, session_factory):
    with session_factory() as s:
        path = store_upload(s, tmp_path, "cv.pdf", PDF, NOW)
    assert path == tmp_path / "cv" / "cv.pdf" and path.read_bytes() == PDF


def test_docx_stored_and_read(tmp_path, session_factory):
    with session_factory() as s:
        path = store_upload(s, tmp_path, "CV.docx", docx_bytes("First line", "", "Second line"), NOW)
    assert path == tmp_path / "cv" / "cv.docx"
    assert read_cv_text(path) == "First line\nSecond line"


@pytest.mark.parametrize("name, content", [("cv.txt", b"plain text"), ("cv.pdf", b"hello"), ("cv.docx", b"not a zip"),
                                           ("cv.docx", PDF)])
def test_wrong_files_refused(tmp_path, session_factory, name, content):
    with session_factory() as s, pytest.raises(CVError, match=r"^Upload a PDF or Word \(\.docx\) file$"):
        store_upload(s, tmp_path, name, content, NOW)
    assert not (tmp_path / "cv").exists() or not any((tmp_path / "cv").iterdir())


def test_over_5_mb_refused(tmp_path, session_factory):
    with session_factory() as s, pytest.raises(CVError, match="^The CV is larger than 5 MB$"):
        store_upload(s, tmp_path, "cv.pdf", PDF + b"0" * MAX_CV_BYTES, NOW)


def test_replacing_a_pdf_with_a_docx_removes_the_pdf(tmp_path, session_factory):
    with session_factory() as s:
        store_upload(s, tmp_path, "old.pdf", PDF, NOW)
        store_upload(s, tmp_path, "new.docx", docx_bytes("New"), NOW)
        assert sorted(p.name for p in (tmp_path / "cv").iterdir()) == ["cv.docx"]
        assert current_cv(s, settings_for(tmp_path)) == CurrentCV(tmp_path / "cv" / "cv.docx", "upload", "new.docx")


def test_path_parts_in_the_name_are_dropped(tmp_path, session_factory):
    with session_factory() as s:
        path = store_upload(s, tmp_path, "../../CV.PDF", PDF, NOW)
        assert path == tmp_path / "cv" / "cv.pdf"
        assert current_cv(s, settings_for(tmp_path)).name == "CV.PDF"
