"""Temiz uzun tablo -> alan x gün -> alan x hafta -> XGBoost özellik tablosu (+ hedef).

Satır anahtarı: area_id + week (haftanın pazartesisi). Bütün özellikler float64, eksik = NaN
(XGBoost NaN'ı kendisi yönetir, doldurmayız; yalnızca en fazla 2 haftalık iç boşlukları doğrusal doldururuz).
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from . import registry as reg

# 16 günlük kompozitlerin tarihi dönem başlangıcıdır; değeri dönemin ortasına taşırız.
COMPOSITE_SHIFT_DAYS = {"ndvi": 8, "evi": 8, "modis_et_mm": 4, "openet_et_mm": 15}
MINMAX_VARS = {"t2m_c", "tmax_c", "tmin_c", "sm_surface", "sm_rootzone", "gwet_top", "gwet_root", "lst_day_c"}
SUM_VARS = {"precip_mm", "et_mm", "modis_et_mm", "openet_et_mm", "gdd", "heat_day", "frost_day", "dry_day"}
SCHEMA_VERSION = 1


def to_daily(long: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Uzun tablo -> (alan x gün geniş tablo, alan merkezleri)."""
    df = long.copy()
    df["var"] = df["var"].astype(str)
    shift = df["var"].map(COMPOSITE_SHIFT_DAYS).fillna(0).astype(int)
    df["date"] = (df["time"] + pd.to_timedelta(shift, unit="D")).dt.normalize()
    centers = df.groupby("area_id")[["lat", "lon"]].mean()
    daily = df.pivot_table(index=["area_id", "date"], columns="var", values="value", aggfunc="mean", observed=True)
    daily.columns.name = None
    daily = daily.reset_index()
    return derive_daily(daily), centers


def derive_daily(d: pd.DataFrame) -> pd.DataFrame:
    """Günlük türetilmiş göstergeler: büyüme derece günü, sıcak/don/kuru gün."""
    d = d.copy()
    if {"tmax_c", "tmin_c"} <= set(d.columns):
        tx = d["tmax_c"].clip(upper=30)
        tn = d["tmin_c"].clip(lower=10)
        d["gdd"] = ((tx + tn) / 2 - 10).clip(lower=0)  # mısır yöntemi (10/30 °C)
        d["heat_day"] = (d["tmax_c"] > 35).astype("float64").where(d["tmax_c"].notna())
        d["frost_day"] = (d["tmin_c"] < 0).astype("float64").where(d["tmin_c"].notna())
    elif "t2m_c" in d.columns:
        d["gdd"] = (d["t2m_c"] - 10).clip(lower=0)
        d["frost_day"] = (d["t2m_c"] < 0).astype("float64").where(d["t2m_c"].notna())
    if "precip_mm" in d.columns:
        d["dry_day"] = (d["precip_mm"] < 1).astype("float64").where(d["precip_mm"].notna())
    return d


def to_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    d = daily.copy()
    d["week"] = d["date"] - pd.to_timedelta(d["date"].dt.day_of_week, unit="D")
    vars_ = [c for c in d.columns if c not in ("area_id", "date", "week")]
    g = d.groupby(["area_id", "week"])
    out = {}
    for v in vars_:
        if v in SUM_VARS:
            # Eksik günlere dayanıklı haftalık toplam: gözlenen günlerin ortalaması x 7
            out[v] = g[v].mean() * 7
            if v == "precip_mm":
                out["precip_max_day"] = g[v].max()
        else:
            out[v] = g[v].mean()
            if v in MINMAX_VARS:
                out[f"{v}_min"] = g[v].min()
                out[f"{v}_max"] = g[v].max()
        out[f"_n_{v}"] = g[v].count()
    w = pd.DataFrame(out).reset_index()
    return w


def build_features(weekly: pd.DataFrame, centers: pd.DataFrame, horizon_weeks: int = 2,
                   ndvi_lags: int = 8, other_lags=(1, 2, 4), max_gap_weeks: int = 2,
                   target_var: str = "ndvi") -> pd.DataFrame:
    """Haftalık tablodan gecikme/pencere/mevsim özellikleri ve hedefi üretir."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", pd.errors.PerformanceWarning)
        return _build_features(weekly, centers, horizon_weeks, ndvi_lags, other_lags, max_gap_weeks, target_var)


def _build_features(weekly, centers, horizon_weeks, ndvi_lags, other_lags, max_gap_weeks, target_var):
    frames = []
    count_cols = [c for c in weekly.columns if c.startswith("_n_")]
    base_vars = [c for c in weekly.columns if c not in ("area_id", "week") and c not in count_cols]
    for aid, a in weekly.groupby("area_id", sort=True):
        a = a.set_index("week").sort_index()
        full = pd.date_range(a.index.min(), a.index.max(), freq="7D")
        a = a.reindex(full)
        a.index.name = "week"
        observed_target = a[target_var].notna() if target_var in a else pd.Series(False, index=a.index)
        f = pd.DataFrame(index=a.index)
        # kısa iç boşlukları doldur (en fazla 2 adım), uzun boşluklar NaN kalır
        filled = a[base_vars].astype("float64").interpolate(method="linear", limit=max_gap_weeks, limit_area="inside")
        for v in base_vars:
            f[v] = filled[v]
        if target_var in filled:
            t = filled[target_var]
            for k in range(1, ndvi_lags + 1):
                f[f"{target_var}_lag{k}"] = t.shift(k)
            f[f"{target_var}_diff1"] = t - t.shift(1)
            f[f"{target_var}_slope4"] = (t - t.shift(4)) / 4
            f[f"{target_var}_max12"] = t.rolling(12, min_periods=3).max()
            f[f"{target_var}_obs"] = observed_target.astype("float64")
        for v in [v for v in base_vars if v != target_var and not v.endswith(("_min", "_max"))]:
            for k in other_lags:
                f[f"{v}_lag{k}"] = filled[v].shift(k)
        for v in ("precip_mm", "et_mm", "gdd", "heat_day", "frost_day", "dry_day"):
            if v in filled:
                f[f"{v}_4w"] = filled[v].rolling(4, min_periods=2).sum()
        if "precip_mm" in filled:
            f["precip_mm_12w"] = filled["precip_mm"].rolling(12, min_periods=6).sum()
        for v in ("sm_surface", "sm_rootzone", "gwet_top", "gwet_root", "t2m_c", "lst_day_c"):
            if v in filled:
                trail = filled[v].shift(1).rolling(12, min_periods=4).mean()
                f[f"{v}_anom12"] = filled[v] - trail  # sızıntısız anomali (yalnızca geçmiş)
        # mevsim başından birikimli toplamlar
        year = f.index.year
        for v in ("gdd", "precip_mm"):
            if v in filled:
                f[f"{v}_ytd"] = filled[v].fillna(0).groupby(year).cumsum().where(filled[v].notna())
        woy = f.index.isocalendar().week.astype("float64").to_numpy()
        f["week_sin"] = np.sin(2 * np.pi * woy / 52.18)
        f["week_cos"] = np.cos(2 * np.pi * woy / 52.18)
        f["lat"] = float(centers.loc[aid, "lat"])
        f["lon"] = float(centers.loc[aid, "lon"])
        # hedef
        if target_var in filled:
            f[f"target_{target_var}"] = filled[target_var].shift(-horizon_weeks)
            f["target_observed"] = observed_target.shift(-horizon_weeks).astype("float64").fillna(0)
        # veri kapsamı (güven skoru için): son 4 haftada kaç kaynakta gözlem var
        f["_coverage"] = (a[count_cols].fillna(0) > 0).mean(axis=1).rolling(4, min_periods=1).mean() if count_cols else 0.0
        f.insert(0, "area_id", aid)
        frames.append(f.reset_index())
    out = pd.concat(frames, ignore_index=True).copy()
    out["year"] = out["week"].dt.year
    out["horizon_weeks"] = horizon_weeks
    out["schema_version"] = SCHEMA_VERSION
    num = [c for c in out.columns if c not in ("area_id", "week")]
    out[num] = out[num].astype("float64")
    out["year"] = out["year"].astype("int64")
    out["horizon_weeks"] = out["horizon_weeks"].astype("int64")
    out["schema_version"] = out["schema_version"].astype("int64")
    return out


ID_COLS = ("area_id", "week", "year", "horizon_weeks", "schema_version")


def feature_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if c not in ID_COLS and not c.startswith(("target", "_"))]


def unit_of(col: str) -> str:
    base = col.split("_lag")[0].removesuffix("_min").removesuffix("_max")
    spec = reg.spec_for_canonical(base)
    return spec.unit if spec else ""
