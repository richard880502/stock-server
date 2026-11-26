FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN pip install --no-cache-dir "uv>=0.9,<1" && uv sync --frozen --no-dev --extra quotes
ENV PATH="/app/.venv/bin:$PATH"
CMD ["quant-signal-quotes"]
