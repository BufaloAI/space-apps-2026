"""Hava görünümü: tarlanın kendi koordinatı için önümüzdeki 1-4 haftanın sıcaklık / yağış / GDD / don / sıcak günleri.

Yöntem (istatistiksel, GPU/eğitim gerekmez; tarla başına saniyeler):
1. NASA POWER günlük geçmişi (varsayılan 15 yıl) -> her değişken için yılın günü klimatolojisi (±7 gün yumuşatma).
2. Anomali = gözlem - klimatoloji. Son 7 ve 30 günün ortalama anomalisi tahmin edicidir.
3. Her ufuk (1..4 hafta) ve değişken için doğrusal model: haftalık anomali ~ a + b1*A7 + b2*A30
   (bu noktanın kendi geçmişinden öğrenilir). Tahmin = mevsim normali + modelin anomali beklentisi.
4. Dürüstlük: son 2 yıl dışarıda tutularak geri test yapılır. Hata dağılımından %10-%90 aralığı, "mevsim
   normali" tahminine göre beceri (skill = 1 - MAE_model / MAE_normal) ve gerçek kapsama ölçülür.
   2. haftadan sonra beceri hızla düşer; o zaman tahmin kendiliğinden mevsim normaline yaklaşır.

Bu bir sayısal hava tahmini (NWP) değildir; mevsim normali + kalıcılık modelidir. Eminlik ölçülen beceriden gelir.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

POWER_PARAMS = ["T2M", "T2M_MAX", "T2M_MIN", "PRECTOTCORR"]
# ad -> (etiket, birim, haftalık toplama: mean | sum)
VARS = {
    "t2m": ("ortalama sıcaklık", "°C", "mean"),
    "tmax": ("en yüksek sıcaklık (günlük ort.)", "°C", "mean"),
    "tmin": ("en düşük sıcaklık (günlük ort.)", "°C", "mean"),
    "precip": ("yağış", "mm", "sum"),
    "gdd": ("büyüme derece günü", "°C·gün", "sum"),
    "frost_days": ("donlu gün (Tmin < 0 °C)", "gün", "sum"),
    "heat_days": ("sıcak gün (Tmax > 35 °C)", "gün", "sum"),
}
HORIZONS = 4
SMOOTH_DAYS = 7
TEST_DAYS = 730
MIN_YEARS = 5


def daily_from_power(df: pd.DataFrame) -> pd.DataFrame:
    """`download.fetch_power` çıktısı -> tarih indeksli günlük tablo (t2m, tmax, tmin, precip + türetilmişler)."""
    d = df.set_index(pd.to_datetime(df["time"]).dt.normalize())
    out = pd.DataFrame(index=d.index)
    for src, dst in (("T2M", "t2m"), ("T2M_MAX", "tmax"), ("T2M_MIN", "tmin"), ("PRECTOTCORR", "precip")):
        v = pd.to_numeric(d[src], errors="coerce") if src in d else pd.Series(np.nan, index=d.index)
        out[dst] = v.where(v > -900)  # POWER eksik değeri -999
    out = out[~out.index.duplicated()].sort_index()
    return derive(out)


def derive(d: pd.DataFrame) -> pd.DataFrame:
    d = d.copy()
    tx, tn = d["tmax"].clip(upper=30), d["tmin"].clip(lower=10)
    d["gdd"] = ((tx + tn) / 2 - 10).clip(lower=0).where(d["tmax"].notna() & d["tmin"].notna())  # 10/30 °C
    d["frost_days"] = (d["tmin"] < 0).astype(float).where(d["tmin"].notna())
    d["heat_days"] = (d["tmax"] > 35).astype(float).where(d["tmax"].notna())
    return d


def _doy(idx: pd.DatetimeIndex) -> np.ndarray:
    return np.minimum(idx.day_of_year.to_numpy(), 365)  # 366 -> 365 (artık yıl)


def climatology(daily: pd.DataFrame) -> pd.DataFrame:
    """Yılın günü (1..365) x değişken: geçmiş yılların ortalaması, ±SMOOTH_DAYS döngüsel yumuşatma."""
    g = daily.groupby(_doy(daily.index)).mean(numeric_only=True).reindex(range(1, 366))
    g = g.interpolate(limit_direction="both")
    w = 2 * SMOOTH_DAYS + 1
    ext = pd.concat([g.iloc[-SMOOTH_DAYS:], g, g.iloc[:SMOOTH_DAYS]])
    sm = ext.rolling(w, center=True, min_periods=1).mean().iloc[SMOOTH_DAYS:-SMOOTH_DAYS]
    sm.index = range(1, 366)
    return sm


def _window(series: np.ndarray, k: int, how: str) -> np.ndarray:
    """t için [t+7(k-1)+1, t+7k] penceresinin haftalık değeri (sum ise eksik günlere dayanıklı: ortalama x 7)."""
    n = len(series)
    out = np.full(n, np.nan)
    s = pd.Series(series)
    m = s.rolling(7, min_periods=5).mean().to_numpy()  # i'de biten 7 günlük ortalama
    end = np.arange(n) + 7 * k
    ok = end < n
    out[ok] = m[end[ok]]
    return out * 7 if how == "sum" else out


def _fit(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    ok = np.isfinite(y) & np.isfinite(x).all(axis=1)
    if ok.sum() < 200:
        return np.zeros(x.shape[1] + 1)
    a = np.column_stack([np.ones(ok.sum()), x[ok]])
    coef, *_ = np.linalg.lstsq(a, y[ok], rcond=None)
    return coef


def _predict(coef: np.ndarray, x: np.ndarray) -> np.ndarray:
    return coef[0] + x @ coef[1:]


def _clip(var: str, v):
    if var in ("frost_days", "heat_days"):
        return np.clip(v, 0, 7)
    if var in ("precip", "gdd"):
        return np.maximum(v, 0)
    return v


def outlook(daily: pd.DataFrame, horizons: int = HORIZONS) -> dict | None:
    """Son gözlem gününden sonraki `horizons` hafta için tahmin + geri test becerisi. Yetersiz geçmişte None."""
    daily = daily.asfreq("D")
    years = daily.index.year.nunique()
    if years < MIN_YEARS or daily["t2m"].notna().sum() < 365 * MIN_YEARS:
        return None
    clim = climatology(daily)
    doy = _doy(daily.index)
    last = daily["t2m"].last_valid_index()
    t0 = daily.index.get_loc(last)
    test_start = len(daily) - TEST_DAYS
    weeks = []
    skill: dict[str, list[float | None]] = {}
    coverage: dict[str, list[float | None]] = {}
    for var, (_lab, _unit, how) in VARS.items():
        x = daily[var].to_numpy(dtype=float)
        c = clim[var].to_numpy()[doy - 1]
        anom = pd.Series(x - c)
        feats = np.column_stack([anom.rolling(7, min_periods=5).mean(), anom.rolling(30, min_periods=20).mean()])
        skill[var], coverage[var] = [], []
        for k in range(1, horizons + 1):
            target = _window(x, k, how)
            normal = _window(c, k, how)
            y = target - normal
            train = np.arange(len(x)) < test_start - 7 * k  # hedef penceresi teste taşmasın
            test = (np.arange(len(x)) >= test_start) & np.isfinite(y)
            coef = _fit(feats[train], y[train])
            res = y[test] - _predict(coef, feats[test])
            ok = np.isfinite(res)
            if ok.sum() >= 60:
                q10, q90 = np.quantile(res[ok], [0.1, 0.9])
                mae_m, mae_c = float(np.mean(np.abs(res[ok]))), float(np.mean(np.abs(y[test][ok])))
                sk = float(np.clip(1 - mae_m / mae_c, 0, 1)) if mae_c > 1e-9 else 0.0
                # dürüst kapsama: aralığı test döneminin ilk yarısında ayarla, ikinci yarısında ölç
                r = res[ok]
                half = len(r) // 2
                c10, c90 = np.quantile(r[:half], [0.1, 0.9])
                cov = float(np.mean((r[half:] >= c10) & (r[half:] <= c90)))
            else:
                q10 = q90 = np.nan
                sk, cov = 0.0, None
            skill[var].append(round(sk, 3))
            coverage[var].append(None if cov is None else round(cov, 3))
            # nihai tahmin: tüm geçmişle yeniden öğren, aralık geri testteki hata dağılımından
            coef_all = _fit(feats, y)
            f0 = feats[t0]
            delta = float(_predict(coef_all, f0[None, :])[0]) if np.isfinite(f0).all() else 0.0
            start = last + pd.Timedelta(days=7 * (k - 1) + 1)
            days = pd.date_range(start, periods=7, freq="D")
            nd = clim[var].to_numpy()[_doy(days) - 1]
            norm = float(nd.sum() if how == "sum" else nd.mean())
            p50 = norm + delta
            lo = p50 + (q10 if np.isfinite(q10) else 0.0)
            hi = p50 + (q90 if np.isfinite(q90) else 0.0)
            if len(weeks) < k:
                weeks.append({"week": k, "start": start.date().isoformat(), "end": days[-1].date().isoformat(),
                              "vars": {}})
            weeks[k - 1]["vars"][var] = {
                "p10": round(float(_clip(var, lo)), 2), "p50": round(float(_clip(var, p50)), 2),
                "p90": round(float(_clip(var, hi)), 2), "normal": round(float(_clip(var, norm)), 2),
            }
    return {
        "issued_from": last.date().isoformat(),
        "history_years": int(years),
        "history_start": daily.index.min().date().isoformat(),
        "weeks": weeks,
        "skill": skill,
        "interval_coverage": coverage,
        "variables": {k: {"label": v[0], "unit": v[1], "aggregation": v[2]} for k, v in VARS.items()},
        "method": ("mevsim normali (bu noktanın {} yıllık NASA POWER geçmişi) + son 7/30 günün anomalisinden "
                   "öğrenilmiş doğrusal kalıcılık; aralık ve beceri son 2 yılın geri testinden").format(int(years)),
    }


def two_week(o: dict, var: str) -> dict:
    """1. ve 2. haftanın birleşik değeri (ortalama değişkenlerde ortalama, toplamlarda toplam)."""
    how = VARS[var][2]
    w = [o["weeks"][i]["vars"][var] for i in range(min(2, len(o["weeks"])))]
    agg = (lambda xs: float(np.sum(xs))) if how == "sum" else (lambda xs: float(np.mean(xs)))
    return {k: round(agg([x[k] for x in w]), 2) for k in ("p10", "p50", "p90", "normal")}


def confidence(o: dict, var: str) -> float:
    """1-2. hafta becerisinden eminlik (0..1). Mevsim normalini hiç geçemiyorsa ~0.45 (düşük)."""
    sk = [s for s in o["skill"].get(var, [])[:2] if s is not None]
    s = float(np.mean(sk)) if sk else 0.0
    years_term = min(1.0, o["history_years"] / 15)
    return round(float(np.clip(0.45 + 0.5 * s + 0.05 * years_term, 0.3, 0.9)), 3)


def fetch_daily(lat: float, lon: float, today: dt.date, years: int = 15, timeout: float = 30,
                retries: int = 2) -> pd.DataFrame:
    from ml.data_load import download  # noqa: PLC0415

    start = dt.date(today.year - years, 1, 1)
    df = download.fetch_power("outlook", lat, lon, start, today - dt.timedelta(days=1), params=POWER_PARAMS,
                              timeout=timeout, retries=retries)
    return daily_from_power(df)
