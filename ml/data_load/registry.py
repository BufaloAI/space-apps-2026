"""Bilinen NASA değişkenlerinin kaydı: ad eşleme, ölçek, dolgu (fill) değerleri, QA ve haftalık toplama.

Ham veri, ürünün kendi değişken adlarıyla gelir (`250m_16_days_NDVI`, `PRECTOTCORR`, `sm_surface`...).
Burada her biri tek bir kanonik ada (`ndvi`, `precip_mm`, `sm_surface`...) çevrilir.
Kayıtta olmayan sayısal sütunlar da korunur; adları normalize edilir ve genel dolgu kuralı uygulanır.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class VarSpec:
    canonical: str
    scale: float = 1.0
    offset: float = 0.0  # ölçekten SONRA eklenir (örn. Kelvin -> Celsius: -273.15)
    fills: tuple[float, ...] = ()
    valid_raw: tuple[float | None, float | None] = (None, None)  # ham (ölçeksiz) geçerli aralık
    valid: tuple[float | None, float | None] = (None, None)  # ölçeklenmiş geçerli aralık
    agg: str = "mean"  # haftalık: "mean" | "sum" (sum = günlük ortalama * 7)
    qa: str | None = None  # QA sütununun kanonik adı
    unit: str = ""


# Ürünlerin bilinen dolgu değerleri (genel kural için de kullanılır).
GENERIC_FILLS = (-999.0, -999.9, -9999.0, -9999.9, -99999.0, -3000.0, -28672.0, -32768.0, 32767.0, 65535.0)

_SPECS: dict[str, VarSpec] = {
    # --- NASA POWER (günlük, nokta). Dolgu -999.
    "t2m": VarSpec("t2m_c", fills=(-999.0,), valid=(-60, 60), unit="°C"),
    "t2m_max": VarSpec("tmax_c", fills=(-999.0,), valid=(-60, 65), unit="°C"),
    "t2m_min": VarSpec("tmin_c", fills=(-999.0,), valid=(-70, 50), unit="°C"),
    "prectotcorr": VarSpec("precip_mm", fills=(-999.0,), valid=(0, 1000), agg="sum", unit="mm"),
    "rh2m": VarSpec("rh_pct", fills=(-999.0,), valid=(0, 100), unit="%"),
    "allsky_sfc_sw_dwn": VarSpec("srad_mj", fills=(-999.0,), valid=(0, 50), unit="MJ/m²/gün"),
    "ws2m": VarSpec("wind_ms", fills=(-999.0,), valid=(0, 60), unit="m/s"),
    "gwettop": VarSpec("gwet_top", fills=(-999.0,), valid=(0, 1), unit="0-1"),
    "gwetroot": VarSpec("gwet_root", fills=(-999.0,), valid=(0, 1), unit="0-1"),
    "evptrns": VarSpec("et_mm", fills=(-999.0,), valid=(0, 50), agg="sum", unit="mm"),
    # --- MOD13Q1 (16 günlük, 250 m). NDVI/EVI ölçek 0.0001, dolgu -3000.
    "250m_16_days_ndvi": VarSpec("ndvi", scale=1e-4, fills=(-3000.0,), valid_raw=(-2000, 10000),
                                 qa="ndvi_reliability", unit="NDVI"),
    "250m_16_days_evi": VarSpec("evi", scale=1e-4, fills=(-3000.0,), valid_raw=(-2000, 10000),
                                qa="ndvi_reliability", unit="EVI"),
    # pixel_reliability: -1 dolgu, 0 iyi, 1 kabul edilebilir, 2 kar/buz, 3 bulutlu
    "250m_16_days_pixel_reliability": VarSpec("ndvi_reliability", fills=(-1.0,), valid_raw=(0, 3)),
    # --- MOD11A1 (günlük, 1 km). LST ölçek 0.02 K, dolgu 0.
    "lst_day_1km": VarSpec("lst_day_c", scale=0.02, offset=-273.15, fills=(0.0,), valid_raw=(7500, 65535),
                           qa="lst_qc_day", unit="°C"),
    "lst_night_1km": VarSpec("lst_night_c", scale=0.02, offset=-273.15, fills=(0.0,), valid_raw=(7500, 65535),
                             qa="lst_qc_night", unit="°C"),
    "qc_day": VarSpec("lst_qc_day"),
    "qc_night": VarSpec("lst_qc_night"),
    # --- SMAP L4 SPL4SMGP (3 saatlik, 9 km). Dolgu -9999.
    "sm_surface": VarSpec("sm_surface", fills=(-9999.0,), valid=(0, 1), unit="m³/m³"),
    "sm_rootzone": VarSpec("sm_rootzone", fills=(-9999.0,), valid=(0, 1), unit="m³/m³"),
    "sm_profile": VarSpec("sm_profile", fills=(-9999.0,), valid=(0, 1), unit="m³/m³"),
    "surface_temp": VarSpec("smap_tsurf_c", offset=-273.15, fills=(-9999.0,), valid=(-80, 80), unit="°C"),
    # --- GPM IMERG günlük (GPM_3IMERGDF). mm/gün, dolgu -9999.9.
    "precipitation": VarSpec("precip_mm", fills=(-9999.9,), valid=(0, 2000), agg="sum", unit="mm"),
}

# Takma adlar (HDF4/xarray okumasında boşluklu adlar, eski sürümler vb.)
_ALIASES = {
    "250m_16_days_ndvi": ["250m 16 days ndvi", "ndvi"],
    "250m_16_days_evi": ["250m 16 days evi", "evi"],
    "250m_16_days_pixel_reliability": ["250m 16 days pixel reliability", "pixel_reliability"],
    "precipitation": ["precipitationcal", "precipitation_cal"],
}

# Özellik olmayan, atılacak sütunlar (düzenli ifade, normalize edilmiş ada uygulanır).
META_PATTERNS = [
    r"^nv$", r"^bnds?$", r".*_bnds?$", r".*_cnt(_cond)?$", r"^zaman_formatli$", r"^yil$", r"^ay$", r"^gun$",
    r"^saat$", r"^index$", r"^unnamed.*", r"^x$", r"^y$", r"^crs$", r"^spatial_ref$", r"^band$",
    r"^randomerror.*", r"^probabilityliquidprecipitation$", r"^mwprecipitation.*", r"^irprecipitation.*",
    r"^synthetic$", r"^source$", r"^pixel$", r"^tile$", r"^cell_(row|column)$", r"^modis_date$",
    r"^calendar_date$", r"^processed?_date$",
]
_META_RE = [re.compile(p) for p in META_PATTERNS]


def normalize_name(name: str) -> str:
    """'250m 16 days NDVI' -> '250m_16_days_ndvi'."""
    s = re.sub(r"[^0-9a-zA-Z]+", "_", str(name).strip().lower())
    return s.strip("_")


_LOOKUP: dict[str, str] = {}
for _k in _SPECS:
    _LOOKUP[_k] = _k
for _k, _al in _ALIASES.items():
    for _a in _al:
        _LOOKUP[normalize_name(_a)] = _k


def is_meta(name: str) -> bool:
    n = normalize_name(name)
    return any(r.match(n) for r in _META_RE)


@dataclass(frozen=True)
class Resolved:
    raw_key: str | None  # kayıttaki anahtar (yoksa None)
    spec: VarSpec = field(default_factory=lambda: VarSpec("unknown"))


def resolve(name: str) -> Resolved:
    """Ham sütun adını kayıtla eşler. Bilinmiyorsa normalize edilmiş adla genel bir spec döner."""
    n = normalize_name(name)
    key = _LOOKUP.get(n)
    if key is not None:
        return Resolved(key, _SPECS[key])
    return Resolved(None, VarSpec(n, fills=GENERIC_FILLS))


def spec_for_canonical(canonical: str) -> VarSpec | None:
    for s in _SPECS.values():
        if s.canonical == canonical:
            return s
    return None


# QA maskeleri: kanonik QA değişkeni -> "kötü" kabul edilen değer fonksiyonu (ham QA değerine uygulanır)
def qa_bad(qa_canonical: str, qa_value):
    import numpy as np

    v = np.asarray(qa_value, dtype="float64")
    if qa_canonical == "ndvi_reliability":
        # 2 = kar/buz, 3 = bulut; -1 = dolgu
        return (v >= 2) | (v < 0)
    if qa_canonical in ("lst_qc_day", "lst_qc_night"):
        # Bit 0-1: 0 iyi, 1 diğer kalite, 2-3 üretilmedi (bulut vb.)
        iv = np.where(np.isnan(v), 3, v).astype("int64")
        return (iv & 0b11) >= 2
    return np.zeros(v.shape, dtype=bool)
