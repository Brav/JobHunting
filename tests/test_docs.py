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
