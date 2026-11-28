FROM python:3.12-slim
# Same uv as the main image (already on the build host), instead of a pip download.
COPY --from=ghcr.io/astral-sh/uv:0.9.21 /uv /bin/uv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_HTTP_TIMEOUT=300
WORKDIR /app
# Dependencies first, so a source-only change reuses this layer; the cache mount keeps
# wheels on the build host (shared with the main image) when the layer is rebuilt. The
# deploy host's link to PyPI is slow enough to hit uv's default 30 s timeout.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra quotes --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra quotes
ENV PATH="/app/.venv/bin:$PATH"
HEALTHCHECK --interval=15s --timeout=3s --start-period=90s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8003/livez', timeout=2)" || exit 1
# The ingest service: official exchange sync + Shioaji collector (one writer unit).
CMD ["quant-signal-ingest"]
