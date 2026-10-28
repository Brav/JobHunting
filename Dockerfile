FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
# The app runs as uid 1000 (docker-compose.yml); make the root-owned source readable whatever the host modes.
RUN chmod -R a+rX /app/src && uv sync --frozen --no-dev

EXPOSE 8000
CMD ["/app/.venv/bin/uvicorn", "--factory", "jobhunt.main:build_app", "--host", "0.0.0.0", "--port", "8000"]
