# Kronos fine-tuning plan (Taiwan stocks)

Status: **plan, nothing built yet.** Written 2026-11-03.

## 1. Why

`evals/RESULTS.md` (zero-shot Kronos-small, 8 symbols, about 1,920 point-in-time windows, lookback 120) found no usable signal:
median error 1.73 / 3.90 / 11.68 % of price at 1 / 5 / 20 days versus 1.55 / 3.55 / 7.65 % for "no change"; direction accuracy 48.7 / 52.4 / 46.4 % with every 95 % CI containing 50 %; the nominal 80 % interval covered only 59 / 52 / 34 %.
Fine-tuning on Taiwan data is the main remaining lever. **It may still fail**, and a clean negative result is an acceptable outcome of this plan.

## 2. Success criteria (fixed before training)

Evaluated on data **strictly after** the training and validation periods, with the existing walk-forward harness (`evals/kronos_walkforward.py`) and the same baselines (naive, bootstrap, zero-shot Kronos):

1. Median absolute error is lower than **naive** at h = 5 and h = 20, paired month-cluster bootstrap p < 0.01.
2. 10-90 interval coverage within 75-85 % at every horizon (after the calibration step in 6.4, not before).
3. Direction accuracy 95 % CI lower bound above 50 % at one or more horizons, with a Bonferroni-corrected p-value.
4. Holds in at least 2 of 3 rolling test years, including a down or stressed one.

Meeting only (2) means "better calibrated scenarios", not a trading signal, and is reported as exactly that. Fail all of them: stop, keep the bootstrap scenario range, record the result.

## 3. Decisions needed from the owner

| # | Question | Default if unanswered |
|---|---|---|
| D1 | Where does real training run? This Mac (M4, MPS) is fine for smoke tests and tiny subsets; a full run needs w79's 4090 or similar | Smoke test on the Mac, full run on w79 |
| D2 | How much of the 4090 may training use while the chat model on `/svc-a` is loaded? | Ask before starting; training pauses the chat model if it must |
| D3 | Model size: Kronos-small first, Kronos-base (about 4x larger, largest open weights) only if small shows promise | small |
| D4 | Fine-tune the tokenizer too, or predictor only | predictor only first, tokenizer as an ablation |
| D5 | Where do trained weights live (private Hugging Face repo, a GHCR image layer, or w79 disk) | private image tag on GHCR |

## 4. Data

**Source of truth is the project database; nothing is scraped for training.**

1. **Universe**: all TWSE and TPEx common stocks, including names that later delisted (the daily whole-market reports contain them, which avoids survivorship bias). ETFs excluded at first because splits are not adjusted (known 0050.TW 2025-06-18 4-for-1 gap).
2. **History**: about 10 years of daily OHLCV and turnover. Per-stock monthly fetching would be roughly 200,000 requests, so use the **whole-market daily reports** (one request per exchange per day, about 5,000 requests in total) with a "quotes only" mode for deep history, skipping the institutional and credit reports that rankings need.
3. **Adjustment**: forward-adjust from the corporate-action report, fetched for the whole market per year range (one request per range), using the same point-in-time-safe method as the app. Drop any window containing an adjusted jump above 11 % (existing rule), halts, and stocks with fewer than 60 closes.
4. **Splits** by date, with a purge gap equal to the longest horizon so no window straddles a boundary. Initial layout: train up to 2022-12-31, validation 2023, test 2024-01 onward. Robustness uses three rolling folds (test years 2023, 2024, 2025), each retrained.
5. **Export**: a script (`scripts/export_kronos_dataset.py`) writes the format the chosen official pipeline expects. Two upstream options exist: `finetune/` (Qlib-style multi-stock pickles) or `finetune_csv/` (one CSV per config, which does not fit hundreds of stocks without our own concatenation). Verify `finetune/dataset.py` and `qlib_data_preprocess.py` at the pinned commit (67b630e) and write the exporter to produce their pickles directly, without installing Qlib.
6. Every export records the database snapshot date, universe size, split dates and a content hash so a run is reproducible.

## 5. Training

1. **Smoke test** (Mac, MPS or CPU): tiny subset, a few hundred steps, confirm loss falls and a checkpoint loads in the existing sidecar. Measure seconds per step to size the real run. Do this before committing GPU time.
2. **Real run** (D1): Kronos-small predictor fine-tune from `NeoQuasar/Kronos-small`, tokenizer frozen (D4). Lookback and horizons match the evaluation (120; 1 / 5 / 20) so results are comparable; a 512 lookback is a separate experiment.
3. Fixed seeds; save the best checkpoint by validation loss and also the last; log config, data hash and git commit with every run.
4. Training code stays outside the serving image (a separate `finetune/` tool directory or a pinned upstream checkout), so the inference image does not grow.
5. Ablations, in order of expected value: predictor only, then with tokenizer, then Kronos-base, then lookback 512.

## 6. Evaluation

1. Extend `evals/kronos_walkforward.py` to take a model path or id, then run zero-shot and fine-tuned on identical origins from the test period.
2. Same metrics as `RESULTS.md`: MAE, pinball, direction accuracy with exact CI, coverage, correlation, paired cluster-bootstrap differences. All tests reported with a multiple-comparison correction.
3. Per-symbol and per-year tables, plus a stressed-period check.
4. **Calibration** (separate from model quality): widen intervals with split-conformal using the validation year, and report coverage again on test. Raise the sample count from 16 to 64 for stable quantiles.
5. Output is a new `evals/RESULTS_FINETUNE.md` with the verdict against section 2.

## 7. Serving and deployment via Coolify (only after section 2 passes, or if the owner wants it for research)

1. The service loads a path or private repo id (`KRONOS_MODEL`); the response already returns the model id, so every forecast records which weights made it. Name weights `kronos-tw-<size>-<yyyymmdd>`.
2. Build and publish through the existing GHCR workflow, adding a variant for the fine-tuned weights.
3. **CPU first** on w79's `sandbox-docker` server as a Coolify service from the image; later a CUDA image if the 4090 is made available (D2). Resource limits set so it cannot starve the chat model.
4. Expose it through the w79 Caddy gateway (a new `/svc-*` route with the existing key check) and decide how `stock-api` on the VPS reaches it. Today the VPS tunnel lives on its own Docker network, so this needs either joining networks or publishing a loopback port; confirm before changing infrastructure.
5. Set `KRONOS_API_KEY` on both sides. Health check on `/health`; rollback is switching the image tag.

## 8. After acceptance

- Show the forecast to the LLM platform only as labelled evidence with its measured track record attached, never for entry or exit decisions.
- Store forecasts in the database (keyed by symbol, as-of, horizon, lookback, model id) so they are computed once and later compared with realised outcomes.
- Monthly retraining behind a champion/challenger gate using the same evaluation; no automatic promotion.

## 9. Risks

- **Leakage**: mitigated by date splits with purge gaps and the point-in-time export; the export script gets its own tests.
- **Data defects** (unadjusted splits, gaps such as 2603.TW 2022-09): detection filter plus exclusion, logged.
- **Overfitting to one regime**: rolling folds; negative results are reported.
- **Compute contention** on w79: decided in D2; training is interruptible.
- **Cost of finding nothing**: the smoke test and a small first run limit it before any large run.

## 10. Milestones

| Step | Deliverable | Needs |
|---|---|---|
| M1 | Quotes-only deep backfill mode, adjustment, exporter, leakage tests | none |
| M2 | Smoke-test run on the Mac and timing | D1 |
| M3 | First real fine-tune and the evaluation against section 2 | D1, D2, D3, D4 |
| M4 | Calibration and the verdict document | M3 |
| M5 | Serving image, Coolify service on w79, gateway route | accepted result or explicit research request, D5 |
