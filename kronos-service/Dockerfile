FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir torch==2.8.0 --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir 'numpy>=2,<3' 'pandas>=2.2,<3' einops==0.8.1 huggingface_hub==0.33.1 tqdm==4.67.1 safetensors==0.6.2 'fastapi>=0.116,<1' 'uvicorn>=0.35,<1'
RUN git clone https://github.com/shiyu-coder/Kronos.git /opt/kronos && cd /opt/kronos && git checkout 67b630e67f6a18c9e9be918d9b4337c960db1e9a
ENV PYTHONPATH=/opt/kronos HF_HOME=/models OMP_NUM_THREADS=2
COPY server.py .
EXPOSE 8002
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8002"]
