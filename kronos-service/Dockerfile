# syntax=docker/dockerfile:1
FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir --timeout 120 --retries 5 torch==2.8.0 --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir 'numpy>=2,<3' 'pandas>=2.2,<3' einops==0.8.1 huggingface_hub==0.33.1 tqdm==4.67.1 safetensors==0.6.2 'fastapi>=0.116,<1' 'uvicorn>=0.35,<1'
RUN git clone https://github.com/shiyu-coder/Kronos.git /opt/kronos && cd /opt/kronos && git checkout 67b630e67f6a18c9e9be918d9b4337c960db1e9a
ENV PYTHONPATH=/opt/kronos PYTHONUNBUFFERED=1 HF_HOME=/models OMP_NUM_THREADS=2

# Bake the weights into the image so it starts anywhere without reaching Hugging Face.
# Build another size with: --build-arg KRONOS_MODEL=NeoQuasar/Kronos-base
# Skip with --build-arg BAKE_WEIGHTS=false to download on first start instead.
ARG KRONOS_MODEL=NeoQuasar/Kronos-small
ARG KRONOS_TOKENIZER=NeoQuasar/Kronos-Tokenizer-base
ARG BAKE_WEIGHTS=true
ENV KRONOS_MODEL=${KRONOS_MODEL} KRONOS_TOKENIZER=${KRONOS_TOKENIZER}
RUN if [ "$BAKE_WEIGHTS" = "true" ]; then \
      python -c "from huggingface_hub import snapshot_download as d; import os; d(os.environ['KRONOS_TOKENIZER']); d(os.environ['KRONOS_MODEL'])"; \
    fi

LABEL org.opencontainers.image.source="https://github.com/richard880502/stock-server" \
      org.opencontainers.image.description="Kronos candlestick forecast service (CPU)" \
      org.opencontainers.image.licenses="MIT"

COPY server.py auth.py ./
EXPOSE 8002
HEALTHCHECK --interval=20s --timeout=5s --start-period=120s --retries=10 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8002/health')"
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8002"]
