import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_readme_guides_a_new_user():
    text = (ROOT / "README.md").read_text()
    for needle in ("Docker Desktop", "install/docker-compose.yml", "docker compose up -d", "http://localhost:8765",
                   "Skip for now", "docker compose pull", "Troubleshooting", "docs/development.md", "MIT"):
        assert needle in text, needle
    images = re.findall(r"\]\((docs/images/[^)]+)\)", text)
    assert len(images) == 4 and all((ROOT / image).is_file() for image in images)


def test_developer_notes_keep_the_owner_setup():
    text = (ROOT / "docs" / "development.md").read_text()
    for needle in ("JOBHUNT_CV_DIR", "JOBHUNT_UID", "jobhunt.backup restore", "mcp_server", "Change visibility", "v*"):
        assert needle in text, needle


def test_readme_downloads_the_compose_file_from_the_terminal():
    text = (ROOT / "README.md").read_text()
    url = "https://raw.githubusercontent.com/Brav/JobHunting/main/install/docker-compose.yml"
    assert f"curl -L -o docker-compose.yml {url}" in text and f"wget -O docker-compose.yml {url}" in text
    assert "no configuration file provided" in text


def test_readme_explains_moving_to_another_computer():
    text = (ROOT / "README.md").read_text()
    assert "docker compose cp jobhunt.db jobhunt:/app/data/import.db" in text
    assert "jobhunt.backup restore /app/data/import.db" in text
    assert "not in the zip" in text  # the CV and the DeepL key are added again


def test_docs_describe_per_listing_schedules():
    readme = (ROOT / "README.md").read_text()
    dev = (ROOT / "docs" / "development.md").read_text()
    assert "Search schedule" in readme and "every few hours" not in readme
    assert "every minute" in dev and "within 15 minutes" not in dev
