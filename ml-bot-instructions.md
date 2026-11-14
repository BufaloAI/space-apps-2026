# ML layer + overnight pipeline: instructions for Claude Code

Drop this file at `ml/CLAUDE.md` (or the repo root) and tell Claude Code: "Read ML_PIPELINE_INSTRUCTIONS.md, write a plan first, then implement stage by stage."

## 1. Project context (read first)

- **Event:** NASA Space Apps 2026, challenge "Field Shift: Adapting Farms with NASA Data". Goal: a decision-support tool that compares at least 3 crop-rotation strategies (including the farm's existing one) using NASA Earth observations plus local agricultural data. It must explain *why*, show evidence, show uncertainty, and never replace farmer judgment. See `objectives.md` and `resources.md` in the repo root.
- **Repo:** `BufaloAI/space-apps-2026`, managed with **uv** (Python >= 3.12, `pyproject.toml`). Folders: `backend/` (FastAPI + vLLM bridge, owned by teammates), `frontend/` (teammates, in progress), `data-load/` (my data-loader pipeline, inspired by Sebastian Raschka's "PyTorch in One Hour", ch. 6: Dataset / DataLoader / workers), `ml/` (**yours to build**).
- **Hardware for training:** Lenovo Legion laptop, NVIDIA RTX 5060 8 GB VRAM, 32 GB RAM, Intel Core Ultra 7 356H. It will run unattended overnight.
- **ML target (v1):** per-area, per-week forecast of next-period NDVI (regression), with uncertainty (10/50/90% quantiles). Later versions add growth-stage or yield scores. These forecasts feed a separate rotation-scoring step (risk + confidence), which is NOT part of this task.

## 2. Scope

**In scope:** (a) an orchestrated pipeline that runs sampling, feature extraction, data loading and training **concurrently**; (b) GPU XGBoost training, evaluation, and model artifacts; (c) a stable `predict()` interface for later backend use.

**Out of scope, do not touch:** `backend/`, `frontend/`, LLM/vLLM code, answer caching, map/GeoJSON reshaping. Do not edit teammates' files. If something in `backend/` blocks you, write it in `ml/DECISIONS.md` and move on.

## 3. Rules of engagement

1. **Inspect before building.** Read everything in `data-load/` first (and `objectives.md`, `resources.md`). Reuse and wrap its Dataset/DataLoader/sampling code; do not rewrite it. If an interface is unclear, document your assumption in `ml/DECISIONS.md`.
2. **Plan first.** Output a short plan (stages, files, interfaces, risks) and then proceed without waiting. Nobody will answer questions overnight, so choose sensible defaults and log them.
3. **Small, testable steps.** Commit after each stage. Add pytest tests (the repo already uses pytest). Add dependencies with `uv add ...`, never `pip install`.
4. **No secrets or big files in git.** NASA Earthdata credentials come from environment variables or `~/.netrc`. Add data caches, shards, models and run logs to `.gitignore`.
5. **Verify GPU first** (see section 7 preflight). If CUDA is unavailable, fall back to CPU `hist` and clearly report that in the run log. Do not silently continue.
6. **Never fabricate data.** If a source fails, record it as missing. Synthetic data is allowed only in tests and the smoke run, and must be labeled.

## 4. Target layout (adjust to what `data-load/` already has)

```
ml/
  config.yaml            # areas, date range, sources, feature list, model params, time budget
  DECISIONS.md           # running log of assumptions and choices
  pipeline/
    orchestrator.py      # runs all stages concurrently, owns queues, shutdown, heartbeat
    manifest.py          # SQLite or JSONL record of fetched/featurized items (resume + idempotency)
    sampler.py           # stage 1: fetch raw data per (area, source, time chunk)
    features.py          # stage 2: raw -> feature rows (pure functions)
    shards.py            # stage 3: write/read parquet feature shards
  train/
    dataset.py           # shards -> training matrix (see section 6)
    train_xgb.py         # GPU XGBoost training + evaluation
    evaluate.py          # metrics, calibration, plots
  predict.py             # predict(features_df) -> dict (stable schema, see section 9)
  run_overnight.py       # CLI entry point
  tests/
  runs/<timestamp>/      # logs, model, metrics, report (gitignored)
  cache/                 # raw + shards (gitignored)
```

## 5. Concurrent pipeline design

XGBoost is **not** a mini-batch model like a PyTorch net, so the "DataLoader + training loop at the same time" idea works differently. Build a staged producer/consumer pipeline, and train by **periodically retraining on the shards accumulated so far**. Tabular GPU XGBoost trains in seconds to minutes, so this is cheap.

```
[sampler: threads, I/O bound] --queue--> [feature workers: process pool, CPU bound]
        --queue--> [shard writer: parquet] --> shard dir
                                                   |
                                  [trainer: main process, GPU] reads all shards every N new shards
```

Requirements:

- **Bounded queues** (e.g. 32 items) so fetching never outruns featurizing and RAM stays flat.
- **Stage 1 sampler:** thread pool (4 to 8 workers), per-source rate limiting, retries with exponential backoff and jitter, request timeouts, resumable via the manifest. Unit of work = `(area_id, source, time_chunk)`. Prefer subsetting APIs: NASA POWER REST, AppEEARS or ORNL DAAC subset, `earthaccess` + `xarray` bounding-box reads, STAC for HLS/Sentinel-2. Aggregate to the **field polygon at weekly steps** (mean/min/max). Never download whole global rasters.
- **Stage 2 features:** `concurrent.futures.ProcessPoolExecutor` (workers = physical cores - 2). Feature functions are pure and deterministic. Apply quality masks (fill values -999 / -3000 to NaN, cloud and QA flags), fill gaps of at most 2 steps, and do not fill longer gaps.
- **Stage 3 shards:** parquet files of ~50k to 200k rows with a `schema_version` column, written atomically (write temp file, then rename). Row key = `area_id + week`.
- **Trainer:** runs in the main process, owns the GPU, and never blocks the pipeline. Every N new shards (config) or every M minutes: rebuild the training set from all shards, train, evaluate, and save only if validation improves.
- **Concurrency rules:** only one process uses the GPU. Pass file paths between processes, not large arrays. Use `multiprocessing` with spawn for CUDA safety. If you keep PyTorch-style `Dataset`/`DataLoader` from `data-load/`, use `num_workers`, `persistent_workers=True`, seeded workers (Raschka ch. 6 pattern) for the shard-reading stage only.
- **Robustness:** a crash in one item must not kill the run (log, record in manifest, continue). Handle SIGINT/SIGTERM gracefully: flush shards, save the last model, write the report. Write a heartbeat file every minute. Enforce a **disk budget** (config, e.g. 30 GB) and a **time budget** (e.g. `--hours 8`).
- **Keep the machine awake:** on Linux wrap the run with `systemd-inhibit --what=idle:sleep:handle-lid-switch`. Check temperatures or throttling messages and log them.

## 6. GPU XGBoost model architecture

**Library:** `xgboost >= 3.0` with CUDA wheels.

**Parameters (starting point, put in `config.yaml`):**

```python
params = dict(
    device="cuda",
    tree_method="hist",
    objective="reg:squarederror",   # point forecast model
    eval_metric=["rmse", "mae"],
    max_depth=6, eta=0.05,
    subsample=0.8, colsample_bytree=0.8,
    min_child_weight=5, reg_lambda=1.0,
    max_bin=256,
)
# num_boost_round=3000, early_stopping_rounds=100 on the validation set
```

**Uncertainty:** train three extra models (or one multi-quantile model) with `objective="reg:quantileerror"` and `quantile_alpha=[0.1, 0.5, 0.9]`. Report interval coverage (should be near 80%) and mean interval width. These feed the confidence score later.

**Inputs (features, per area-week):** lagged NDVI (last 4 to 12 weeks), phenology (green-up date, peak date and value, season length, area under curve, slopes), climate (growing degree days, heat days > 35 C, frost days, longest dry spell, rain onset, seasonal rain, rain variability), soil moisture (mean/min, days below dryness threshold, anomaly vs day-of-year climatology), terrain (SRTM slope/aspect), soil (SSURGO or SoilGrids texture, organic matter, water capacity), day-of-year as sin/cos, latitude/longitude. Trees need no scaling. Keep NaN as NaN (XGBoost handles missing values natively).

**Target:** `ndvi_next_week` (config-switchable horizon: 1, 2, 4 weeks). Build targets with `shift` per `area_id`, and drop rows whose target is missing.

**Validation (critical):** never random split. Use **grouped, temporal splits**: hold out whole areas (`GroupKFold` by `area_id`) AND whole years (train on earlier years, test on later). Report both. Fit any preprocessing on the training split only.

**Memory:** the tabular set will probably fit in 8 GB VRAM. Use `xgboost.QuantileDMatrix` for the GPU. If it does not fit, implement `xgboost.DataIter` with external memory (`ExtMemQuantileDMatrix`) reading shards one by one. Make it config-switchable and test it on a small set.

**Tuning:** optional Optuna study with a fixed time budget (e.g. 30 min per nightly cycle), tuning `max_depth`, `eta`, `subsample`, `colsample_bytree`, `min_child_weight`, `reg_lambda`. Always keep the default-parameter model as a baseline to compare against.

**Baselines (required):** (1) persistence (next NDVI = current NDVI), (2) seasonal climatology (mean NDVI of that week over past years). The XGBoost model must beat both on held-out areas/years, otherwise say so plainly in the report.

**Explainability:** save feature importances and SHAP values (`booster.predict(dmat, pred_contribs=True)` runs on GPU). The frontend and LLM will need top contributing features per prediction, so store the top 5 per row.

**Artifacts per run** (in `ml/runs/<timestamp>/`): `model.ubj`, `quantile_models/`, `feature_schema.json` (ordered names, dtypes, schema_version), `metrics.json`, `importance.csv`, `config_used.yaml`, `data_manifest_hash.txt`, git commit hash, `train.log`.

## 7. Preflight (run before the overnight job)

1. `nvidia-smi` works and `xgboost` can train a tiny model with `device="cuda"`. If not, report it, fall back to CPU, and note it in the log.
2. Earthdata login works (`earthaccess.login()` non-interactive) and POWER responds for one test point.
3. **Smoke run:** 3 areas, 2 years, 10 minutes. All stages run concurrently, a model is produced, and a report is written. Fix problems before the long run.
4. Confirm free disk space is above the budget and the manifest resumes correctly after a deliberate kill.

## 8. Overnight runbook

```
uv run python -m ml.run_overnight --config ml/config.yaml --hours 8
```

- Writes `ml/runs/<timestamp>/report.md` at the end (and every hour): rows per area, sources fetched vs failed, feature missingness table, metrics vs baselines, interval coverage, top features, errors and retries, GPU/CPU used, wall-clock per stage.
- Must resume cleanly if started again (manifest skips finished items).
- Must exit with a clear status code and a final summary line.

## 9. Interface for later backend integration (define now, do not wire up)

```python
def predict(features: "pd.DataFrame") -> list[dict]:
    """One dict per row:
    {"area_id": str, "week": str, "horizon_weeks": int,
     "ndvi_p10": float, "ndvi_p50": float, "ndvi_p90": float,
     "top_features": [{"name": str, "contribution": float}, ...],  # top 5
     "model_version": str, "schema_version": int}
    """
```

Load the model lazily, validate columns against `feature_schema.json`, and raise clear errors on mismatches. Do not add caching or any map-specific output yet. That comes in a later task.

## 10. Definition of done

- [ ] Plan and `DECISIONS.md` exist; `data-load/` reused, not rewritten.
- [ ] All four stages run concurrently with bounded queues, resume, and graceful shutdown.
- [ ] GPU XGBoost trains, with quantile models, grouped and temporal validation, and both baselines.
- [ ] Preflight and smoke run pass; tests pass (`uv run pytest`).
- [ ] Artifacts and `report.md` are produced; nothing large or secret is committed.
- [ ] `predict()` exists with the schema above.