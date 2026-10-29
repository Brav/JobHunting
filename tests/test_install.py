"""The install files are what a new user downloads; checked as text (there is no YAML library here)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_install_compose_needs_nothing():
    text = (ROOT / "install" / "docker-compose.yml").read_text()
    assert "image: ghcr.io/brav/jobhunting:latest" in text and "build:" not in text
    assert '"127.0.0.1:8765:8000"' in text and "jobhunt-data:/app/data" in text and "restart: unless-stopped" in text
    assert not [line for line in text.splitlines() if "${" in line and not line.lstrip().startswith("#")]


def test_owner_compose_takes_the_user_id_from_env():
    assert 'user: "${JOBHUNT_UID:-1000}:${JOBHUNT_GID:-1000}"' in (ROOT / "docker-compose.yml").read_text()
    example = (ROOT / ".env.example").read_text()
    assert "JOBHUNT_UID" in example and "JOBHUNT_GID" in example


def test_image_runs_as_its_own_user():
    text = (ROOT / "Dockerfile").read_text()
    assert "USER app" in text and "1000" in text and "/app/data" in text


def test_ci_tests_then_publishes_both_architectures():
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    for needle in ("uv run pytest", "linux/amd64,linux/arm64", "ghcr.io/brav/jobhunting", "packages: write",
                   "tags:", "v*", "needs: test"):
        assert needle in text, needle


def test_install_compose_has_a_fixed_project_name():
    """The data volume must not depend on the folder's name: renaming the folder mustn't look like losing the data."""
    assert "\nname: jobhunt\n" in "\n" + (ROOT / "install" / "docker-compose.yml").read_text()


def test_version_tags_never_move_latest():
    assert "latest=false" in (ROOT / ".github" / "workflows" / "ci.yml").read_text()
