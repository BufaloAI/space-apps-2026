"""SENTETİK örnek veri: `data-load/nasa_pipeline.py` çıktısının şeklini taklit eder.

Gerçek veri başka makinede olduğu için temizleyiciyi bununla deneriz. Değerler UYDURMADIR,
yalnızca test ve duman (smoke) çalıştırması içindir. Dosya adı `SYNTHETIC_` ile başlar ve
her satırda `synthetic=1` sütunu vardır (temizleyici bunu görür, rapora yazar).

`nasa_pipeline.process_data()` çıktısının özellikleri (kodu okuyarak çıkarıldı):
- Tüm ürünler alt alta eklenir (`pd.concat`); bir ürünün satırında diğerlerinin sütunları NaN.
- `ds.to_dataframe().reset_index()`: boyutlar sütun olur (time, lon, lat, nv). GPM'de `time_bnds`
  değişkeni `nv` boyutunu getirdiği için her satır 2 kez tekrar eder.
- Sonuna `zaman_formatli` (YYYY.MM.DD.HH), `yil`, `ay`, `gun`, `saat` eklenir.
- Ölçek ve dolgu değerleri uygulanmaz (ham değerler: NDVI 0..10000, -3000, -9999.9 ...).
- Not: pipeline'ın kendisi .hdf (MODIS) dosyalarını atlıyor, SMAP'ı da (lat/lon sütunu yok) atlıyor;
  burada pipeline düzeltilmiş gibi bu ürünlerin satırlarını da üretiyoruz ki temizleyici hepsini görsün.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

# (area_id, merkez lat, merkez lon, ürün tipi) — sentetik
SAMPLE_AREAS = [
    ("ia_story", 42.03, -93.60, "corn_soy"),
    ("il_champaign", 40.06, -88.30, "corn_soy"),
    ("ne_york", 40.85, -97.60, "irrigated_corn"),
    ("ks_reno", 37.95, -98.10, "wheat"),
    ("ca_fresno", 36.60, -119.90, "orchard"),
    ("ms_delta", 33.45, -90.70, "cotton_soy"),
]

_PHENO = {  # (taban NDVI, genlik, tepe günü, genişlik gün)
    "corn_soy": (0.22, 0.62, 205, 38),
    "irrigated_corn": (0.22, 0.66, 200, 40),
    "wheat": (0.25, 0.50, 120, 35),
    "orchard": (0.38, 0.30, 170, 70),
    "cotton_soy": (0.25, 0.55, 215, 40),
}


def _daily_weather(rng, lat, kind, days):
    doy = days.day_of_year.to_numpy()
    season = np.cos(2 * np.pi * (doy - 200) / 365.25)
    tmean = 12 + (16 - 0.25 * (lat - 35)) * season + rng.normal(0, 3, len(days))
    tmax = tmean + 6 + rng.normal(0, 1.5, len(days))
    tmin = tmean - 6 + rng.normal(0, 1.5, len(days))
    wet_prob = 0.30 + 0.12 * np.sin(2 * np.pi * (doy - 80) / 365.25)
    if kind == "orchard":
        wet_prob = 0.12 + 0.18 * np.cos(2 * np.pi * (doy - 15) / 365.25)
    # 2025 yazına sentetik bir kuraklık koy (modelin öğrenebileceği bir sinyal olsun)
    drought = (days.year == 2025) & (doy > 150) & (doy < 230)
    wet_prob = np.where(drought, wet_prob * 0.35, wet_prob)
    precip = np.where(rng.random(len(days)) < wet_prob, rng.gamma(0.9, 9.0, len(days)), 0.0)
    sm = np.empty(len(days))
    s = 0.28
    for i, p in enumerate(precip):  # basit kova modeli
        et = max(0.0, 0.004 * (tmean[i] - 2))
        s = np.clip(s + p * 0.006 - et - 0.002 * (s - 0.08), 0.04, 0.50)
        if kind == "irrigated_corn" and 140 < doy[i] < 250 and s < 0.22:
            s = 0.26  # sulama
        sm[i] = s
    return tmean, tmax, tmin, precip, sm


def _ndvi(days, kind, sm, rng):
    base, amp, peak, width = _PHENO[kind]
    doy = days.day_of_year.to_numpy()
    curve = base + amp * np.exp(-0.5 * ((doy - peak) / width) ** 2)
    sm_lag = pd.Series(sm).rolling(21, min_periods=1).mean().to_numpy()
    stress = np.clip((sm_lag - 0.12) / 0.15, 0.25, 1.0)
    return np.clip(base + (curve - base) * stress + rng.normal(0, 0.02, len(days)), -0.1, 0.95)


def make_sample(start="2024-01-01", end="2025-12-31", seed=7, areas=SAMPLE_AREAS) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    days = pd.date_range(start, end, freq="D")
    chunks = []
    for area_id, clat, clon, kind in areas:
        tmean, tmax, tmin, precip, sm = _daily_weather(rng, clat, kind, days)
        ndvi = _ndvi(days, kind, sm, rng)

        # GPM IMERG günlük: 0.1° ızgara, 2x2 piksel, nv boyutu yüzünden 2'şer satır
        glat = np.round(np.floor(clat * 10) / 10 + np.array([0.05, 0.15]), 2)
        glon = np.round(np.floor(clon * 10) / 10 + np.array([0.05, 0.15]), 2)
        rows = []
        for la in glat:
            for lo in glon:
                p = np.maximum(0, precip * rng.normal(1, 0.15, len(days)))
                p = np.where(rng.random(len(days)) < 0.01, -9999.9, p)  # ara sıra dolgu
                for nv in (0, 1):
                    rows.append(pd.DataFrame({
                        "time": days, "lon": lo, "lat": la, "nv": nv,
                        "precipitation": p.astype("float32"),
                        "precipitation_cnt": 48, "precipitation_cnt_cond": rng.integers(0, 48, len(days)),
                        "MWprecipitation": (p * 0.9).astype("float32"), "MWprecipitation_cnt": 12,
                        "MWprecipitation_cnt_cond": 3, "randomError": (p * 0.3).astype("float32"),
                        "randomError_cnt": 48, "probabilityLiquidPrecipitation": 100,
                        "time_bnds": days + pd.Timedelta(days=nv),
                    }))
        chunks.append(pd.concat(rows))

        # SMAP L4: günde 4 örnek (01:30, 07:30, 13:30, 19:30), 2 piksel
        t3 = pd.DatetimeIndex(np.concatenate([days + pd.Timedelta(hours=h, minutes=30) for h in (1, 7, 13, 19)])).sort_values()
        idx = (t3.normalize() - days[0]).days.to_numpy()
        for k in range(2):
            v = sm[idx] + rng.normal(0, 0.01, len(t3))
            v = np.where(rng.random(len(t3)) < 0.02, -9999.0, v)
            chunks.append(pd.DataFrame({
                "time": t3, "lat": clat + 0.04 * k, "lon": clon - 0.04 * k,
                "sm_surface": v.astype("float32"),
                "sm_rootzone": (sm[idx] * 0.9 + 0.03).astype("float32"),
                "surface_temp": (tmean[idx] + 273.15 + rng.normal(0, 2, len(t3))).astype("float32"),
            }))

        # MOD13Q1: 16 günlük kompozit, 3x3 piksel, HDF4 adlarıyla (boşluklu), ham tamsayı
        comp = days[(days.day_of_year - 1) % 16 == 0]
        cidx = (comp - days[0]).days.to_numpy()
        for i in range(3):
            for j in range(3):
                rel = rng.choice([0, 0, 0, 0, 1, 1, 3, 2], size=len(comp))
                nd = np.round(ndvi[cidx] * 10000 + rng.normal(0, 150, len(comp)))
                nd = np.where(rel == 3, nd * 0.4, nd)  # bulutlu piksel düşük görünür
                nd = np.where(rng.random(len(comp)) < 0.03, -3000, nd)
                chunks.append(pd.DataFrame({
                    "time": comp, "lat": clat + 0.002 * i, "lon": clon + 0.002 * j,
                    "250m 16 days NDVI": nd.astype("int16"),
                    "250m 16 days EVI": (nd * 0.62).astype("int16"),
                    "250m 16 days pixel reliability": rel.astype("int8"),
                }))

        # MOD11A1 LST: günlük, ölçek 0.02 K, dolgu 0, QC_Day
        qc = rng.choice([0, 0, 0, 1, 2, 3], size=len(days))
        lst = np.round((tmax + 3 + 273.15) / 0.02)
        lst = np.where(qc >= 2, 0, lst)
        chunks.append(pd.DataFrame({
            "time": days, "lat": clat, "lon": clon, "LST_Day_1km": lst.astype("uint16"), "QC_Day": qc.astype("uint8"),
        }))

        # POWER tarzı günlük sıcaklıklar (pipeline'da yok ama gerçek indirici POWER da çekiyor)
        chunks.append(pd.DataFrame({
            "time": days, "lat": clat, "lon": clon,
            "T2M": tmean.round(2), "T2M_MAX": tmax.round(2), "T2M_MIN": tmin.round(2),
        }))

    df = pd.concat(chunks, ignore_index=True)
    df["time"] = pd.to_datetime(df["time"])
    df["zaman_formatli"] = df["time"].dt.strftime("%Y.%m.%d.%H")
    df["yil"] = df["time"].dt.year
    df["ay"] = df["time"].dt.month
    df["gun"] = df["time"].dt.day
    df["saat"] = df["time"].dt.hour
    df["synthetic"] = 1
    return df


def main(argv=None):
    ap = argparse.ArgumentParser(description="SENTETİK nasa_pipeline çıktısı üretir (yalnızca test için).")
    ap.add_argument("--out", default="ml/cache/sample/SYNTHETIC_field_shift_optimized_features.csv")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args(argv)
    df = make_sample(a.start, a.end, a.seed)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    areas = [{"area_id": a, "lat": la, "lon": lo, "radius_deg": 0.2, "crop_hint": k} for a, la, lo, k in SAMPLE_AREAS]
    (out.parent / "SYNTHETIC_areas.json").write_text(json.dumps(areas, indent=1))
    print(f"SENTETİK örnek yazıldı: {out} ({len(df):,} satır, {df.shape[1]} sütun) + SYNTHETIC_areas.json")


if __name__ == "__main__":
    main()
