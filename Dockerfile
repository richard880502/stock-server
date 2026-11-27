FROM python:3.12-slim

# uv installs exactly the versions in uv.lock, so containers run what the tests ran.
COPY --from=ghcr.io/astral-sh/uv:0.9.21 /uv /bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_HTTP_TIMEOUT=300 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Dependencies first (cached until pyproject.toml / uv.lock change), then the project.
# The cache mount keeps downloaded wheels on the build host, so a rebuild (Coolify busts
# the layer cache whenever a build-time variable changes) does not download them again;
# the deploy host's link to PyPI is slow enough to hit uv's default 30 s timeout.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY README.md alembic.ini ./
COPY migrations ./migrations
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

EXPOSE 8000 8001

CMD ["quant-signal-api"]
