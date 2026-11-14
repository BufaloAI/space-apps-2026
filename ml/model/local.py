"""Yerel (çiftçi / saha / sensör) verisi: modelin doğruluğunu ve güvenini artırmak için.

CSV biçimi (`ml/local_obs.example.csv`):
    area_id,date,variable,value
    ia_story,2025-07-14,ndvi,0.81           # saha/dron/sensör NDVI ölçümü (zaman serisi)
    ia_story,,irrigation,0                  # sabit tarla bilgisi (tarih boş)
    ia_story,,soil_om_pct,3.4
    ia_story,,soil_texture,silt_loam
    ia_story,,existing_rotation,corn>soybean

Üç yolla kullanılır:
1. Eğitim: yerel NDVI ölçümleri, ilgili satırın hedefini değiştirir ve `local_weight` ağırlığıyla eğitime girer.
2. Kalibrasyon: eğitilmiş modelin yerel ölçümlerdeki hatasından alan bazlı sapma (bias) ve aralık genişliği
   hesaplanır (`calibration.json`). Ölçüm sayısı arttıkça düzeltme ve güven artar (n / (n + 5)).
3. Rotasyon: sulama, toprak organik maddesi, doku, eğim, mevcut rotasyon gibi sabit bilgiler puanlamaya girer.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

SERIES_VARS = {"ndvi"}
SHRINK_K = 5.0  # n yerel ölçümde düzeltmenin ağırlığı n/(n+K)


def load_local(path: str | Path) -> pd.DataFrame:
    d = pd.read_csv(path, dtype={"area_id": "string", "variable": "string", "value": "string"})
    d["date"] = pd.to_datetime(d.get("date"), errors="coerce")
    d["variable"] = d["variable"].str.strip().str.lower()
    return d


def area_profile(local: pd.DataFrame | None, area_id: str) -> dict:
    """Sabit tarla bilgileri (tarihi boş satırlar). Sayıya çevrilebilenler float olur."""
    if local is None:
        return {}
    s = local[(local["area_id"] == area_id) & local["date"].isna()]
    out = {}
    for _, r in s.iterrows():
        v = r["value"]
        try:
            out[r["variable"]] = float(v)
        except (TypeError, ValueError):
            out[r["variable"]] = str(v).strip()
    return out


def _ndvi_obs(local: pd.DataFrame) -> pd.DataFrame:
    o = local[local["variable"].isin(SERIES_VARS) & local["date"].notna()].copy()
    o["value"] = pd.to_numeric(o["value"], errors="coerce")
    o = o[o["value"].between(-1, 1)]
    o["target_week"] = o["date"].dt.normalize() - pd.to_timedelta(o["date"].dt.day_of_week, unit="D")
    return o.groupby(["area_id", "target_week"], as_index=False)["value"].mean()


def merge_local_targets(df: pd.DataFrame, local: pd.DataFrame, weight: float = 3.0):
    """Yerel NDVI ölçümünü 'h hafta önceki' özellik satırının hedefi yapar (uydu hedefinin yerine)."""
    df = df.copy()
    if "weight" not in df:
        df["weight"] = 1.0
    df["is_local"] = 0.0
    o = _ndvi_obs(local)
    if o.empty:
        return df, {"used": False, "matched": 0, "unmatched": 0}
    h = pd.to_timedelta(df["horizon_weeks"] * 7, unit="D")
    key = pd.DataFrame({"area_id": df["area_id"].astype(str), "target_week": df["week"] + h, "_i": df.index})
    m = key.merge(o.assign(area_id=o["area_id"].astype(str)), on=["area_id", "target_week"], how="inner")
    df.loc[m["_i"], "target_ndvi"] = m["value"].to_numpy()
    df.loc[m["_i"], "target_observed"] = 1.0
    df.loc[m["_i"], "weight"] = weight
    df.loc[m["_i"], "is_local"] = 1.0
    return df, {"used": True, "matched": int(len(m)), "unmatched": int(len(o) - len(m)), "weight": weight}


def calibrate(model_dir: str | Path, features_df: pd.DataFrame, local: pd.DataFrame) -> dict:
    """Yerel NDVI ölçümleriyle alan bazlı sapma + aralık kalibrasyonu yazar (calibration.json)."""
    from .predict import Model  # döngüsel içe aktarmayı önle

    model = Model.load(model_dir)
    o = _ndvi_obs(local)
    h = pd.to_timedelta(features_df["horizon_weeks"] * 7, unit="D")
    key = pd.DataFrame({"area_id": features_df["area_id"].astype(str), "target_week": features_df["week"] + h,
                        "_i": features_df.index})
    m = key.merge(o.assign(area_id=o["area_id"].astype(str)), on=["area_id", "target_week"], how="inner")
    cal = {}
    if m.empty:
        return cal
    rows = features_df.loc[m["_i"]]
    q = model.raw_quantiles(rows)
    y = m["value"].to_numpy()
    for aid in m["area_id"].unique():
        sel = (m["area_id"] == aid).to_numpy()
        n = int(sel.sum())
        resid = y[sel] - q[sel, 1]
        shrink = n / (n + SHRINK_K)
        bias = float(np.mean(resid) * shrink)
        e = np.maximum(q[sel, 0] + bias - y[sel], y[sel] - (q[sel, -1] + bias))
        qa = float(max(0.0, np.quantile(e, 0.8))) if n >= 5 else model.schema.get("conformal_q", 0.0)
        cal[aid] = {"n": n, "bias": bias, "q": qa, "weight": shrink,
                    "mae_before": float(np.mean(np.abs(resid))),
                    "mae_after": float(np.mean(np.abs(y[sel] - (q[sel, 1] + bias))))}
    Path(model_dir, "calibration.json").write_text(json.dumps(cal, indent=1))
    return cal
