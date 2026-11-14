"""Tarlanın zamanla değişmeyen (ya da yılda bir değişen) bilgileri -> haftalık özellik tablosuna sütun.

Kaynaklar (`ml/cache/static/`):
- soil.parquet (SSURGO): organik madde, kil/kum, su tutma, pH, geçirgenlik, eğim, hidrolojik grup, drenaj
- cdl_history.parquet (USDA CDL): her yılın ürünü

SIZINTI KURALI: bir yılın CDL ürünü ancak ertesi yıl yayınlanır; canlı tahminde "bu yılın ürünü" bilinmez.
Bu yüzden yalnızca GEÇMİŞ yılların ürünleri ve onlardan çıkan "beklenen ürün" kullanılır (eğitim ve canlı
tahminde aynı şekilde hesaplanabilir).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

# Ürün kodları sabit (model şeması bunlara bağlı; yalnızca SONA ekleyin).
CROP_CODES = {"corn": 1, "soybean": 2, "winter_wheat": 3, "sorghum": 4, "cotton": 5, "sunflower": 6,
              "chickpea": 7, "alfalfa": 8, "fallow": 9, "other_crop": 10}
LEGUMES = {"soybean", "chickpea", "alfalfa"}
HYDGRP = {"A": 1.0, "B": 2.0, "C": 3.0, "D": 4.0}  # "C/D" -> 3.5 (ortalama)
DRAINAGE = {"excessively drained": 1, "somewhat excessively drained": 2, "well drained": 3,
            "moderately well drained": 4, "somewhat poorly drained": 5, "poorly drained": 6,
            "very poorly drained": 7}
SOIL_NUM = ["om_pct", "clay_pct", "sand_pct", "awc_cm_cm", "ph", "ksat_um_s", "slope_pct"]


def _main(key) -> str | None:
    if key is None or (isinstance(key, float) and np.isnan(key)) or pd.isna(key):
        return None
    k = str(key).split(">")[0]  # çift ürün (winter_wheat>soybean) -> ilk ürün
    return k if k in CROP_CODES else "other_crop"


def hydgrp_code(v) -> float:
    if v is None or pd.isna(v):
        return np.nan
    parts = [HYDGRP.get(p.strip().upper()) for p in str(v).split("/")]
    parts = [p for p in parts if p is not None]
    return float(np.mean(parts)) if parts else np.nan


def soil_features(soil: dict | None) -> dict:
    """SSURGO sözlüğü (fields.soil_at çıktısı) -> sayısal özellikler (eksik = NaN)."""
    s = soil or {}
    out = {f"soil_{k}": float(s[k]) if s.get(k) is not None and not pd.isna(s.get(k)) else np.nan for k in SOIL_NUM}
    out["soil_hydgrp"] = hydgrp_code(s.get("hydgrp"))
    d = s.get("drainagecl")
    out["soil_drainage"] = float(DRAINAGE.get(str(d).strip().lower(), np.nan)) if d is not None and not pd.isna(d) \
        else np.nan
    return out


def crop_features(history: dict[int, str | None], year: int) -> dict:
    """Bir yıl için geçmiş ürün özellikleri. history: {yıl: crop_key}. Yalnızca year-1 ve öncesi kullanılır."""
    p1, p2 = _main(history.get(year - 1)), _main(history.get(year - 2))
    # beklenen ürün: son iki yıl farklıysa dönüşüm sürüyor varsayılır (2 yıl önceki tekrar gelir), aynıysa devam
    if p1 and p2:
        expected = p2 if p1 != p2 else p1
    else:
        expected = p1
    return {
        "crop_prev1": float(CROP_CODES[p1]) if p1 else np.nan,
        "crop_prev2": float(CROP_CODES[p2]) if p2 else np.nan,
        "crop_expected": float(CROP_CODES[expected]) if expected else np.nan,
        "prev1_legume": float(p1 in LEGUMES) if p1 else np.nan,
    }


def add_static_features(feat: pd.DataFrame, static_dir: str | Path | None = None,
                        soil: dict[str, dict] | None = None,
                        cdl: dict[str, dict[int, str | None]] | None = None) -> tuple[pd.DataFrame, dict]:
    """Haftalık tabloya toprak ve ürün geçmişi sütunlarını ekler.

    Ya `static_dir`'den (eğitim: parquet dosyaları) ya da doğrudan `soil`/`cdl` sözlüklerinden (canlı parsel)
    okur. Veri olmayan alan için sütunlar NaN olur (XGBoost yönetir). Dönen rapor: hangi alanlarda ne eksik."""
    soil = dict(soil or {})
    cdl = {k: dict(v) for k, v in (cdl or {}).items()}
    rep = {"soil_areas": 0, "cdl_areas": 0, "missing_soil": [], "missing_cdl": []}
    if static_dir:
        sd = Path(static_dir)
        if (sd / "soil.parquet").exists():
            for r in pd.read_parquet(sd / "soil.parquet").to_dict("records"):
                soil.setdefault(str(r["area_id"]), r)
        if (sd / "cdl_history.parquet").exists():
            h = pd.read_parquet(sd / "cdl_history.parquet")
            for aid, g in h.groupby("area_id"):
                cdl.setdefault(str(aid), {int(y): (None if pd.isna(k) else str(k))
                                          for y, k in zip(g["year"], g["crop_key"])})
    out = feat.copy()
    rows = []
    for aid, year in zip(out["area_id"].astype(str), out["week"].dt.year):
        rows.append({**soil_features(soil.get(aid)), **crop_features(cdl.get(aid, {}), int(year))})
    extra = pd.DataFrame(rows, index=out.index).astype("float64")
    for c in extra.columns:
        out[c] = extra[c]
    areas = set(out["area_id"].astype(str))
    rep["soil_areas"] = len(areas & set(soil))
    rep["cdl_areas"] = len(areas & set(cdl))
    rep["missing_soil"] = sorted(areas - set(soil))
    rep["missing_cdl"] = sorted(areas - set(cdl))
    return out, rep
