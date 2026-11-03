"""Private CPU inference service. Deliberately has no published host ports."""
import os
import threading
from contextlib import asynccontextmanager

import numpy as np
import pandas as pd
import torch
from fastapi import FastAPI
from model import Kronos, KronosPredictor, KronosTokenizer
from pydantic import BaseModel, Field

lock = threading.Lock()
predictor = None


@asynccontextmanager
async def lifespan(app):
    global predictor
    torch.set_num_threads(2)
    tokenizer = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
    model = Kronos.from_pretrained("NeoQuasar/Kronos-small")
    tokenizer.eval()
    model.eval()
    predictor = KronosPredictor(
        model,
        tokenizer,
        device=os.environ.get("KRONOS_DEVICE") or None,
        max_context=512,
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
    return {"ready": predictor is not None, "model": "NeoQuasar/Kronos-small"}


@app.post("/predict")
def predict(request: Request):
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
    return {"paths": paths.tolist()}
