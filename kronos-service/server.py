"""Kronos inference service. Deliberately has no published host ports by default.

Configuration (all optional):
  KRONOS_MODEL      Hugging Face model id            (default NeoQuasar/Kronos-small)
  KRONOS_TOKENIZER  tokenizer id                      (default NeoQuasar/Kronos-Tokenizer-base)
  KRONOS_MAX_CONTEXT  context length, 512 for small/base (default 512)
  KRONOS_DEVICE     torch device, e.g. cpu, cuda:0, mps (default: auto)
  KRONOS_THREADS    CPU threads                       (default 2)
  KRONOS_API_KEY    require Authorization: Bearer <key> on /predict
  KRONOS_LOAD_MODEL set to 0 to start without weights (smoke tests)
"""
import os
import threading
from contextlib import asynccontextmanager

import numpy as np
import pandas as pd
import torch
from auth import auth_required, require_api_key
from fastapi import Depends, FastAPI, HTTPException
from model import Kronos, KronosPredictor, KronosTokenizer
from pydantic import BaseModel, Field

MODEL_NAME = os.environ.get("KRONOS_MODEL", "NeoQuasar/Kronos-small")
TOKENIZER_NAME = os.environ.get("KRONOS_TOKENIZER", "NeoQuasar/Kronos-Tokenizer-base")
MAX_CONTEXT = int(os.environ.get("KRONOS_MAX_CONTEXT", "512"))

lock = threading.Lock()
predictor = None


@asynccontextmanager
async def lifespan(app):
    global predictor
    torch.set_num_threads(int(os.environ.get("KRONOS_THREADS", "2")))
    if os.environ.get("KRONOS_LOAD_MODEL", "1") != "0":
        tokenizer = KronosTokenizer.from_pretrained(TOKENIZER_NAME)
        model = Kronos.from_pretrained(MODEL_NAME)
        tokenizer.eval()
        model.eval()
        predictor = KronosPredictor(
            model,
            tokenizer,
            device=os.environ.get("KRONOS_DEVICE") or None,
            max_context=MAX_CONTEXT,
        )
    yield


app = FastAPI(lifespan=lifespan)


class Bar(BaseModel):
    date: str
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)


class Request(BaseModel):
    bars: list[Bar] = Field(min_length=60, max_length=512)
    dates: list[str] = Field(min_length=1, max_length=20)


@app.get("/health")
def health():
    return {"ready": predictor is not None, "model": MODEL_NAME, "auth_required": auth_required()}


@app.post("/predict", dependencies=[Depends(require_api_key)])
def predict(request: Request):
    if predictor is None:
        raise HTTPException(status_code=503, detail="model is not loaded")
    df = pd.DataFrame([bar.model_dump() for bar in request.bars])
    dates = pd.Series(pd.to_datetime(df.pop("date")))
    future = pd.Series(pd.to_datetime(request.dates))
    # Preserve individual draws. sample_count>1 would average away their spread.
    with lock, torch.inference_mode():
        torch.manual_seed(42)
        results = predictor.predict_batch(
            df_list=[df.copy() for _ in range(16)],
            x_timestamp_list=[dates for _ in range(16)],
            y_timestamp_list=[future for _ in range(16)],
            pred_len=len(future), sample_count=1, T=1.0, top_p=0.9, verbose=False,
        )
    paths = np.asarray([result["close"].to_list() for result in results])
    return {"paths": paths.tolist(), "model": MODEL_NAME}
