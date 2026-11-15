"""SSURGO (yalnızca ABD) yoksa toprak ve eğimi küresel kaynaklardan HESAPLAYARAK tahmin eder.

- Toprak: ISRIC SoilGrids 250 m (kil, kum, organik karbon, pH; 0-30 cm ağırlıklı). REST servisi sık kapalı olduğu
  için doğrudan bulut-optimize GeoTIFF'ten (files.isric.org) yalnızca noktanın bloğu okunur.
- Su tutma (AWC), doygun hidrolik iletkenlik (Ksat) ve hidrolojik grup: Saxton & Rawls (2006) pedotransfer
  denklemleriyle kil/kum/organik maddeden hesaplanır.
- Eğim: Copernicus GLO-90 yükseklik modelinden (Open-Meteo elevation API, anahtarsız) tarla merkezi çevresinde
  3x3 nokta, Horn yöntemi.

Çıktı `fields.soil_at` ile aynı anahtarları kullanır (model özellikleri aynı yoldan üretilir) ve
`estimated=True` taşır. Bunlar ölçüm değil TAHMİNDİR: arayüz ve bot bunu belirtir, güven bonusu yarımdır.
"""
from __future__ import annotations

import json
import logging
import math
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

log = logging.getLogger("ml.data_load.soil_global")

SOILGRIDS_VRT = "/vsicurl/https://files.isric.org/soilgrids/latest/data/{p}/{p}_{d}_mean.vrt"
DEPTHS = {"0-5cm": 5, "5-15cm": 10, "15-30cm": 15}  # ağırlık = katman kalınlığı
# özellik -> değer bölücü (SoilGrids tamsayı ölçekli): kil/kum g/kg -> %, soc dg/kg -> g/kg, pH x10
PROPS = {"clay": 10.0, "sand": 10.0, "soc": 10.0, "phh2o": 10.0}
ELEV_URL = "https://api.open-meteo.com/v1/elevation"
DEM_STEP_M = 90.0
USER_AGENT = "field-shift-research/0.1"


# ---------------------------------------------------------------- pedotransfer (Saxton & Rawls 2006)
def saxton_rawls(sand_pct: float, clay_pct: float, om_pct: float) -> dict:
    """Kum/kil (%) ve organik madde (%) -> θ1500, θ33, θS (hacimsel), AWC (cm/cm), Ksat (µm/s)."""
    s, c = sand_pct / 100, clay_pct / 100
    om = min(max(om_pct, 0.0), 8.0)  # denklemler OM <= %8 için geçerli
    t1500t = -0.024 * s + 0.487 * c + 0.006 * om + 0.005 * s * om - 0.013 * c * om + 0.068 * s * c + 0.031
    t1500 = t1500t + (0.14 * t1500t - 0.02)
    t33t = -0.251 * s + 0.195 * c + 0.011 * om + 0.006 * s * om - 0.027 * c * om + 0.452 * s * c + 0.299
    t33 = t33t + (1.283 * t33t ** 2 - 0.374 * t33t - 0.015)
    ts33t = 0.278 * s + 0.034 * c + 0.022 * om - 0.018 * s * om - 0.027 * c * om - 0.584 * s * c + 0.078
    ts33 = ts33t + (0.636 * ts33t - 0.107)
    ts = t33 + ts33 - 0.097 * s + 0.043
    t1500, t33 = max(t1500, 0.005), max(t33, t1500 + 0.005)
    b = (math.log(1500) - math.log(33)) / (math.log(t33) - math.log(t1500))
    lam = 1 / b
    ks_mm_h = 1930 * max(ts - t33, 1e-6) ** (3 - lam)
    return {"theta1500": t1500, "theta33": t33, "theta_s": ts, "awc_cm_cm": t33 - t1500,
            "ksat_um_s": ks_mm_h / 3.6}


def hydgrp_from_ksat(ksat_um_s: float) -> str:
    """NRCS NEH 630 Bölüm 7 (kısıtlayıcı katman > 100 cm varsayımı)."""
    if ksat_um_s > 40:
        return "A"
    if ksat_um_s > 10:
        return "B"
    if ksat_um_s > 1:
        return "C"
    return "D"


def slope_horn(z: list[list[float]], step_m: float) -> float:
    """3x3 yükseklik ızgarası (kuzeyden güneye satırlar) -> eğim (%)."""
    (a, b, c), (d, _e, f), (g, h, i) = z
    dzdx = ((c + 2 * f + i) - (a + 2 * d + g)) / (8 * step_m)
    dzdy = ((g + 2 * h + i) - (a + 2 * b + c)) / (8 * step_m)
    return 100 * math.hypot(dzdx, dzdy)


# ---------------------------------------------------------------- veri kaynakları
def soilgrids_point(lat: float, lon: float, workers: int = 12, timeout: int = 30) -> dict | None:
    """Noktadaki 0-30 cm ağırlıklı kil, kum (%), organik karbon (g/kg), pH. Veri yoksa (su, şehir) None."""
    import rasterio  # noqa: PLC0415
    from rasterio.warp import transform  # noqa: PLC0415

    def one(job):
        p, d = job
        with rasterio.Env(GDAL_HTTP_TIMEOUT=timeout, GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
                          GDAL_HTTP_MAX_RETRY=2, GDAL_HTTP_RETRY_DELAY=1):
            with rasterio.open(SOILGRIDS_VRT.format(p=p, d=d)) as ds:
                x, y = transform("EPSG:4326", ds.crs, [lon], [lat])
                v = float(next(ds.sample([(x[0], y[0])]))[0])
                nodata = ds.nodata
        return p, d, None if v <= -32768 or (nodata is not None and v == nodata) or v < 0 else v

    jobs = [(p, d) for p in PROPS for d in DEPTHS]
    with ThreadPoolExecutor(min(workers, len(jobs))) as ex:
        res = list(ex.map(one, jobs))
    out = {}
    for p, div in PROPS.items():
        vals = [(v / div, DEPTHS[d]) for q, d, v in res if q == p and v is not None]
        if not vals:
            return None
        out[p] = sum(v * w for v, w in vals) / sum(w for _, w in vals)
    if out["clay"] + out["sand"] <= 0:
        return None
    return out


def dem_slope_pct(lat: float, lon: float, step_m: float = DEM_STEP_M, timeout: int = 20) -> float | None:
    dlat = step_m / 111_320
    dlon = step_m / (111_320 * max(0.1, math.cos(math.radians(lat))))
    pts = [(lat + r * dlat, lon + c * dlon) for r in (1, 0, -1) for c in (-1, 0, 1)]  # kuzeyden güneye
    q = urllib.parse.urlencode({"latitude": ",".join(f"{p[0]:.6f}" for p in pts),
                                "longitude": ",".join(f"{p[1]:.6f}" for p in pts)})
    req = urllib.request.Request(f"{ELEV_URL}?{q}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        z = json.load(r).get("elevation") or []
    if len(z) != 9 or any(v is None for v in z):
        return None
    return round(slope_horn([z[0:3], z[3:6], z[6:9]], step_m), 2)


# ---------------------------------------------------------------- birleşik tahmin
def estimate(lat: float, lon: float, cache_dir: str | Path | None = None, need_soil: bool = True) -> dict:
    """{"soil": dict|None, "errors": [...]} — `fields.soil_at` biçiminde, `estimated=True`.
    Sonuç kalıcı önbelleğe yazılır (toprak ve eğim değişmez)."""
    from ml.data_load.fields import texture_class  # noqa: PLC0415

    path = None
    if cache_dir:
        path = Path(cache_dir) / f"{lat:.3f}_{lon:.3f}{'' if need_soil else '_slope'}.json"
        if path.exists():
            return json.loads(path.read_text())
    errors: list[str] = []
    soil: dict = {}
    if need_soil:
        try:
            sg = soilgrids_point(lat, lon)
        except Exception as e:  # noqa: BLE001
            sg = None
            errors.append(f"SoilGrids toprak tahmini alınamadı: {str(e)[:100]}")
        if sg:
            om = sg["soc"] * 1.724 / 10  # organik karbon g/kg -> organik madde %
            silt = max(0.0, 100 - sg["clay"] - sg["sand"])
            pt = saxton_rawls(sg["sand"], sg["clay"], om)
            tex = texture_class(sg["sand"], sg["clay"], silt)
            soil = {
                "om_pct": round(om, 2), "clay_pct": round(sg["clay"], 1), "sand_pct": round(sg["sand"], 1),
                "silt_pct": round(silt, 1), "ph": round(sg["phh2o"], 2),
                "awc_cm_cm": round(pt["awc_cm_cm"], 3), "ksat_um_s": round(pt["ksat_um_s"], 2),
                "hydgrp": hydgrp_from_ksat(pt["ksat_um_s"]), "drainagecl": None,
                "texture": tex.replace("_", " ") if tex else None, "soil_texture": tex,
            }
    try:
        slope = dem_slope_pct(lat, lon)
    except Exception as e:  # noqa: BLE001
        slope = None
        errors.append(f"yükseklik modelinden eğim hesaplanamadı: {str(e)[:100]}")
    if slope is not None:
        soil["slope_pct"] = slope
    if not soil:
        out = {"soil": None, "errors": errors}
    else:
        srcs = (["SoilGrids 250 m + Saxton-Rawls"] if "om_pct" in soil else []) + \
               (["Copernicus GLO-90 eğim"] if "slope_pct" in soil else [])
        soil.update({"estimated": True, "source": " + ".join(srcs),
                     "estimated_keys": [k for k in ("om_pct", "clay_pct", "sand_pct", "awc_cm_cm", "ph",
                                                    "ksat_um_s", "slope_pct", "hydgrp", "soil_texture")
                                        if soil.get(k) is not None]})
        out = {"soil": soil, "errors": errors}
    if path and out["soil"] is not None and not errors:  # kısmi sonuç önbelleğe yazılmaz (tekrar denensin)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out))
    return out
