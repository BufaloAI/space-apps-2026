"""Tarla geçmişi ve toprak: USDA CDL (ekim geçmişi) + SSURGO (toprak) + eğitim tarlası seçimi.

Genel servisler, kimlik bilgisi gerekmez. Yalnızca ABD kapsamı.
  - CDL: CropScape GetCDLValue (30 m, yıllık, 2008-2025), konum EPSG:5070 (Albers) istenir.
  - Toprak: Soil Data Access (SDA) Tabular POST servisi.
CLI:  uv run python -m ml.data_load.fields select|point ...
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import random
import re
import threading
import time as _time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Iterable, Sequence

import pandas as pd

log = logging.getLogger("ml.data_load.fields")

USER_AGENT = "field-shift-research/0.1"
TIMEOUT_S = 90
MAX_RETRIES = 4
MIN_INTERVAL_S = 0.4
CDL_URL = "https://nassgeodata.gmu.edu/axis2/services/CDLService/GetCDLValue"
SDA_URL = "https://sdmdataaccess.sc.egov.usda.gov/Tabular/post.rest"

CACHE_DIR = Path(__file__).resolve().parents[1] / "cache" / "static"
HTTP_CACHE_FILE = "fields_http_cache.json"

# ---------------------------------------------------------------- Albers (EPSG:5070)
# Kaynak: Snyder, "Map Projections - A Working Manual" (USGS PP 1395), Albers Equal Area Conic,
# küre-dışı (elipsoidal) formüller 14-1..14-12. EPSG:5070: NAD83 / Conus Albers.
_A = 6378137.0                      # GRS80 yarı büyük eksen
_F = 1 / 298.257222101              # GRS80 basıklık
_E2 = _F * (2 - _F)
_E = math.sqrt(_E2)


def _q(phi: float) -> float:
    s = math.sin(phi)
    return (1 - _E2) * (s / (1 - _E2 * s * s)
                        - (1 / (2 * _E)) * math.log((1 - _E * s) / (1 + _E * s)))


def _m(phi: float) -> float:
    s = math.sin(phi)
    return math.cos(phi) / math.sqrt(1 - _E2 * s * s)


_P1, _P2, _P0, _L0 = (math.radians(v) for v in (29.5, 45.5, 23.0, -96.0))
_N = (_m(_P1) ** 2 - _m(_P2) ** 2) / (_q(_P2) - _q(_P1))
_C = _m(_P1) ** 2 + _N * _q(_P1)
_RHO0 = _A * math.sqrt(_C - _N * _q(_P0)) / _N


def albers_5070(lat: float, lon: float) -> tuple[float, float]:
    """(lat, lon) derece -> EPSG:5070 (x, y) metre."""
    rho = _A * math.sqrt(max(_C - _N * _q(math.radians(lat)), 0.0)) / _N
    theta = _N * (math.radians(lon) - _L0)
    return rho * math.sin(theta), _RHO0 - rho * math.cos(theta)


# ---------------------------------------------------------------- CDL sınıf tablosu
# Kaynak: USDA NASS CDL kategori listesi (https://www.nass.usda.gov/Research_and_Science/Cropland/docs/).
# kod: (ad, crop_key, kategori). crop_key: ml/model/crops.toml anahtarı; modelde olmayan ekin -> None.
# Kategori: crop | fallow | developed | forest | water | grass | other
_CROP_TABLE: dict[int, tuple[str, str | None]] = {
    1: ("Corn", "corn"), 2: ("Cotton", "cotton"), 3: ("Rice", None), 4: ("Sorghum", "sorghum"),
    5: ("Soybeans", "soybean"), 6: ("Sunflower", "sunflower"), 10: ("Peanuts", None),
    11: ("Tobacco", None), 12: ("Sweet Corn", None), 13: ("Pop or Orn Corn", None), 14: ("Mint", None),
    21: ("Barley", None), 22: ("Durum Wheat", "winter_wheat"),   # en yakın buğday profili
    23: ("Spring Wheat", "winter_wheat"),                         # en yakın buğday profili
    24: ("Winter Wheat", "winter_wheat"), 25: ("Other Small Grains", None),
    26: ("Dbl Crop WinWht/Soybeans", "winter_wheat>soybean"), 27: ("Rye", "cereal_rye"), 28: ("Oats", None),
    29: ("Millet", None), 30: ("Speltz", None), 31: ("Canola", None), 32: ("Flaxseed", None),
    33: ("Safflower", None), 34: ("Rape Seed", None), 35: ("Mustard", None), 36: ("Alfalfa", "alfalfa"),
    37: ("Other Hay/Non Alfalfa", None), 38: ("Camelina", None), 39: ("Buckwheat", None),
    41: ("Sugarbeets", None), 42: ("Dry Beans", None), 43: ("Potatoes", None), 44: ("Other Crops", None),
    45: ("Sugarcane", None), 46: ("Sweet Potatoes", None), 47: ("Misc Vegs & Fruits", None),
    48: ("Watermelons", None), 49: ("Onions", None), 50: ("Cucumbers", None), 51: ("Chick Peas", "chickpea"),
    52: ("Lentils", None), 53: ("Peas", None), 54: ("Tomatoes", None), 55: ("Caneberries", None),
    56: ("Hops", None), 57: ("Herbs", None), 58: ("Clover/Wildflowers", None), 59: ("Sod/Grass Seed", None),
    60: ("Switchgrass", None), 66: ("Cherries", None), 67: ("Peaches", None), 68: ("Apples", None),
    69: ("Grapes", None), 70: ("Christmas Trees", None), 71: ("Other Tree Crops", None), 72: ("Citrus", None),
    74: ("Pecans", None), 75: ("Almonds", None), 76: ("Walnuts", None), 77: ("Pears", None),
    204: ("Pistachios", None), 205: ("Triticale", None), 206: ("Carrots", None), 207: ("Asparagus", None),
    208: ("Garlic", None), 209: ("Cantaloupes", None), 210: ("Prunes", None), 211: ("Olives", None),
    212: ("Oranges", None), 213: ("Honeydew Melons", None), 214: ("Broccoli", None), 216: ("Peppers", None),
    217: ("Pomegranates", None), 218: ("Nectarines", None), 219: ("Greens", None), 220: ("Plums", None),
    221: ("Strawberries", None), 222: ("Squash", None), 223: ("Apricots", None),
    224: ("Vetch", "hairy_vetch"),
    225: ("Dbl Crop WinWht/Corn", "winter_wheat>corn"), 226: ("Dbl Crop Oats/Corn", "corn"),
    227: ("Lettuce", None), 228: ("Dbl Crop Triticale/Corn", "corn"),
    229: ("Pumpkins", None), 234: ("Dbl Crop Durum Wht/Sorghum", "winter_wheat>sorghum"),
    235: ("Dbl Crop Barley/Sorghum", "sorghum"), 236: ("Dbl Crop WinWht/Sorghum", "winter_wheat>sorghum"),
    237: ("Dbl Crop Barley/Corn", "corn"), 238: ("Dbl Crop WinWht/Cotton", "winter_wheat>cotton"),
    239: ("Dbl Crop Soybeans/Cotton", "soybean>cotton"), 240: ("Dbl Crop Soybeans/Oats", "soybean"),
    241: ("Dbl Crop Corn/Soybeans", "corn>soybean"), 242: ("Blueberries", None), 243: ("Cabbage", None),
    244: ("Cauliflower", None), 245: ("Celery", None), 246: ("Radishes", None), 247: ("Turnips", None),
    248: ("Eggplants", None), 249: ("Gourds", None), 250: ("Cranberries", None),
    254: ("Dbl Crop Barley/Soybeans", "soybean"),
}
_NONCROP_TABLE: dict[int, tuple[str, str]] = {
    61: ("Fallow/Idle Cropland", "fallow"),
    63: ("Forest", "forest"), 141: ("Deciduous Forest", "forest"), 142: ("Evergreen Forest", "forest"),
    143: ("Mixed Forest", "forest"), 64: ("Shrubland", "grass"), 152: ("Shrubland", "grass"),
    176: ("Grassland/Pasture", "grass"), 62: ("Pasture/Grass", "grass"),
    65: ("Barren", "other"), 131: ("Barren", "other"), 81: ("Clouds/No Data", "other"),
    82: ("Developed", "developed"), 121: ("Developed/Open Space", "developed"),
    122: ("Developed/Low Intensity", "developed"), 123: ("Developed/Med Intensity", "developed"),
    124: ("Developed/High Intensity", "developed"),
    83: ("Water", "water"), 111: ("Open Water", "water"), 92: ("Aquaculture", "water"),
    112: ("Perennial Ice/Snow", "water"),
    87: ("Wetlands", "other"), 190: ("Woody Wetlands", "other"), 195: ("Herbaceous Wetlands", "other"),
    88: ("Nonag/Undefined", "other"), 0: ("Background", "other"),
}
CDL_CODES: dict[int, dict[str, Any]] = {
    **{c: {"name": n, "crop_key": k,
           "category": "fallow" if c == 61 else "crop"} for c, (n, k) in _CROP_TABLE.items()},
    **{c: {"name": n, "crop_key": "fallow" if cat == "fallow" else None, "category": cat}
       for c, (n, cat) in _NONCROP_TABLE.items()},
}


def cdl_category(code: int | None) -> str:
    """CDL kodu -> crop | fallow | developed | forest | water | grass | other."""
    if code is None:
        return "other"
    return CDL_CODES.get(int(code), {"category": "other"})["category"]


def is_crop_code(code: int | None) -> bool:
    """Gerçek ekili ürün mü (nadas, çayır, yapay alan hariç)."""
    return cdl_category(code) == "crop"


def crop_key_for(code: int | None) -> str | None:
    if code is None:
        return None
    return CDL_CODES.get(int(code), {}).get("crop_key")


# ---------------------------------------------------------------- HTTP + önbellek
_rate_lock = threading.Lock()
_last_call = 0.0
_cache_lock = threading.Lock()


def _throttle() -> None:
    global _last_call
    with _rate_lock:
        wait = _last_call + MIN_INTERVAL_S - _time.monotonic()
        if wait > 0:
            _time.sleep(wait)
        _last_call = _time.monotonic()


def _http(url: str, data: bytes | None = None, headers: dict | None = None,
          timeout: float = TIMEOUT_S, retries: int = MAX_RETRIES) -> str:
    """GET/POST; üstel geri çekilme + jitter ile yeniden dener (servis bazen yavaş)."""
    last: Exception | None = None
    for attempt in range(retries + 1):
        _throttle()
        try:
            req = urllib.request.Request(url, data=data,
                                         headers={"User-Agent": USER_AGENT, **(headers or {})})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500 and e.code != 429:
                raise RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}") from e
            last = e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
        if attempt < retries:
            delay = 2 ** attempt + random.uniform(0, 1)
            log.warning("retry %d/%d in %.1fs: %s", attempt + 1, retries, delay, last)
            _time.sleep(delay)
    raise RuntimeError(f"giving up on {url}: {last}")


class DiskCache:
    """Küçük JSON anahtar-değer önbelleği (tekrar çalıştırmada istek tekrarını önler)."""

    def __init__(self, path: Path | str | None):
        self.path = Path(path) if path else None
        self.data: dict[str, Any] = {}
        self._dirty = 0
        if self.path and self.path.exists():
            try:
                self.data = json.loads(self.path.read_text())
            except (OSError, json.JSONDecodeError):
                log.warning("cache okunamadı: %s", self.path)

    def get(self, key: str, default=None):
        with _cache_lock:
            return self.data.get(key, default)

    def put(self, key: str, value: Any, flush_every: int = 25) -> None:
        with _cache_lock:
            self.data[key] = value
            self._dirty += 1
            if self._dirty >= flush_every:
                self._flush_locked()

    def _flush_locked(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data))
            tmp.replace(self.path)
        self._dirty = 0

    def flush(self) -> None:
        with _cache_lock:
            self._flush_locked()


_NO_CACHE = DiskCache(None)


def default_cache() -> DiskCache:
    return DiskCache(CACHE_DIR / HTTP_CACHE_FILE)


# ---------------------------------------------------------------- CDL
_CDL_RE = re.compile(r"value:\s*(-?\d+)\s*,\s*category:\s*\"([^\"]*)\"")


def parse_cdl_response(text: str) -> tuple[int, str] | None:
    """GetCDLValue yanıtı -> (kod, ad); veri yoksa/ABD dışıysa None.

    Gerçek yanıt: <Result>{x: -250000.0, y: 2000000.0, value: 176, category: "Grass/Pasture", color: "#E9FFBE"}</Result>
    """
    m = _CDL_RE.search(text)
    if not m:
        return None
    return int(m.group(1)), m.group(2)


def cdl_value(lat: float, lon: float, year: int, cache: DiskCache | None = None) -> tuple[int, str] | None:
    """Noktanın o yıl CDL sınıfı: (kod, ad) ya da None."""
    cache = cache or _NO_CACHE
    key = f"cdl|{lat:.5f}|{lon:.5f}|{year}"
    hit = cache.get(key)
    if hit is not None:
        return None if hit == "none" else (int(hit[0]), hit[1])
    x, y = albers_5070(lat, lon)
    text = _http(f"{CDL_URL}?year={int(year)}&x={x:.1f}&y={y:.1f}")
    res = parse_cdl_response(text)
    cache.put(key, "none" if res is None else [res[0], res[1]])
    return res


def cdl_history(lat: float, lon: float, years: Iterable[int], cache: DiskCache | None = None,
                workers: int = 1) -> pd.DataFrame:
    """Yıl yıl CDL geçmişi: year, cdl_code, cdl_name, crop_key, category."""
    years = list(years)

    def one(y: int) -> dict:
        try:
            res = cdl_value(lat, lon, y, cache)
        except RuntimeError as e:
            log.warning("CDL %s,%s %s: %s", lat, lon, y, e)
            res = None
        code = res[0] if res else None
        return {"year": y, "cdl_code": code, "cdl_name": res[1] if res else None,
                "crop_key": crop_key_for(code), "category": cdl_category(code) if res else None}

    if workers > 1:
        with ThreadPoolExecutor(workers) as ex:
            rows = list(ex.map(one, years))
    else:
        rows = [one(y) for y in years]
    df = pd.DataFrame(rows, columns=["year", "cdl_code", "cdl_name", "crop_key", "category"])
    df["cdl_code"] = df["cdl_code"].astype("Int64")
    return df


def _main_crop(key: str | None) -> str | None:
    """Çift ürün 'a>b' -> ana (ilk) ürün; rotasyon dizisinde tek sezon olarak sayılır."""
    return key.split(">")[0] if key else None


def infer_rotation(history: pd.DataFrame | Sequence[str | None], last_n: int = 6) -> dict:
    """Son ~6 yıldan mevcut rotasyonu çıkarır (periyot 1-3, tek aykırı yıla tolerans).

    Döner: {rotation: 'corn>soybean' | None, confidence: 0..1, sequence: [...], period}.
    Sıra: eskiden yeniye; rotasyon dizisi en eski yılın fazından başlar.
    """
    if isinstance(history, pd.DataFrame):
        h = history.sort_values("year")
        seq = [_main_crop(k if isinstance(k, str) else None) for k in h["crop_key"].tolist()]
    else:
        seq = [_main_crop(k) for k in history]
    seq = seq[-last_n:]
    known = [s for s in seq if s]
    out = {"rotation": None, "confidence": 0.0, "sequence": seq, "period": None}
    if len(known) < 3:
        return out
    n = len(seq)
    best = None
    for p in (1, 2, 3):
        if n < 2 * p and p > 1:
            continue
        cycle, mism = [], 0
        for ph in range(p):
            vals = [s for s in seq[ph::p] if s]
            if not vals:
                cycle.append(None)
                continue
            top, cnt = Counter(vals).most_common(1)[0]
            cycle.append(top)
            mism += len(vals) - cnt
        mism += sum(1 for s in seq if not s)  # bilinmeyen yıl da bir sapma
        if None in cycle:
            continue
        if p > 1 and len(set(cycle)) == 1:
            continue  # periyot 1'in tekrarı
        if p > 1 and any(cycle[i] == cycle[(i + 1) % p] for i in range(p)) and p == 2:
            continue  # [a, a] gibi
        best_here = (mism, p, cycle)
        if mism <= 1:
            best = best_here
            break  # en kısa uygun periyot
        if best is None or mism < best[0]:
            best = best_here
    if best is None:
        return out
    mism, p, cycle = best
    conf = max(0.0, 1 - mism / n) * min(1.0, n / 6)
    if mism > 1:
        conf *= 0.5
    out.update(rotation=">".join(cycle), confidence=round(conf, 3), period=p)
    return out


# ---------------------------------------------------------------- Toprak (SSURGO / SDA)
def texture_class(sand: float | None, clay: float | None, silt: float | None = None) -> str | None:
    """USDA tekstür üçgeni (sand/silt/clay yüzde)."""
    if sand is None or clay is None:
        return None
    if silt is None:
        silt = 100.0 - sand - clay
    if silt + 1.5 * clay < 15:
        return "sand"
    if silt + 1.5 * clay >= 15 and silt + 2 * clay < 30:
        return "loamy_sand"
    if (7 <= clay < 20 and sand > 52 and silt + 2 * clay >= 30) or (clay < 7 and silt < 50 and silt + 2 * clay >= 30):
        return "sandy_loam"
    if 7 <= clay < 27 and 28 <= silt < 50 and sand <= 52:
        return "loam"
    if (silt >= 50 and 12 <= clay < 27) or (50 <= silt < 80 and clay < 12):
        return "silt_loam"
    if silt >= 80 and clay < 12:
        return "silt"
    if 20 <= clay < 35 and silt < 28 and sand > 45:
        return "sandy_clay_loam"
    if 27 <= clay < 40 and 20 < sand <= 45:
        return "clay_loam"
    if 27 <= clay < 40 and sand <= 20:
        return "silty_clay_loam"
    if clay >= 35 and sand > 45:
        return "sandy_clay"
    if clay >= 40 and silt >= 40:
        return "silty_clay"
    if clay >= 40 and sand <= 45 and silt < 40:
        return "clay"
    return "loam"


SOIL_SQL = """SELECT TOP 1 mu.mukey, mu.muname, c.compname, c.comppct_r, c.slope_r, c.hydgrp, c.drainagecl,
 h.om_r, h.claytotal_r, h.sandtotal_r, h.silttotal_r, h.awc_r, h.ph1to1h2o_r, h.ksat_r
FROM mapunit mu JOIN component c ON c.mukey = mu.mukey
LEFT JOIN chorizon h ON h.cokey = c.cokey AND h.hzdept_r = 0
WHERE mu.mukey IN (SELECT mukey FROM SDA_Get_Mukey_from_intersection_with_WktWgs84('point({lon} {lat})'))
ORDER BY c.comppct_r DESC, h.hzdepb_r DESC"""

SOIL_FIELDS = ["mukey", "muname", "compname", "comppct", "om_pct", "clay_pct", "sand_pct", "silt_pct",
               "awc_cm_cm", "ph", "ksat_um_s", "slope_pct", "hydgrp", "drainagecl", "texture", "soil_texture"]


def _num(v: Any) -> float | None:
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def parse_sda_response(text: str) -> dict | None:
    """SDA 'JSON+COLUMNNAME' yanıtı -> soil dict; satır yoksa (ABD dışı/boş) None.

    Gerçek yanıt: {"Table":[["mukey","muname",...],["2765522","Clarion loam, ...",...]]}
    (boş sonuçta gövde boş olabilir). Baskın bileşen = max comppct_r.
    """
    if not text or not text.strip():
        return None
    try:
        tab = json.loads(text).get("Table")
    except json.JSONDecodeError:
        return None
    if not tab or len(tab) < 2:
        return None
    r = dict(zip(tab[0], tab[1]))
    if re.search(r"\bwater\b", r.get("muname") or "", re.I) and not r.get("claytotal_r"):
        return None
    clay, sand, silt = _num(r.get("claytotal_r")), _num(r.get("sandtotal_r")), _num(r.get("silttotal_r"))
    tex = texture_class(sand, clay, silt)
    return {
        "mukey": r.get("mukey"), "muname": r.get("muname"), "compname": r.get("compname"),
        "comppct": _num(r.get("comppct_r")), "om_pct": _num(r.get("om_r")),
        "clay_pct": clay, "sand_pct": sand, "silt_pct": silt, "awc_cm_cm": _num(r.get("awc_r")),
        "ph": _num(r.get("ph1to1h2o_r")), "ksat_um_s": _num(r.get("ksat_r")),
        "slope_pct": _num(r.get("slope_r")), "hydgrp": r.get("hydgrp"), "drainagecl": r.get("drainagecl"),
        "texture": tex.replace("_", " ") if tex else None, "soil_texture": tex,
    }


def soil_at(lat: float, lon: float, cache: DiskCache | None = None) -> dict | None:
    """Noktadaki SSURGO baskın bileşeninin yüzey horizonu; veri yoksa None."""
    cache = cache or _NO_CACHE
    key = f"soil|{lat:.5f}|{lon:.5f}"
    hit = cache.get(key)
    if hit is not None:
        return None if hit == "none" else hit
    body = json.dumps({"query": SOIL_SQL.format(lat=f"{lat:.6f}", lon=f"{lon:.6f}"),
                       "format": "JSON+COLUMNNAME"}).encode()
    text = _http(SDA_URL, data=body, headers={"Content-Type": "application/json"})
    res = parse_sda_response(text)
    cache.put(key, "none" if res is None else res)
    return res


# ---------------------------------------------------------------- Tarla seçimi
# (state, region, county/etiket, lat, lon): tarım ağırlıklı ilçe merkezlerine yakın, elle seçilmiş.
_SEEDS: list[tuple[str, str, str, list[tuple[float, float]]]] = [
    ("ia", "corn_belt", "story", [(42.03, -93.74), (42.10, -93.60), (41.95, -93.85)]),
    ("ia", "corn_belt", "webster", [(42.45, -94.20), (42.55, -94.35)]),
    ("ia", "corn_belt", "sioux", [(43.10, -96.15), (43.05, -96.00)]),
    ("ia", "corn_belt", "cedar", [(41.75, -91.20), (41.80, -91.05)]),
    ("il", "corn_belt", "champaign", [(40.04, -88.23), (40.15, -88.40), (39.95, -88.10)]),
    ("il", "corn_belt", "mclean", [(40.45, -88.90), (40.55, -88.75)]),
    ("il", "corn_belt", "macon", [(39.85, -88.95), (39.75, -88.80)]),
    ("il", "corn_belt", "henry", [(41.35, -90.20), (41.45, -90.05)]),
    ("in", "corn_belt", "tippecanoe", [(40.40, -86.90), (40.30, -86.80)]),
    ("in", "corn_belt", "carroll", [(40.60, -86.60), (40.70, -86.50)]),
    ("in", "corn_belt", "rush", [(39.60, -85.45), (39.65, -85.30)]),
    ("oh", "corn_belt", "darke", [(40.20, -84.60), (40.10, -84.50)]),
    ("oh", "corn_belt", "wood", [(41.35, -83.60), (41.30, -83.75)]),
    ("oh", "corn_belt", "fayette", [(39.55, -83.45), (39.50, -83.60)]),
    ("mn", "corn_belt", "blue_earth", [(44.05, -94.05), (44.15, -94.20)]),
    ("mn", "corn_belt", "stearns", [(45.55, -94.60), (45.45, -94.75)]),
    ("mn", "corn_belt", "redwood", [(44.40, -95.20), (44.55, -95.05)]),
    ("ne", "corn_belt", "hamilton", [(40.78, -98.00), (40.85, -98.15)]),
    ("ne", "corn_belt", "saunders", [(41.25, -96.60), (41.15, -96.75)]),
    ("ne", "corn_belt", "buffalo", [(40.75, -99.20), (40.70, -99.05)]),
    ("sd", "corn_belt", "brookings", [(44.35, -96.80), (44.25, -96.95)]),
    ("sd", "corn_belt", "minnehaha", [(43.65, -96.75), (43.75, -96.60)]),
    ("ks", "great_plains", "pratt_stafford", [(37.90, -98.20), (37.95, -98.35), (37.85, -98.05)]),
    ("ks", "great_plains", "sumner", [(37.35, -97.40), (37.25, -97.25)]),
    ("ks", "great_plains", "finney", [(38.05, -100.80), (37.95, -100.95)]),
    ("ks", "great_plains", "ford", [(37.70, -99.90), (37.60, -100.05)]),
    ("ok", "great_plains", "grant", [(36.75, -98.05), (36.65, -97.90)]),
    ("ok", "great_plains", "texas", [(36.65, -101.45), (36.75, -101.30)]),
    ("tx", "great_plains", "hansford", [(36.25, -101.35), (36.20, -101.50)]),
    ("tx", "great_plains", "castro", [(34.55, -102.25), (34.45, -102.40)]),
    ("co", "great_plains", "yuma", [(40.05, -102.55), (40.15, -102.70)]),
    ("co", "great_plains", "weld", [(40.40, -104.50), (40.30, -104.35)]),
    ("nd", "great_plains", "cass", [(46.90, -97.30), (46.80, -97.15)]),
    ("nd", "great_plains", "ward", [(48.35, -101.60), (48.25, -101.45)]),
    ("mt", "great_plains", "teton", [(47.85, -111.90), (47.75, -111.75)]),
    ("mt", "great_plains", "hill", [(48.60, -110.20), (48.50, -110.35)]),
    ("ms", "delta_southeast", "bolivar", [(33.75, -90.85), (33.65, -90.70)]),
    ("ms", "delta_southeast", "sunflower", [(33.55, -90.60), (33.45, -90.50)]),
    ("ar", "delta_southeast", "poinsett", [(35.55, -90.70), (35.45, -90.55)]),
    ("ar", "delta_southeast", "arkansas", [(34.35, -91.35), (34.25, -91.20)]),
    ("la", "delta_southeast", "tensas", [(32.10, -91.20), (32.20, -91.10)]),
    ("la", "delta_southeast", "madison", [(32.40, -91.15), (32.30, -91.25)]),
    ("ga", "delta_southeast", "mitchell", [(31.25, -84.20), (31.35, -84.10)]),
    ("ga", "delta_southeast", "dooly", [(32.15, -83.80), (32.05, -83.70)]),
    ("al", "delta_southeast", "limestone", [(34.80, -86.95), (34.70, -87.05)]),
    ("al", "delta_southeast", "baldwin", [(30.65, -87.75), (30.55, -87.65)]),
    ("ca", "central_valley", "fresno", [(36.50, -120.10), (36.40, -119.90), (36.60, -120.25)]),
    ("ca", "central_valley", "kern", [(35.35, -119.20), (35.45, -119.35)]),
    ("ca", "central_valley", "san_joaquin", [(37.90, -121.25), (37.85, -121.40)]),
    ("ca", "central_valley", "colusa", [(39.25, -122.00), (39.15, -121.95)]),
    ("wa", "pacific_nw", "whitman", [(46.85, -117.50), (46.75, -117.35)]),
    ("wa", "pacific_nw", "lincoln", [(47.35, -118.40), (47.25, -118.25)]),
    ("wa", "pacific_nw", "walla_walla", [(46.10, -118.50), (46.00, -118.35)]),
    ("or", "pacific_nw", "umatilla", [(45.80, -118.80), (45.70, -118.65)]),
    ("or", "pacific_nw", "morrow", [(45.65, -119.60), (45.75, -119.75)]),
    ("or", "pacific_nw", "sherman", [(45.55, -120.65), (45.45, -120.50)]),
    ("mo", "corn_belt", "audrain", [(39.20, -91.85), (39.15, -91.70)]),
    ("wi", "corn_belt", "dodge", [(43.50, -88.60), (43.60, -88.75)]),
    ("mi", "corn_belt", "huron", [(43.80, -83.05), (43.70, -82.95)]),
    ("nc", "delta_southeast", "pitt", [(35.60, -77.35), (35.50, -77.20)]),
    ("tn", "delta_southeast", "haywood", [(35.60, -89.30), (35.70, -89.15)]),
    ("tx", "great_plains", "lubbock", [(33.60, -101.90), (33.70, -101.75)]),
    ("ne", "corn_belt", "perkins", [(40.85, -101.60), (40.95, -101.75)]),
    ("ks", "great_plains", "dickinson", [(38.80, -97.20), (38.70, -97.05)]),
]


def candidate_points() -> list[dict]:
    """~130 aday nokta: area_id, lat, lon, state, region (çeşitli ABD ekim bölgeleri)."""
    out, seen = [], Counter()
    for state, region, county, pts in _SEEDS:
        for lat, lon in pts:
            seen[(state, county)] += 1
            out.append({"area_id": f"{state}_{county}_{seen[(state, county)]:02d}",
                        "lat": lat, "lon": lon, "state": state.upper(), "region": region})
    return out


def select_fields(candidates: Sequence[dict], years: Iterable[int] = range(2015, 2026),
                  min_crop_years: int = 8, cache: DiskCache | None = None, workers: int = 3,
                  max_n: int | None = None) -> tuple[list[dict], pd.DataFrame]:
    """CDL'e göre en az min_crop_years yıl ekili ürün olan adayları tutar.

    Döner: (tarla dict listesi [existing_rotation, rotation_confidence, crop_history], uzun CDL tablosu).
    """
    years = list(years)
    kept, frames = [], []
    for c in candidates:
        if max_n is not None and len(kept) >= max_n:
            break
        hist = cdl_history(c["lat"], c["lon"], years, cache, workers=workers)
        n_crop = int(hist["cdl_code"].map(lambda v: is_crop_code(None if pd.isna(v) else v)).sum())
        if n_crop < min_crop_years:
            log.info("elendi %s: %d ekili yıl", c["area_id"], n_crop)
            continue
        inf = infer_rotation(hist)
        d = 0.002
        kept.append({**c, "bbox": [round(c["lon"] - d, 6), round(c["lat"] - d, 6),
                                   round(c["lon"] + d, 6), round(c["lat"] + d, 6)],
                     "existing_rotation": inf["rotation"], "rotation_confidence": inf["confidence"],
                     "crop_history": [{"year": int(r.year), "cdl_code": None if pd.isna(r.cdl_code) else int(r.cdl_code),
                                       "crop_key": r.crop_key} for r in hist.itertuples()]})
        frames.append(hist.assign(area_id=c["area_id"]))
    cols = ["area_id", "year", "cdl_code", "cdl_name", "crop_key"]
    allh = pd.concat(frames)[cols] if frames else pd.DataFrame(columns=cols)
    return kept, allh.reset_index(drop=True)


# ---------------------------------------------------------------- CLI
def _years(s: str) -> list[int]:
    a, _, b = s.partition("-")
    return list(range(int(a), int(b or a) + 1))


def _cmd_select(a) -> None:
    cache = default_cache() if not a.cache else DiskCache(a.cache)
    out = Path(a.out)
    static = Path(a.static_dir)
    static.mkdir(parents=True, exist_ok=True)
    cands = candidate_points()
    try:
        kept, hist = select_fields(cands, _years(a.years), a.min_crop_years, cache, a.workers, a.max)
        soils = []
        for f in kept:
            s = soil_at(f["lat"], f["lon"], cache)
            soils.append({"area_id": f["area_id"], **(s or {})})
            if s:
                f["soil_texture"] = s.get("soil_texture")
    finally:
        cache.flush()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(kept, indent=1, ensure_ascii=False))
    hist.to_parquet(static / "cdl_history.parquet", index=False)
    pd.DataFrame(soils).to_parquet(static / "soil.parquet", index=False)
    print(f"{len(kept)}/{len(cands)} aday kabul -> {out}, {static}")


def _cmd_static(a) -> None:
    """Var olan alan dosyasından (seçim tekrar yapılmadan) statik tabloları kurar: CDL geçmişi dosyanın içindeki
    crop_history'den (ağ yok), toprak SSURGO'dan (alan başına 1 hızlı istek)."""
    cache = default_cache() if not a.cache else DiskCache(a.cache)
    areas = json.loads(Path(a.areas).read_text())
    static = Path(a.static_dir)
    static.mkdir(parents=True, exist_ok=True)
    rows, soils = [], []
    try:
        for f in areas:
            for h in f.get("crop_history", []):
                code = h.get("cdl_code")
                rows.append({"area_id": f["area_id"], "year": int(h["year"]), "cdl_code": code,
                             "cdl_name": CDL_CODES.get(code, {}).get("name") if code is not None else None,
                             "crop_key": h.get("crop_key")})
            try:
                s = soil_at(f["lat"], f["lon"], cache)
            except RuntimeError as e:
                log.warning("toprak %s: %s", f["area_id"], e)
                s = None
            soils.append({"area_id": f["area_id"], **(s or {})})
    finally:
        cache.flush()
    hist = pd.DataFrame(rows, columns=["area_id", "year", "cdl_code", "cdl_name", "crop_key"])
    hist["cdl_code"] = hist["cdl_code"].astype("Int64")
    hist.to_parquet(static / "cdl_history.parquet", index=False)
    pd.DataFrame(soils).to_parquet(static / "soil.parquet", index=False)
    n_soil = sum(1 for s in soils if len(s) > 1)
    print(f"{len(areas)} tarla: CDL geçmişi {hist['area_id'].nunique()} tarla, toprak {n_soil} tarla -> {static}")


def _cmd_point(a) -> None:
    cache = default_cache() if not a.cache else DiskCache(a.cache)
    try:
        h = cdl_history(a.lat, a.lon, _years(a.years), cache)
        print(h.to_string(index=False))
        print("rotation:", json.dumps(infer_rotation(h)))
        print("soil:", json.dumps(soil_at(a.lat, a.lon, cache), indent=1))
    finally:
        cache.flush()


def main(argv: Sequence[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="ml.data_load.fields")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("select", help="adayları CDL ile doğrula, areas + parquet yaz")
    s.add_argument("--out", default="ml/data_load/areas.us.json")
    s.add_argument("--static-dir", default=str(CACHE_DIR))
    s.add_argument("--years", default="2015-2025")
    s.add_argument("--min-crop-years", type=int, default=8)
    s.add_argument("--max", type=int, default=None)
    s.add_argument("--workers", type=int, default=3)
    s.add_argument("--cache", default=None, help="HTTP önbellek dosyası (varsayılan ml/cache/static)")
    s.set_defaults(fn=_cmd_select)
    t = sub.add_parser("static", help="var olan alan dosyasından CDL/toprak tablolarını kur (seçim yok)")
    t.add_argument("--areas", default="ml/data_load/areas.us.json")
    t.add_argument("--static-dir", default=str(CACHE_DIR))
    t.add_argument("--cache", default=None)
    t.set_defaults(fn=_cmd_static)
    p = sub.add_parser("point", help="tek nokta: geçmiş + rotasyon + toprak")
    p.add_argument("--lat", type=float, required=True)
    p.add_argument("--lon", type=float, required=True)
    p.add_argument("--years", default="2018-2025")
    p.add_argument("--cache", default=None)
    p.set_defaults(fn=_cmd_point)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    a.fn(a)


if __name__ == "__main__":
    main()
