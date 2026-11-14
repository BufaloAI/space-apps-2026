"""XGBoost eğitimi: 2 hafta sonraki NDVI (nokta + p10/p50/p90 kantil), bazlarla karşılaştırma.

    uv run python -m ml.model.train --features ml/cache/features.parquet [--local ml/local_obs.csv]

Doğrulama: rastgele bölme YOK. (1) Zamansal: eğitim < valid_start <= doğrulama < test_start <= test.
(2) Alan bazlı: alanların bir kısmı tamamen dışarıda tutulur (GroupKFold benzeri).
Bazlar: kalıcılık (gelecek NDVI = bugünkü NDVI) ve mevsimsel klimatoloji (alan x yılın haftası ortalaması).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import shutil
import subprocess
import tomllib
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb

from ml.data_load import features as F
from ml.data_load import prune as P
from ml.data_load.build import DEFAULT_CONFIG, load_config

from . import local as L

log = logging.getLogger("ml.train")
ML_DIR = Path(__file__).resolve().parents[1]
RUNS_DIR = ML_DIR / "runs"
TARGET = "target_ndvi"
FIT = "_y"  # modelin gördüğü hedef: "delta" modunda (hedef - bugünkü NDVI), "absolute" modunda hedefin kendisi


def fit_target(df: pd.DataFrame, mode: str) -> pd.Series:
    return df[TARGET] - df["ndvi"] if mode == "delta" else df[TARGET]


def base_of(df: pd.DataFrame, mode: str) -> np.ndarray:
    return df["ndvi"].to_numpy("float64") if mode == "delta" else np.zeros(len(df))


# ---------------------------------------------------------------- cihaz
def pick_device(pref: str = "auto") -> tuple[str, str]:
    """('cuda'|'cpu', açıklama). CUDA yoksa sessizce devam etmez, nedeni döner (log'a yazılır)."""
    if pref == "cpu":
        return "cpu", "config ile CPU seçildi"
    has_cuda_build = bool(xgb.build_info().get("USE_CUDA"))
    smi = shutil.which("nvidia-smi")
    gpu_ok = False
    if has_cuda_build and smi:
        try:
            gpu_ok = subprocess.run([smi, "-L"], capture_output=True, timeout=10).returncode == 0
        except Exception:  # noqa: BLE001
            gpu_ok = False
    if gpu_ok:
        return "cuda", "NVIDIA GPU bulundu"
    why = "xgboost CUDA'sız derlenmiş" if not has_cuda_build else "nvidia-smi yok/çalışmıyor"
    if pref == "cuda":
        raise RuntimeError(f"device=cuda istendi ama GPU kullanılamıyor: {why}")
    return "cpu", f"GPU yok, CPU 'hist' kullanılıyor ({why})"


# ---------------------------------------------------------------- yardımcılar
def rmse(y, p):
    m = np.isfinite(y) & np.isfinite(p)
    return float(np.sqrt(np.mean((y[m] - p[m]) ** 2))) if m.any() else float("nan")


def mae(y, p):
    m = np.isfinite(y) & np.isfinite(p)
    return float(np.mean(np.abs(y[m] - p[m]))) if m.any() else float("nan")


def climatology(train: pd.DataFrame) -> pd.DataFrame:
    """Alan x yılın haftası hedef ortalaması/std'si (yalnızca eğitimden)."""
    d = train[["area_id", "week", TARGET]].copy()
    d["woy"] = (d["week"] + pd.to_timedelta(d.get("horizon_weeks", 2) * 7, unit="D")).dt.isocalendar().week.astype(int)
    c = d.groupby(["area_id", "woy"])[TARGET].agg(["mean", "std", "count"]).reset_index()
    return c.rename(columns={"mean": "clim_mean", "std": "clim_std", "count": "clim_n"})


def clim_predict(df: pd.DataFrame, clim: pd.DataFrame) -> np.ndarray:
    woy = (df["week"] + pd.to_timedelta(df["horizon_weeks"] * 7, unit="D")).dt.isocalendar().week.astype(int)
    key = pd.DataFrame({"area_id": df["area_id"].to_numpy(), "woy": woy.to_numpy()})
    m = key.merge(clim, on=["area_id", "woy"], how="left")["clim_mean"].to_numpy()
    # komşu haftalar (±1) ve alan ortalamasıyla doldur
    if np.isnan(m).any():
        sm = clim.sort_values(["area_id", "woy"]).copy()
        for shift in (1, -1, 2, -2):
            k2 = key.copy()
            k2["woy"] = (k2["woy"] - 1 + shift) % 53 + 1
            alt = k2.merge(clim, on=["area_id", "woy"], how="left")["clim_mean"].to_numpy()
            m = np.where(np.isnan(m), alt, m)
        amean = key.merge(sm.groupby("area_id")["clim_mean"].mean().rename("am").reset_index(), on="area_id",
                          how="left")["am"].to_numpy()
        m = np.where(np.isnan(m), amean, m)
    return m


def time_split(df: pd.DataFrame, valid_start: str, test_start: str):
    """Hedef haftasına göre böler; eğitim hedefi doğrulama dönemine taşmaz (purge)."""
    tw = df["week"] + pd.to_timedelta(df["horizon_weeks"] * 7, unit="D")
    vs, ts = pd.Timestamp(valid_start), pd.Timestamp(test_start)
    return df[tw < vs], df[(df["week"] >= vs) & (tw < ts)], df[df["week"] >= ts]


def train_models(train, valid, feats, params, device, rounds, es, quantiles, weight_col="weight"):
    def dm(d, ref=None):
        w = d[weight_col].to_numpy() if weight_col in d else None
        return xgb.QuantileDMatrix(d[feats], d[FIT], weight=w, ref=ref, missing=np.nan)

    dtr = dm(train)
    dva = dm(valid, ref=dtr)
    p = dict(params, device=device)
    point = xgb.train(p, dtr, rounds, evals=[(dva, "valid")], early_stopping_rounds=es, verbose_eval=False)
    qp = {k: v for k, v in p.items() if k not in ("objective", "eval_metric")}
    qp.update(objective="reg:quantileerror", quantile_alpha=np.array(quantiles))
    quant = xgb.train(qp, dtr, rounds, evals=[(dva, "valid")], early_stopping_rounds=es, verbose_eval=False)
    return point, quant


def predict_quantiles(model: xgb.Booster, X: pd.DataFrame) -> np.ndarray:
    q = model.predict(xgb.DMatrix(X, missing=np.nan), iteration_range=(0, model.best_iteration + 1))
    return np.sort(q.reshape(len(X), -1), axis=1)  # kantil kesişmesini düzelt


def conformal_q(y, q, coverage=0.8) -> float:
    """CQR: [p10-Q, p90+Q] aralığı doğrulamada hedef kapsamaya ulaşsın diye Q."""
    m = np.isfinite(y)
    if m.sum() < 5:
        return 0.0
    e = np.maximum(q[m, 0] - y[m], y[m] - q[m, -1])
    n = m.sum()
    return float(max(0.0, np.quantile(e, min(1.0, coverage * (1 + 1 / n)))))


def interval_stats(y, lo, hi):
    m = np.isfinite(y)
    return {"coverage": float(np.mean((y[m] >= lo[m]) & (y[m] <= hi[m]))) if m.any() else float("nan"),
            "mean_width": float(np.mean(hi[m] - lo[m])) if m.any() else float("nan")}


def evaluate(name, d, point, quant, feats, clim, cq, mode="delta"):
    if len(d) == 0:
        return {"n": 0}
    y = d[TARGET].to_numpy()
    b = base_of(d, mode)
    p = b + point.predict(xgb.DMatrix(d[feats], missing=np.nan), iteration_range=(0, point.best_iteration + 1))
    q = b[:, None] + predict_quantiles(quant, d[feats])
    pers = d["ndvi"].to_numpy() if "ndvi" in d else np.full(len(d), np.nan)
    cl = clim_predict(d, clim)
    res = {
        "n": int(len(d)),
        "xgb": {"rmse": rmse(y, p), "mae": mae(y, p)},
        "xgb_p50": {"rmse": rmse(y, q[:, 1]), "mae": mae(y, q[:, 1])},
        "persistence": {"rmse": rmse(y, pers), "mae": mae(y, pers)},
        "climatology": {"rmse": rmse(y, cl), "mae": mae(y, cl)},
        "interval_raw": interval_stats(y, q[:, 0], q[:, -1]),
        "interval_conformal": interval_stats(y, q[:, 0] - cq, q[:, -1] + cq),
    }
    best_base = min(res["persistence"]["rmse"], res["climatology"]["rmse"])
    res["beats_baselines"] = bool(res["xgb"]["rmse"] < best_base) if np.isfinite(best_base) else None
    log.info("%s: n=%d xgb=%.4f pers=%.4f clim=%.4f", name, res["n"], res["xgb"]["rmse"],
             res["persistence"]["rmse"], res["climatology"]["rmse"])
    return res


def area_cv(df, feats, params, device, rounds, k, mode="delta"):
    """Alanları tamamen dışarıda tutarak değerlendirme (tüm yıllar). Sabit tur sayısıyla."""
    areas = np.array(sorted(df["area_id"].unique()))
    if len(areas) < 2:
        return {"folds": 0, "note": "tek alan, alan bazlı değerlendirme yapılamadı"}
    k = min(k, len(areas))
    folds = np.array_split(np.random.default_rng(0).permutation(areas), k)
    out = []
    for hold in folds:
        tr, te = df[~df["area_id"].isin(hold)], df[df["area_id"].isin(hold)]
        b = xgb.train(dict(params, device=device), xgb.QuantileDMatrix(tr[feats], tr[FIT], missing=np.nan), rounds)
        p = base_of(te, mode) + b.predict(xgb.DMatrix(te[feats], missing=np.nan))
        y = te[TARGET].to_numpy()
        out.append({"held_out": hold.tolist(), "n": len(te), "xgb_rmse": rmse(y, p),
                    "persistence_rmse": rmse(y, te["ndvi"].to_numpy())})
    return {"folds": k, "detail": out,
            "xgb_rmse_mean": float(np.nanmean([o["xgb_rmse"] for o in out])),
            "persistence_rmse_mean": float(np.nanmean([o["persistence_rmse"] for o in out]))}


def git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=ML_DIR,
                              timeout=5).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


# ---------------------------------------------------------------- ana akış
def run(features_path: str, cfg: dict, local_path: str | None = None, out_dir: Path | None = None,
        set_latest: bool = True) -> Path:
    """Eğitir ve çıktıları yazar. set_latest=True ise ml/runs/LATEST bu modeli gösterir (testlerde False)."""
    df = pd.read_parquet(features_path)
    sc, mc, pc = cfg.get("split", {}), cfg.get("model", {}), cfg.get("prune", {})
    synthetic = bool(json.loads(Path(features_path).with_suffix(".report.json").read_text()).get("synthetic", False)) \
        if Path(features_path).with_suffix(".report.json").exists() else None

    df["weight"] = 1.0
    local_info = {"used": False}
    if local_path:
        df, local_info = L.merge_local_targets(df, L.load_local(local_path), weight=mc.get("local_weight", 3.0))

    feats_all = F.feature_columns(df)
    mode = mc.get("target_mode", "delta")
    if TARGET not in df or "ndvi" not in df or df.get("target_observed", pd.Series(dtype=float)).sum() == 0:
        raise RuntimeError(
            "Özellik tablosunda NDVI hedefi yok: MODIS NDVI verisi hiç inmemiş (ml/cache/raw/MOD13Q1.parquet "
            "yok ya da boş). İndirmeyi yalnızca NDVI için tekrar çalıştırın (var olan veriye eklenir):\n"
            "  uv run python -m ml.data_load.download --areas <alanlar.json> --sources modis --workers 1 "
            "--out ml/cache/raw\nsonra ml.data_load.build'i tekrar çalıştırın.")
    df = df[df[TARGET].notna() & (df["target_observed"] > 0)].copy()
    df[FIT] = fit_target(df, mode)
    df = df[df[FIT].notna()]
    train, valid, test = time_split(df, sc.get("valid_start", "2025-04-01"), sc.get("test_start", "2025-07-01"))
    if len(valid) < 10:  # doğrulama çok küçükse eğitimin son %15'i
        cut = train["week"].quantile(0.85)
        train, valid = train[train["week"] < cut], train[train["week"] >= cut]
    if len(train) < 30:
        raise RuntimeError(f"Eğitim için çok az satır: {len(train)} (gözlenmiş hedefli). Daha fazla alan/tarih gerekli.")

    plan = P.fit_prune(train, feats_all, pc.get("col_missing_max", 0.5), pc.get("corr_max", 0.95))
    feats = plan.kept
    n0 = len(train)
    train = P.drop_sparse_rows(train, feats, pc.get("row_missing_max", 0.5))
    n1 = len(train)
    train = P.drop_near_duplicates(train, feats, FIT, pc.get("near_dup_bins", 50))
    n2 = len(train)

    device, why = pick_device(mc.get("device", "auto"))
    log.info("cihaz: %s (%s)", device, why)
    params = dict(mc.get("params", {}))
    rounds, es = mc.get("num_boost_round", 3000), mc.get("early_stopping_rounds", 100)
    quantiles = mc.get("quantiles", [0.1, 0.5, 0.9])
    point, quant = train_models(train, valid, feats, params, device, rounds, es, quantiles)

    clim = climatology(train)
    qv = base_of(valid, mode)[:, None] + predict_quantiles(quant, valid[feats])
    cq = conformal_q(valid[TARGET].to_numpy(), qv, coverage=quantiles[-1] - quantiles[0])

    metrics = {
        "synthetic_input": synthetic,
        "device": device, "device_note": why,
        "rows": {"train_before_prune": n0, "train_after_sparse_rows": n1, "train_after_near_dup": n2,
                 "valid": len(valid), "test": len(test)},
        "n_features": len(feats), "n_features_dropped": len(plan.dropped),
        "best_iteration": {"point": point.best_iteration, "quantile": quant.best_iteration},
        "conformal_q": cq,
        "target_mode": mode,
        "valid": evaluate("valid", valid, point, quant, feats, clim, cq, mode),
        "test_temporal": evaluate("test", test, point, quant, feats, clim, cq, mode),
        "area_holdout": area_cv(pd.concat([train, valid, test]), feats, params, device,
                                max(50, point.best_iteration + 1), sc.get("area_folds", 3), mode),
        "local": local_info,
    }

    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = out_dir or RUNS_DIR / ts
    out.mkdir(parents=True, exist_ok=True)
    point.save_model(out / "model.ubj")
    quant.save_model(out / "quantile_model.ubj")
    clim.to_parquet(out / "climatology.parquet", index=False)
    # risk eşikleri için eğitim dağılımı (alan bazlı tipik değerler)
    ref_cols = [c for c in ("precip_mm_4w", "sm_surface", "sm_rootzone", "gwet_top", "gwet_root", "heat_day_4w",
                            "frost_day_4w", "tmax_c_max", "ndvi") if c in df.columns]
    df.groupby("area_id")[ref_cols].median().to_parquet(out / "area_reference.parquet")
    imp = point.get_score(importance_type="gain")
    pd.DataFrame(sorted(imp.items(), key=lambda kv: -kv[1]), columns=["feature", "gain"]).to_csv(
        out / "importance.csv", index=False)
    schema = {
        "schema_version": F.SCHEMA_VERSION, "target": TARGET, "horizon_weeks": int(df["horizon_weeks"].iloc[0]),
        "target_mode": mode, "features": feats, "dtypes": {c: "float64" for c in feats}, "quantiles": quantiles,
        "prune": plan.to_dict(), "model_version": ts, "conformal_q": cq, "device": device,
        "synthetic_input": synthetic,
    }
    (out / "feature_schema.json").write_text(json.dumps(schema, indent=1, ensure_ascii=False))
    (out / "metrics.json").write_text(json.dumps(metrics, indent=1, ensure_ascii=False, default=float))
    (out / "config_used.toml").write_text(DEFAULT_CONFIG.read_text() if DEFAULT_CONFIG.exists() else "")
    (out / "git_commit.txt").write_text(git_commit())
    if set_latest:
        RUNS_DIR.mkdir(parents=True, exist_ok=True)  # yeni klonda ml/runs yok (.gitignore)
        (RUNS_DIR / "LATEST").write_text(str(out.resolve()))
    log.info("model kaydedildi: %s", out)
    return out


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--features", default="ml/cache/features.parquet")
    ap.add_argument("--local", help="yerel gözlem CSV'si (area_id,date,variable,value)")
    ap.add_argument("--config")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    cfg = tomllib.loads(Path(a.config).read_text()) if a.config else load_config()
    out = run(a.features, cfg, a.local, Path(a.out) if a.out else None)
    m = json.loads((out / "metrics.json").read_text())
    t = m["test_temporal"]
    if m.get("synthetic_input"):
        print("UYARI: model SENTETİK veriyle eğitildi; metrikler gerçek başarıyı göstermez.")
    if t.get("n"):
        print(f"test RMSE xgb={t['xgb']['rmse']:.4f} kalıcılık={t['persistence']['rmse']:.4f} "
              f"klimatoloji={t['climatology']['rmse']:.4f} | bazları geçti mi: {t['beats_baselines']} | "
              f"%80 aralık kapsaması={t['interval_conformal']['coverage']:.2f}")
    print(f"çıktı: {out}")


if __name__ == "__main__":
    main()
