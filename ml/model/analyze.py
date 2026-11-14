"""Bir alan için uçtan uca sonuç: NDVI tahmini (p10/p50/p90) + risk skoru + rotasyon önerileri.

    uv run python -m ml.model.analyze --features ml/cache/features.parquet --area ia_story \
        --existing corn>corn [--local ml/local_obs.csv] [--week 2025-07-07] [--priorities water=2,income=1]
    uv run python -m ml.model.analyze --calibrate --features ... --local ml/local_obs.csv

`to_layers()` sonucu backend'in `Layer` alanlarına (name, label, unit, mean, min, max, confidence, note)
çevirir; backend entegrasyonu ayrıca yapılacak (backend/ klasörüne dokunulmadı).
"""
from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd

from . import local as L
from . import risk as R
from . import rotation as ROT
from .predict import Model


def pick_row(area: pd.DataFrame, week: str | None) -> pd.Series:
    a = area.sort_values("week")
    if week:
        w = pd.Timestamp(week)
        w = w - pd.Timedelta(days=w.weekday())
        r = a[a["week"] == w]
        if r.empty:
            raise ValueError(f"{w.date()} haftası bu alanda yok ({a['week'].min().date()}..{a['week'].max().date()})")
        return r.iloc[0]
    ok = a[a["ndvi"].notna()] if "ndvi" in a else a
    return (ok if len(ok) else a).iloc[-1]


def analyze_area(features: pd.DataFrame, area_id: str, week: str | None = None, existing=None,
                 priorities: dict | None = None, local: pd.DataFrame | None = None,
                 model: Model | None = None) -> dict:
    model = model or Model.load()
    area = features[features["area_id"] == area_id]
    if area.empty:
        raise ValueError(f"Alan yok: {area_id}. Var olanlar: {sorted(features['area_id'].unique())[:20]}")
    row = pick_row(area, week)
    pred = model.predict(row.to_frame().T.astype({c: "float64" for c in model.features}))[0]
    ref = model.area_reference.loc[area_id] if area_id in model.area_reference.index else None
    if ref is None and len(model.area_reference):
        ref = model.area_reference.median()  # yeni alan: eğitim alanlarının medyanı (güven düşer)
    coverage = float(row.get("_coverage", 1.0)) if pd.notna(row.get("_coverage", np.nan)) else 0.5
    srisk = R.short_term_risk(row, pred, model.climatology, ref, coverage)
    cond = R.area_climate(area)
    profile = L.area_profile(local, area_id)
    rot = ROT.recommend(cond, existing, priorities, profile, srisk, model_confidence=srisk["confidence"])
    return {
        "area_id": area_id, "week": pred["week"], "forecast": pred, "risk": srisk,
        "area_conditions": cond, "local_profile": profile, "rotations": rot,
        "synthetic": pred["synthetic_model"],
    }


def to_layers(result: dict) -> list[dict]:
    """Backend `Layer` biçimine (sayısal özet). confidence 0..1."""
    f, r = result["forecast"], result["risk"]
    note_syn = " (SENTETİK veriyle eğitilmiş model)" if result.get("synthetic") else ""
    conf = r["confidence"]
    layers = [{
        "name": "ndvi_tahmin", "label": f"{f['horizon_weeks']} hafta sonraki bitki örtüsü (NDVI)", "unit": "NDVI",
        "mean": f["ndvi_p50"], "min": f["ndvi_p10"], "max": f["ndvi_p90"], "confidence": conf,
        "note": "aralık %80 tahmin aralığıdır" + note_syn,
    }]
    if r["overall"] is not None and np.isfinite(r["overall"]):
        layers.append({"name": "risk", "label": "Genel risk skoru", "unit": "puan", "mean": r["overall_0_100"],
                       "min": 0.0, "max": 100.0, "confidence": conf,
                       "note": ", ".join(f"{k}: {v['level']}" for k, v in r["components"].items()) + note_syn})
    for rec in result["rotations"]["recommended"]:
        layers.append({"name": f"rotasyon_{rec['key']}", "label": f"Rotasyon önerisi #{rec['rank']}: {rec['name']}",
                       "unit": "puan", "mean": round(100 * rec["score"], 1), "min": 0.0, "max": 100.0,
                       "confidence": result["rotations"]["confidence"],
                       "note": "; ".join(rec["why"] + rec["benefits"][:2]) + note_syn})
    return layers


def _priorities(s: str | None) -> dict:
    if not s:
        return {}
    return {k.strip(): float(v) for k, v in (p.split("=") for p in s.split(","))}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--features", default="ml/cache/features.parquet")
    ap.add_argument("--area")
    ap.add_argument("--week")
    ap.add_argument("--existing", help="mevcut rotasyon: corn>soybean ya da corn_soy")
    ap.add_argument("--priorities", help="water=2,soil_health=1,income=1,erosion=1,forage=0.3")
    ap.add_argument("--local")
    ap.add_argument("--model")
    ap.add_argument("--calibrate", action="store_true", help="yerel NDVI ölçümleriyle modeli kalibre et")
    ap.add_argument("--layers", action="store_true", help="yalnızca backend Layer biçimini yaz")
    a = ap.parse_args(argv)
    feats = pd.read_parquet(a.features)
    local = L.load_local(a.local) if a.local else None
    model = Model.load(a.model)
    if a.calibrate:
        if local is None:
            ap.error("--calibrate için --local gerekli")
        print(json.dumps(L.calibrate(model.path, feats, local), indent=1))
        return
    if not a.area:
        ap.error("--area gerekli")
    res = analyze_area(feats, a.area, a.week, a.existing, _priorities(a.priorities), local, model)
    out = to_layers(res) if a.layers else res
    print(json.dumps(out, indent=1, ensure_ascii=False, default=lambda x: None if x != x else float(x)))


if __name__ == "__main__":
    main()
