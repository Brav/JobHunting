FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
# The app runs as uid 1000 (docker-compose.yml); make the root-owned source readable whatever the host modes.
RUN chmod -R a+rX /app/src && uv sync --frozen --no-dev

# Its own user, owning the data folder: a new Docker volume starts writable on every system.
RUN groupadd --gid 1000 app && useradd --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /app/data && chown app:app /app/data
USER app

EXPOSE 8000
HEALTHCHECK --interval=1m --timeout=5s --start-period=30s \
    CMD ["/app/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/static/style.css', timeout=4)"]
CMD ["/app/.venv/bin/uvicorn", "--factory", "jobhunt.main:build_app", "--host", "0.0.0.0", "--port", "8000"]
