"""Risk skorları: (1) kısa vadeli (tahmin haftası) bitki/su/sıcak/don riski, (2) uzun vadeli alan iklimi.

Bütün eşik ve karşılaştırmalar burada (sayısal kararlar backend/LLM'e bırakılmaz, ilke: architecture.md).
Skorlar 0..1; etiketler: yüksek >= 0.6, orta >= 0.3, düşük.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

Z90 = 1.2816  # standart normalde %90 kantil


def level(x: float | None) -> str:
    if x is None or not np.isfinite(x):
        return "bilinmiyor"
    return "yüksek" if x >= 0.6 else "orta" if x >= 0.3 else "düşük"


def confidence_level(c: float) -> str:  # backend Layer ile aynı eşikler
    return "yüksek" if c >= 0.8 else "orta" if c >= 0.6 else "düşük"


def _get(row, col, default=np.nan) -> float:
    v = row.get(col, default) if hasattr(row, "get") else default
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return v if np.isfinite(v) else default


def _clip01(x):
    return float(min(1.0, max(0.0, x))) if np.isfinite(x) else float("nan")


def _phi(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def short_term_risk(row: pd.Series, pred: dict | None, clim: pd.DataFrame | None, ref: pd.Series | None,
                    coverage: float = 1.0, horizon_weeks: int = 2) -> dict:
    """Bir alan-hafta için risk bileşenleri + genel skor + güven. `pred` yoksa (NDVI alınamadı)
    bitki stresi hesaplanmaz, diğer bileşenler iklim verisinden hesaplanır."""
    if pred is not None:
        p10, p50, p90 = pred["ndvi_p10"], pred["ndvi_p50"], pred["ndvi_p90"]
        horizon_weeks = int(pred["horizon_weeks"])
    else:
        p10 = p50 = p90 = float("nan")
    sigma = max(1e-3, (p90 - p10) / (2 * Z90)) if pred is not None else 1.0
    target_week = pd.Timestamp(row["week"]) + pd.Timedelta(weeks=horizon_weeks)
    woy = int(target_week.isocalendar().week)
    normal = np.nan
    if clim is not None and len(clim):
        c = clim[(clim["area_id"] == row["area_id"]) & (clim["woy"].between(woy - 1, woy + 1))]
        if len(c):
            normal = float(c["clim_mean"].mean())
    now = _get(row, "ndvi")
    # bitki stresi: tahmini NDVI'nın "normal - 0.1"in (normal yoksa bugünkü - 0.1) altına düşme olasılığı
    thr = (normal if np.isfinite(normal) else now) - 0.10
    veg = _phi((thr - p50) / sigma) if (np.isfinite(thr) and np.isfinite(p50)) else float("nan")

    # su stresi: son 4 hafta yağışı alanın tipik değerine göre + toprak nemi anomalisi
    parts = []
    p4, p4ref = _get(row, "precip_mm_4w"), _get(ref, "precip_mm_4w") if ref is not None else np.nan
    if np.isfinite(p4) and np.isfinite(p4ref) and p4ref > 0:
        parts.append(_clip01(1 - p4 / p4ref))
    for sm in ("sm_rootzone", "sm_surface", "gwet_root", "gwet_top"):
        v, r = _get(row, sm), _get(ref, sm) if ref is not None else np.nan
        if np.isfinite(v) and np.isfinite(r) and r > 0:
            parts.append(_clip01((r - v) / (0.5 * r)))
            break
    water = float(np.mean(parts)) if parts else float("nan")

    hd = _get(row, "heat_day_4w")
    heat = _clip01(hd / 10) if np.isfinite(hd) else _clip01((_get(row, "tmax_c_max") - 32) / 8)
    fd = _get(row, "frost_day_4w")
    in_season = 14 <= woy <= 40  # kabaca nisan-eylül
    frost = _clip01(fd / 4) * (1.0 if in_season else 0.3) if np.isfinite(fd) else float("nan")

    comps = {"vegetation_stress": veg, "water_stress": water, "heat_stress": heat, "frost_risk": frost}
    weights = {"vegetation_stress": 0.4, "water_stress": 0.3, "heat_stress": 0.2, "frost_risk": 0.1}
    avail = {k: v for k, v in comps.items() if np.isfinite(v)}
    overall = sum(weights[k] * v for k, v in avail.items()) / sum(weights[k] for k in avail) if avail else float("nan")

    # güven: tahmin aralığı darlığı + veri kapsamı + bileşen bulunurluğu + yerel kalibrasyon
    width_score = _clip01(1 - (p90 - p10) / 0.30) if pred is not None else 0.0
    pred = pred or {}
    n_local = pred.get("n_local_obs", 0)
    conf = (0.45 * width_score + 0.30 * _clip01(coverage) + 0.15 * len(avail) / len(comps)
            + 0.10 * n_local / (n_local + 5))
    if pred.get("synthetic_model"):
        conf = min(conf, 0.3)
    return {
        "overall": overall, "overall_0_100": round(100 * overall, 1) if np.isfinite(overall) else None,
        "level": level(overall),
        "components": {k: {"score": (round(v, 3) if np.isfinite(v) else None), "level": level(v)}
                       for k, v in comps.items()},
        "ndvi_now": now if np.isfinite(now) else None,
        "ndvi_normal": normal if np.isfinite(normal) else None,
        "confidence": round(conf, 3), "confidence_level": confidence_level(conf),
        "missing_components": [k for k in comps if k not in avail],
    }


def seasonal_reference(feats: pd.DataFrame, week) -> pd.Series:
    """Su stresi karşılaştırması için AYNI MEVSİMİN (±3 hafta, geçmiş yıllar) medyanları; yeterli veri yoksa tüm
    geçmişin medyanı. (Yıllık medyanla 4 haftalık yağışı karşılaştırmak kışın stresi abartır.)"""
    w = pd.Timestamp(week)
    woy = int(w.isocalendar().week)
    fw = feats["week"].dt.isocalendar().week.astype(int)
    near = np.minimum(abs(fw - woy), 52 - abs(fw - woy)) <= 3
    same = feats[near & (feats["week"] < w)]
    return same.median(numeric_only=True) if len(same) >= 3 else feats.median(numeric_only=True)


def area_climate(area_feats: pd.DataFrame) -> dict:
    """Alanın birkaç yıllık geçmişinden uzun vadeli koşullar (rotasyon puanlaması için)."""
    d = area_feats.copy()
    d["month"] = d["week"].dt.month
    summer = d[d["month"].between(5, 9)]
    cnt = d.groupby("year").size()
    years = cnt[cnt >= 26].index  # yarım yılı (örn. 1 haftalık Aralık başı) yıl sayma
    out = {"years": sorted(int(y) for y in years), "n_weeks": int(len(d))}

    def yearly(col, frame, how="sum"):
        if col not in frame or frame[col].notna().sum() == 0:
            return float("nan")
        g = frame.groupby("year")[col]
        return float((g.sum(min_count=1) if how == "sum" else g.max()).mean())

    out["summer_precip_mm"] = yearly("precip_mm", summer)
    if "precip_mm" in summer and summer["precip_mm"].notna().any():  # yıllar arası en kurak / en yağışlı yaz
        full = summer.groupby("year")["precip_mm"].agg(["sum", "count"])
        full = full[full["count"] >= 18]["sum"]  # mayıs-eylülün çoğu gözlenmiş yıllar
        if len(full):
            out["summer_precip_min_mm"], out["summer_precip_max_mm"] = float(full.min()), float(full.max())
    out["annual_gdd"] = yearly("gdd_ytd", d, how="max") if "gdd_ytd" in d else yearly("gdd", d)
    out["summer_heat_days"] = yearly("heat_day", summer)
    spring = d[d["month"].between(5, 6)]  # ekimden sonraki geç donlar (nisan donları ekimden önce, zarar vermez)
    out["spring_frost_days"] = yearly("frost_day", spring)
    out["winter_min_c"] = float(d.loc[d["month"].isin([12, 1, 2]), "tmin_c_min"].min()) \
        if "tmin_c_min" in d else float("nan")
    out["p95_daily_precip_mm"] = float(d["precip_max_day"].quantile(0.95)) if "precip_max_day" in d else float("nan")
    sm_col = next((c for c in ("sm_rootzone", "gwet_root", "sm_surface", "gwet_top") if c in d), None)
    out["summer_soil_moisture"] = float(summer[sm_col].mean()) if sm_col else float("nan")
    out["soil_moisture_var"] = sm_col

    # 0..1 stres göstergeleri (mutlak eşikler: ürün uygunluğu mutlak koşula bağlı)
    ws = []
    if np.isfinite(out["summer_precip_mm"]):
        ws.append(_clip01((500 - out["summer_precip_mm"]) / 350))  # 150 mm -> 1, 500 mm+ -> 0
    if sm_col and np.isfinite(out["summer_soil_moisture"]):
        lo, hi = (0.10, 0.30) if sm_col.startswith("sm_") else (0.25, 0.65)
        ws.append(_clip01((hi - out["summer_soil_moisture"]) / (hi - lo)))
    out["water_stress"] = float(np.mean(ws)) if ws else float("nan")
    out["heat_stress"] = _clip01(out["summer_heat_days"] / 30) if np.isfinite(out["summer_heat_days"]) else float("nan")
    out["frost_stress"] = _clip01(out["spring_frost_days"] / 5) if np.isfinite(out["spring_frost_days"]) else float("nan")
    out["wetness"] = _clip01((out["summer_precip_mm"] - 400) / 300) if np.isfinite(out["summer_precip_mm"]) \
        else float("nan")
    out["erosion_pressure"] = _clip01((out["p95_daily_precip_mm"] - 10) / 30) \
        if np.isfinite(out["p95_daily_precip_mm"]) else float("nan")
    return out
