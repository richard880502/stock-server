FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN pip install --no-cache-dir "uv>=0.9,<1" && uv sync --frozen --no-dev --extra quotes
ENV PATH="/app/.venv/bin:$PATH"
HEALTHCHECK --interval=15s --timeout=3s --start-period=90s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8003/livez', timeout=2)" || exit 1
CMD ["quant-signal-quotes"]
