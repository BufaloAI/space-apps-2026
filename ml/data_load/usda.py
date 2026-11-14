"""USDA/OpenET: ilçe bulma (Census), NASS QuickStats verim/fiyat, OpenET aylık ET.

API anahtarları ml/.env'den okunur (NASS_API_KEY, OPENET_API_KEY); asla loglanmaz/yazılmaz.
CLI:  uv run python -m ml.data_load.usda --areas ml/data_load/areas.us.json \
        --start 2024-01-01 --end 2025-12-31 --years 2015-2025 \
        --static-out ml/cache/static --raw-out ml/cache/raw --what counties,yields,prices,openet
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import sys
import threading
import time as _time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

from ml.data_load.download import write_raw

log = logging.getLogger("ml.data_load.usda")

USER_AGENT = "field-shift-research/0.1"
TIMEOUT_S = 90
MAX_RETRIES = 4
MIN_INTERVAL_S = 0.6
ENV_FILE = Path(__file__).resolve().parents[1] / ".env"

CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/geographies/coordinates"
NASS_URL = "https://quickstats.nass.usda.gov/api/api_GET/"
NASS_COUNTS_URL = "https://quickstats.nass.usda.gov/api/get_counts/"
OPENET_URL = "https://openet-api.org/raster/timeseries/point"
NASS_ROW_LIMIT = 50000

# OpenET kapsamı: 17 batı eyaleti
OPENET_STATES = {"AZ", "CA", "CO", "ID", "KS", "MT", "NE", "NV", "NM", "ND", "OK", "OR", "SD", "TX", "UT", "WA", "WY"}

# ---------------------------------------------------------------- birimler
LB_KG = 0.45359237
ACRE_HA = 0.40468564224
# bushel başına lb: mısır/sorgum 56, soya/buğday 60 (NASS standardı)
BU_LB = {"corn": 56.0, "sorghum": 56.0, "soybean": 60.0, "winter_wheat": 60.0}
SHORT_TON_KG = 907.18474

# crop_key -> NASS short_desc (verim, fiyat)
CROPS: dict[str, dict[str, str]] = {
    "corn": {"yield": "CORN, GRAIN - YIELD, MEASURED IN BU / ACRE",
             "price": "CORN, GRAIN - PRICE RECEIVED, MEASURED IN $ / BU"},
    "soybean": {"yield": "SOYBEANS - YIELD, MEASURED IN BU / ACRE",
                "price": "SOYBEANS - PRICE RECEIVED, MEASURED IN $ / BU"},
    "winter_wheat": {"yield": "WHEAT, WINTER - YIELD, MEASURED IN BU / ACRE",
                     "price": "WHEAT, WINTER - PRICE RECEIVED, MEASURED IN $ / BU"},
    "sorghum": {"yield": "SORGHUM, GRAIN - YIELD, MEASURED IN BU / ACRE",
                "price": "SORGHUM, GRAIN - PRICE RECEIVED, MEASURED IN $ / CWT"},
    "cotton": {"yield": "COTTON, UPLAND - YIELD, MEASURED IN LB / ACRE",
               "price": "COTTON, UPLAND - PRICE RECEIVED, MEASURED IN $ / LB"},
    "sunflower": {"yield": "SUNFLOWER - YIELD, MEASURED IN LB / ACRE",
                  "price": "SUNFLOWER - PRICE RECEIVED, MEASURED IN $ / CWT"},
    "chickpea": {"yield": "CHICKPEAS - YIELD, MEASURED IN LB / ACRE",
                 "price": "CHICKPEAS - PRICE RECEIVED, MEASURED IN $ / CWT"},
    "alfalfa": {"yield": "HAY, ALFALFA - YIELD, MEASURED IN TONS / ACRE",
                "price": "HAY, ALFALFA - PRICE RECEIVED, MEASURED IN $ / TON"},
}
DESC_TO_CROP = {v[k]: c for c, v in CROPS.items() for k in v}
NA_CODES = {"", "(D)", "(Z)", "(NA)", "(S)", "(X)", "(L)", "(H)"}


def crop_of_desc(short_desc: str) -> str | None:
    """NASS short_desc -> crop_key (tam eşleşme, yoksa None)."""
    return DESC_TO_CROP.get(short_desc.strip().upper())


def parse_value(v: Any) -> float:
    """NASS değer dizgisi ('1,234.5', '(D)') -> float / NaN."""
    s = str(v).strip().replace(",", "")
    if s.upper() in NA_CODES or not re.fullmatch(r"-?\d+(\.\d+)?", s):
        return float("nan")
    return float(s)


def yield_to_t_ha(value: float, unit: str, crop_key: str) -> float:
    """Verim -> t/ha. bu/ac: bu_lb*0.4536/0.4047/1000 (mısır 0.0628); lb/ac: *1.1209/1000;
    short ton/ac: *907.18/0.4047/1000 (=2.2417)."""
    u = unit.upper().replace(" ", "")
    if u == "BU/ACRE":
        return value * BU_LB[crop_key] * LB_KG / ACRE_HA / 1000
    if u == "LB/ACRE":
        return value * LB_KG / ACRE_HA / 1000
    if u == "TONS/ACRE":
        return value * SHORT_TON_KG / ACRE_HA / 1000
    return float("nan")


def price_usd_per_t(value: float, unit: str, crop_key: str) -> float:
    """Fiyat -> $/t (short ton = 907.18 kg; bu: ürün başına lb tablosu; CWT=100 lb)."""
    u = unit.upper().replace(" ", "")
    if u == "$/BU":
        return value / (BU_LB[crop_key] * LB_KG) * 1000
    if u == "$/CWT":
        return value / (100 * LB_KG) * 1000
    if u == "$/LB":
        return value / LB_KG * 1000
    if u == "$/TON":
        return value / SHORT_TON_KG * 1000
    return float("nan")


# ---------------------------------------------------------------- anahtar + HTTP
def load_key(name: str) -> str | None:
    """Anahtarı ortamdan / ml/.env'den okur; yoksa None."""
    from dotenv import load_dotenv
    load_dotenv(ENV_FILE)
    return os.environ.get(name) or None


def scrub(text: str) -> str:
    """Hata metninden key=... değerlerini ve ortam anahtarlarını siler."""
    text = re.sub(r"(?i)(key=)[^&\s'\"]+", r"\1***", str(text))
    for n in ("NASS_API_KEY", "OPENET_API_KEY"):
        k = os.environ.get(n)
        if k and len(k) > 4:
            text = text.replace(k, "***")
    return text


_rate_lock = threading.Lock()
_last_call: dict[str, float] = {}


def _throttle(host: str) -> None:
    with _rate_lock:
        wait = _last_call.get(host, 0.0) + MIN_INTERVAL_S - _time.monotonic()
        if wait > 0:
            _time.sleep(wait)
        _last_call[host] = _time.monotonic()


def _request_json(req: urllib.request.Request, timeout: float = TIMEOUT_S,
                  retries: int = MAX_RETRIES) -> Any:
    """JSON isteği: timeout + üstel geri çekilme. 4xx (429 hariç) -> RuntimeError, anında."""
    host = urllib.parse.urlparse(req.full_url).netloc
    last: Exception | None = None
    for attempt in range(retries + 1):
        _throttle(host)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = scrub(e.read().decode("utf-8", "replace")[:300])
            if 400 <= e.code < 500 and e.code != 429:
                raise RuntimeError(f"HTTP {e.code}: {body}") from None
            last = RuntimeError(f"HTTP {e.code}")
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as e:
            last = RuntimeError(scrub(repr(e)))
        if attempt < retries:
            delay = 2 ** attempt + random.uniform(0, 1)
            log.warning("retry %d/%d in %.1fs: %s", attempt + 1, retries, delay, scrub(last))
            _time.sleep(delay)
    raise RuntimeError(f"giving up on {scrub(req.full_url)}: {scrub(last)}")


class JsonCache:
    """Basit disk JSON önbelleği (anahtar: anahtarsız istek dizgisi)."""

    def __init__(self, path: str | Path | None):
        self.path = Path(path) if path else None
        self.data: dict[str, Any] = {}
        if self.path and self.path.exists():
            try:
                self.data = json.loads(self.path.read_text())
            except json.JSONDecodeError:
                self.data = {}

    def get(self, k: str):
        return self.data.get(k)

    def put(self, k: str, v: Any) -> None:
        self.data[k] = v
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data))
            os.replace(tmp, self.path)


# ---------------------------------------------------------------- Census ilçe
def parse_county(resp: dict) -> dict | None:
    """Census geocoder yanıtından 'Counties' katmanını ayıklar."""
    geos = (resp.get("result") or {}).get("geographies") or {}
    items = geos.get("Counties") or []
    if not items:
        return None
    c = items[0]
    return {"state_fips": c["STATE"], "county_fips": c["COUNTY"],
            "state_alpha": STATE_ALPHA.get(c["STATE"]), "county_name": c.get("BASENAME") or c.get("NAME")}


def county_of(lat: float, lon: float, cache: JsonCache | None = None) -> dict | None:
    """(lat, lon) -> {state_fips, county_fips, state_alpha, county_name}; bulunamazsa None."""
    k = f"census:{lat:.5f},{lon:.5f}"
    if cache is not None and (hit := cache.get(k)) is not None:
        return hit or None
    q = urllib.parse.urlencode({"x": lon, "y": lat, "benchmark": "Public_AR_Current",
                                "vintage": "Current_Current", "format": "json", "layers": "Counties"})
    resp = _request_json(urllib.request.Request(f"{CENSUS_URL}?{q}", headers={"User-Agent": USER_AGENT}))
    out = parse_county(resp)
    if cache is not None:
        cache.put(k, out or {})
    return out


STATE_ALPHA = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO", "09": "CT", "10": "DE",
    "11": "DC", "12": "FL", "13": "GA", "15": "HI", "16": "ID", "17": "IL", "18": "IN", "19": "IA",
    "20": "KS", "21": "KY", "22": "LA", "23": "ME", "24": "MD", "25": "MA", "26": "MI", "27": "MN",
    "28": "MS", "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH", "34": "NJ", "35": "NM",
    "36": "NY", "37": "NC", "38": "ND", "39": "OH", "40": "OK", "41": "OR", "42": "PA", "44": "RI",
    "45": "SC", "46": "SD", "47": "TN", "48": "TX", "49": "UT", "50": "VT", "51": "VA", "53": "WA",
    "54": "WV", "55": "WI", "56": "WY",
}


# ---------------------------------------------------------------- NASS
def _nass_get(params: list[tuple[str, str]], cache: JsonCache | None, key: str | None = None) -> list[dict]:
    """QuickStats GET; yanıt (anahtarsız) önbelleğe alınır. 'no data' (4xx) -> boş liste."""
    ck = "nass:" + urllib.parse.urlencode(sorted(params))
    if cache is not None and (hit := cache.get(ck)) is not None:
        return hit
    key = key or load_key("NASS_API_KEY")
    if not key:
        raise MissingKey("NASS_API_KEY")
    full = [("key", key), ("format", "JSON"), *params]
    req = urllib.request.Request(f"{NASS_URL}?{urllib.parse.urlencode(full)}",
                                 headers={"User-Agent": USER_AGENT})
    try:
        rows = _request_json(req).get("data", [])
    except RuntimeError as e:
        # NASS: eşleşme yoksa 400 "bad request - invalid query" döner -> boş sonuç
        if "HTTP 400" in str(e):
            rows = []
        else:
            raise
    if len(rows) >= NASS_ROW_LIMIT:
        raise RuntimeError("NASS row limit reached; narrow the query")
    if cache is not None:
        cache.put(ck, rows)
    return rows


class MissingKey(RuntimeError):
    def __init__(self, name: str):
        super().__init__(f"{name} not set (ml/.env)")


def _unit_of(short_desc: str) -> str:
    m = re.search(r"MEASURED IN (.+)$", short_desc)
    return m.group(1).strip() if m else ""


def parse_yields(rows: list[dict]) -> pd.DataFrame:
    """NASS ilçe verim satırları -> DataFrame; (D) vb. satırlar atılır."""
    out = []
    for r in rows:
        crop = crop_of_desc(r.get("short_desc", ""))
        v = parse_value(r.get("Value"))
        if crop is None or np.isnan(v):
            continue
        unit = _unit_of(r["short_desc"])
        out.append({"state": r.get("state_alpha"), "county": r.get("county_name"),
                    "county_ansi": r.get("county_code"), "year": int(r["year"]), "crop_key": crop,
                    "yield_value": v, "yield_unit": unit, "yield_t_ha": yield_to_t_ha(v, unit, crop)})
    cols = ["state", "county", "county_ansi", "year", "crop_key", "yield_value", "yield_unit", "yield_t_ha"]
    return pd.DataFrame(out, columns=cols).drop_duplicates(["state", "county_ansi", "year", "crop_key"])


def parse_prices(rows: list[dict]) -> pd.DataFrame:
    """NASS eyalet fiyat satırları -> DataFrame (yıllık/pazarlama yılı)."""
    out = []
    for r in rows:
        crop = crop_of_desc(r.get("short_desc", ""))
        v = parse_value(r.get("Value"))
        if crop is None or np.isnan(v):
            continue
        # aylık seriler atlanır; yıllık: MARKETING YEAR ya da YEAR
        if r.get("freq_desc", "ANNUAL") != "ANNUAL":
            continue
        unit = _unit_of(r["short_desc"])
        out.append({"state": r.get("state_alpha"), "year": int(r["year"]), "crop_key": crop,
                    "price": v, "price_unit": unit, "usd_per_t": price_usd_per_t(v, unit, crop),
                    "reference_period": r.get("reference_period_desc")})
    cols = ["state", "year", "crop_key", "price", "price_unit", "usd_per_t", "reference_period"]
    df = pd.DataFrame(out, columns=cols)
    # aynı yıl için MARKETING YEAR varsa onu tercih et
    df["_p"] = (df["reference_period"] != "MARKETING YEAR").astype(int)
    return (df.sort_values("_p").drop_duplicates(["state", "year", "crop_key"])
              .drop(columns="_p").sort_values(["state", "crop_key", "year"]).reset_index(drop=True))


def _year_params(years: Sequence[int]) -> list[tuple[str, str]]:
    return [("year__GE", str(min(years))), ("year__LE", str(max(years)))]


def county_yields(state_alpha: str, county_name: str | None = None, commodities: Sequence[str] | None = None,
                  years: Sequence[int] = (2015, 2025), county_ansi: str | None = None,
                  cache: JsonCache | None = None, key: str | None = None) -> pd.DataFrame:
    """SURVEY ilçe verimleri. county_ansi verilirse onunla, yoksa ilçe adıyla filtrelenir."""
    p = [("source_desc", "SURVEY"), ("statisticcat_desc", "YIELD"), ("agg_level_desc", "COUNTY"),
         ("state_alpha", state_alpha), *_year_params(years)]
    for c in (commodities or CROPS):
        p.append(("short_desc", CROPS[c]["yield"]))
    if county_ansi:
        p.append(("county_ansi", county_ansi))
    elif county_name:
        p.append(("county_name", county_name.upper()))
    df = parse_yields(_nass_get(p, cache, key))
    return df[df["year"].between(min(years), max(years))].reset_index(drop=True)  # API aralığı gevşek kalabiliyor


def state_prices(state_alpha: str, commodities: Sequence[str] | None = None,
                 years: Sequence[int] = (2015, 2025), cache: JsonCache | None = None,
                 key: str | None = None) -> pd.DataFrame:
    """Eyalet PRICE RECEIVED (yıllık). $/birim ve $/t."""
    p = [("source_desc", "SURVEY"), ("statisticcat_desc", "PRICE RECEIVED"), ("agg_level_desc", "STATE"),
         ("state_alpha", state_alpha), *_year_params(years)]
    for c in (commodities or CROPS):
        p.append(("short_desc", CROPS[c]["price"]))
    df = parse_prices(_nass_get(p, cache, key))
    return df[df["year"].between(min(years), max(years))].reset_index(drop=True)


# ---------------------------------------------------------------- OpenET
OPENET_COLS = ["source", "area_id", "lat", "lon", "time", "openet_et_month_mm"]


def _openet_empty() -> pd.DataFrame:
    return pd.DataFrame({c: [] for c in OPENET_COLS}).astype(
        {"lat": "float64", "lon": "float64", "time": "datetime64[ns]", "openet_et_month_mm": "float64"})


def parse_openet(resp: list[dict]) -> pd.DataFrame:
    """OpenET yanıtı [{time, et}] -> DataFrame(time, et_mm)."""
    df = pd.DataFrame({"time": pd.to_datetime([r["time"] for r in resp]).astype("datetime64[ns]"),
                       "et_mm": [float(r["et"]) if r.get("et") is not None else np.nan for r in resp]})
    return df


def openet_monthly(lat: float, lon: float, start, end, variable: str = "ET", model: str = "Ensemble",
                   key: str | None = None) -> tuple[pd.DataFrame, str | None]:
    """Aylık ET (mm). Dönüş: (DataFrame(time, et_mm), reason). Kapsam dışı/4xx -> (boş, sebep)."""
    key = key or load_key("OPENET_API_KEY")
    if not key:
        raise MissingKey("OPENET_API_KEY")
    body = {"date_range": [str(start)[:10], str(end)[:10]], "interval": "monthly", "geometry": [lon, lat],
            "model": model, "variable": variable, "reference_et": "gridMET", "units": "mm",
            "file_format": "JSON"}
    req = urllib.request.Request(OPENET_URL, data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": key, "Content-Type": "application/json",
                                          "User-Agent": USER_AGENT})
    try:
        resp = _request_json(req)
    except RuntimeError as e:
        if re.match(r"HTTP 4\d\d", str(e)):
            return pd.DataFrame({"time": pd.Series([], dtype="datetime64[ns]"),
                                 "et_mm": pd.Series([], dtype="float64")}), scrub(str(e))
        raise
    if not isinstance(resp, list) or not resp:
        return parse_openet([]), "empty response"
    return parse_openet(resp), None


def openet_raw(area: dict, start, end, key: str | None = None) -> tuple[pd.DataFrame, str | None]:
    """Bir alan için ham nokta tablosu (source=OPENET). Kapsam dışı -> boş + sebep."""
    lat, lon = float(area["lat"]), float(area["lon"])
    st = area.get("state_alpha")
    if st and st not in OPENET_STATES:
        return _openet_empty(), f"outside OpenET coverage ({st})"
    df, why = openet_monthly(lat, lon, start, end, key=key)
    if df.empty:
        return _openet_empty(), why
    out = pd.DataFrame({"source": "OPENET", "area_id": str(area["area_id"]), "lat": lat, "lon": lon,
                        "time": df["time"].dt.to_period("M").dt.to_timestamp().astype("datetime64[ns]"),
                        "openet_et_month_mm": df["et_mm"].astype("float64")})
    return out, None


# ---------------------------------------------------------------- CLI
def _parse_years(s: str) -> tuple[int, int]:
    a, _, b = s.partition("-")
    return int(a), int(b or a)


def _atomic_parquet(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)


def run(areas: list[dict], what: Sequence[str], start, end, years: tuple[int, int],
        static_out: Path, raw_out: Path) -> list[str]:
    """Seçili adımları çalıştırır; başarısız adım adlarını döner."""
    cache = JsonCache(static_out / "usda_http_cache.json")
    failed: list[str] = []
    rows = []
    # ilçe bilgisi tüm adımlar için gerekli
    try:
        for a in areas:
            c = county_of(float(a["lat"]), float(a["lon"]), cache)
            if c is None:
                log.warning("county not found: %s", a["area_id"])
                continue
            rows.append({"area_id": str(a["area_id"]), "lat": float(a["lat"]), "lon": float(a["lon"]), **c})
    except Exception as e:  # noqa: BLE001
        log.error("counties failed: %s", scrub(e))
        failed.append("counties")
    cdf = pd.DataFrame(rows)
    if "counties" in what and not cdf.empty:
        _atomic_parquet(cdf, static_out / "counties.parquet")
        print(f"counties: {len(cdf)} areas, {cdf[['state_fips', 'county_fips']].drop_duplicates().shape[0]} counties")
    yr = list(range(years[0], years[1] + 1))
    if "yields" in what and not cdf.empty:
        try:
            parts = []
            for (st, cf), g in cdf.groupby(["state_alpha", "county_fips"]):
                y = county_yields(st, years=yr, county_ansi=cf, cache=cache)
                for aid in g["area_id"]:
                    parts.append(y.assign(area_id=aid))
            ydf = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
            ydf = ydf.drop(columns=["county_ansi"], errors="ignore")
            _atomic_parquet(ydf, static_out / "nass_yields.parquet")
            print(f"yields: {len(ydf)} rows")
        except MissingKey as e:
            print(f"yields: SKIPPED ({e})")
        except Exception as e:  # noqa: BLE001
            log.error("yields failed: %s", scrub(e))
            failed.append("yields")
    if "prices" in what and not cdf.empty:
        try:
            pdf = pd.concat([state_prices(st, years=yr, cache=cache)
                             for st in sorted(cdf["state_alpha"].dropna().unique())], ignore_index=True)
            _atomic_parquet(pdf, static_out / "nass_prices.parquet")
            print(f"prices: {len(pdf)} rows")
        except MissingKey as e:
            print(f"prices: SKIPPED ({e})")
        except Exception as e:  # noqa: BLE001
            log.error("prices failed: %s", scrub(e))
            failed.append("prices")
    if "openet" in what:
        if not load_key("OPENET_API_KEY"):
            print("openet: SKIPPED (OPENET_API_KEY not set (ml/.env))")
        else:
            st_of = {r["area_id"]: r["state_alpha"] for r in rows}
            n_ok = n_skip = n_fail = 0
            for a in areas:
                a = {**a, "state_alpha": st_of.get(str(a["area_id"]))}
                try:
                    df, why = openet_raw(a, start, end)
                except Exception as e:  # noqa: BLE001
                    log.error("openet %s failed: %s", a["area_id"], scrub(e))
                    n_fail += 1
                    continue
                if df.empty:
                    log.info("openet %s skipped: %s", a["area_id"], why)
                    n_skip += 1
                else:
                    write_raw(df, raw_out)
                    n_ok += 1
            print(f"openet: ok={n_ok} skipped(no coverage)={n_skip} failed={n_fail}")
            if n_fail:
                failed.append("openet")
    return failed


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--areas", required=True)
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--years", default="2015-2025")
    ap.add_argument("--static-out", default="ml/cache/static")
    ap.add_argument("--raw-out", default="ml/cache/raw")
    ap.add_argument("--what", default="counties,yields,prices,openet")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    areas = json.loads(Path(a.areas).read_text())
    failed = run(areas, [w.strip() for w in a.what.split(",")], a.start, a.end, _parse_years(a.years),
                 Path(a.static_out), Path(a.raw_out))
    if failed:
        print("FAILED steps:", ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
