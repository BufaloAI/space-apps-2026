"""Küçük alan (nokta/bbox) için ham NASA verisi indirici.

Her kaynak fonksiyonu "ham nokta tablosu" döner:
source, area_id, lat, lon, time + ürünün kendi değişken sütunları.
Değerler HAM kalır (ölçek faktörü ve fill değeri temizleyiciye aittir).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import threading
import time as _time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

log = logging.getLogger("ml.data_load.download")

USER_AGENT = "field-shift-research/0.1"
TIMEOUT_S = 60
MAX_RETRIES = 4
MIN_INTERVAL_S = 0.5  # host başına en fazla 2 istek/sn
BASE_COLS = ["source", "area_id", "lat", "lon", "time"]
KEY_COLS = ["source", "area_id", "lat", "lon", "time"]

POWER_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
POWER_PARAMS = ["T2M", "T2M_MAX", "T2M_MIN", "PRECTOTCORR", "RH2M",
                "ALLSKY_SFC_SW_DWN", "WS2M", "GWETTOP", "GWETROOT", "EVPTRNS"]

ORNL_URL = "https://modis.ornl.gov/rst/api/v1"
ORNL_BANDS = {
    # EVI, NDVI ile çok yüksek korelasyonlu (budamada zaten düşer); servis yavaş olduğu için varsayılanda yok
    "MOD13Q1": ["250m_16_days_NDVI", "250m_16_days_pixel_reliability"],
    "MOD11A1": ["LST_Day_1km", "LST_Night_1km", "QC_Day"],
}
ORNL_MAX_DATES = 10  # servis istek başına ~10 tarih kabul eder

_rate_lock = threading.Lock()
_last_call: dict[str, float] = {}


# ---------------------------------------------------------------- HTTP
def _throttle(host: str) -> None:
    """Host başına istekler arası minimum aralığı uygular."""
    with _rate_lock:
        wait = _last_call.get(host, 0.0) + MIN_INTERVAL_S - _time.monotonic()
        if wait > 0:
            _time.sleep(wait)
        _last_call[host] = _time.monotonic()


def _fetch_json(url: str) -> dict:
    """URL'den JSON okur: timeout, üstel geri çekilme + jitter ile 4 yeniden deneme."""
    host = urllib.parse.urlparse(url).netloc
    last: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        _throttle(host)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:300]
            # 4xx (429 hariç) tekrar denemeye değmez; POWER parametre hatası 422 döner
            if 400 <= e.code < 500 and e.code != 429:
                raise RuntimeError(f"HTTP {e.code}: {body}") from e
            last = e
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as e:
            last = e
        if attempt < MAX_RETRIES:
            delay = 2 ** attempt + random.uniform(0, 1)
            log.warning("retry %d/%d in %.1fs: %s", attempt + 1, MAX_RETRIES, delay, last)
            _time.sleep(delay)
    raise RuntimeError(f"giving up on {url}: {last}")


def _empty(extra: Sequence[str]) -> pd.DataFrame:
    return pd.DataFrame({c: [] for c in [*BASE_COLS, *extra]}).astype(
        {"lat": "float64", "lon": "float64", "time": "datetime64[ns]"})


def _d(x: str | date) -> date:
    return x if isinstance(x, date) else date.fromisoformat(str(x)[:10])


# ---------------------------------------------------------------- POWER
def fetch_power(area_id: str, lat: float, lon: float, start, end,
                params: Sequence[str] = POWER_PARAMS) -> pd.DataFrame:
    """NASA POWER günlük nokta verisi. API'nin reddettiği parametreler düşürülür."""
    s, e = _d(start), _d(end)
    params = list(params)
    data = None
    for _ in range(len(params) + 1):
        q = urllib.parse.urlencode({
            "parameters": ",".join(params), "community": "AG",
            "longitude": lon, "latitude": lat,
            "start": s.strftime("%Y%m%d"), "end": e.strftime("%Y%m%d"), "format": "JSON"},
            safe=",")
        try:
            data = _fetch_json(f"{POWER_URL}?{q}")
            break
        except RuntimeError as err:
            # hata mesajı: "One of your parameters is incorrect: FOO."
            bad = [p for p in params if f" {p}" in str(err) or f"{p}." in str(err)]
            if not bad or len(bad) == len(params):
                raise
            log.warning("POWER rejected %s, dropping", bad)
            params = [p for p in params if p not in bad]
    msgs = " ".join(map(str, data.get("messages", [])))
    block = data.get("properties", {}).get("parameter", {})
    if not block:
        raise RuntimeError(f"POWER empty response: {msgs}")
    # mesajda reddedilen parametre varsa zaten block'ta yoktur; olanı alırız
    df = pd.DataFrame(block)  # index = YYYYMMDD
    df.index = pd.to_datetime(df.index, format="%Y%m%d")
    df = df.rename_axis("time").reset_index()
    df["time"] = df["time"].astype("datetime64[ns]")
    df.insert(0, "source", "POWER")
    df.insert(1, "area_id", area_id)
    df.insert(2, "lat", float(lat))
    df.insert(3, "lon", float(lon))
    for c in df.columns[5:]:
        df[c] = df[c].astype("float64")
    return df


# ---------------------------------------------------------------- ORNL MODIS
def _chunks(seq: list, n: int):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def fetch_modis_ornl(area_id: str, lat: float, lon: float, start, end,
                     product: str = "MOD13Q1", bands: Sequence[str] | None = None) -> pd.DataFrame:
    """ORNL DAAC MODIS web servisi, tek piksel (km=0).

    Birden fazla piksel dönerse MERKEZ piksel alınır (data[len//2]).
    `time` = calendar_date (16 günlük kompozitte kompozit başlangıcı).
    """
    bands = list(bands or ORNL_BANDS[product])
    s, e = _d(start), _d(end)
    base = f"{ORNL_URL}/{product}"
    dq = urllib.parse.urlencode({"latitude": lat, "longitude": lon})
    dates = _fetch_json(f"{base}/dates?{dq}")["dates"]
    mdates = [d["modis_date"] for d in dates if s <= _d(d["calendar_date"]) <= e]
    if not mdates:
        return _empty(bands)
    per_band: dict[str, dict[pd.Timestamp, float]] = {b: {} for b in bands}
    for b in bands:
        for ch in _chunks(mdates, ORNL_MAX_DATES):
            q = urllib.parse.urlencode({
                "latitude": lat, "longitude": lon, "band": b,
                "startDate": ch[0], "endDate": ch[-1],
                "kmAboveBelow": 0, "kmLeftRight": 0})
            try:
                resp = _fetch_json(f"{base}/subset?{q}")
            except Exception as ex:  # noqa: BLE001  (bir parça düşerse diğerleri korunur; tekrar çalıştırmak güvenli)
                log.warning("%s %s %s %s..%s atlandı: %s", product, area_id, b, ch[0], ch[-1], ex)
                continue
            for row in resp.get("subset", []):
                vals = row.get("data") or []
                if not vals:
                    continue
                per_band[b][pd.Timestamp(row["calendar_date"])] = float(vals[len(vals) // 2])
    times = sorted({t for d in per_band.values() for t in d})
    out = pd.DataFrame({"time": pd.DatetimeIndex(times).astype("datetime64[ns]")})
    out.insert(0, "source", product)
    out.insert(1, "area_id", area_id)
    out.insert(2, "lat", float(lat))
    out.insert(3, "lon", float(lon))
    for b in bands:
        out[b] = [per_band[b].get(t, float("nan")) for t in times]
        out[b] = out[b].astype("float64")
    return out[[*BASE_COLS, *bands]]


# ---------------------------------------------------------------- earthaccess
def select_granules_by_hour(items: Sequence[tuple[datetime, Any]], start, end,
                            hours: Sequence[int] = (0, 6, 12, 18),
                            max_gap_h: float = 3.0) -> list[tuple[datetime, Any]]:
    """Her gün/saat hedefi için zamanı en yakın granülü seçer.

    items: (granül merkez zamanı, nesne). SMAP merkezleri 01:30, 04:30, ... olduğundan
    0/6/12/18 hedefleri 01:30/07:30/13:30/19:30'a düşer.
    """
    out, seen = [], set()
    d, last = _d(start), _d(end)
    while d <= last:
        for h in hours:
            target = datetime(d.year, d.month, d.day, h)
            cand = min(items, key=lambda it: (abs(it[0] - target), -it[0].timestamp()), default=None)  # eşitlikte sonraki
            if cand and abs(cand[0] - target) <= timedelta(hours=max_gap_h) and id(cand[1]) not in seen:
                seen.add(id(cand[1]))
                out.append(cand)
        d += timedelta(days=1)
    return sorted(out, key=lambda it: it[0])


def _granule_center(g) -> datetime:
    rng = g["umm"]["TemporalExtent"]["RangeDateTime"]
    b = pd.Timestamp(rng["BeginningDateTime"]).tz_localize(None) if pd.Timestamp(rng["BeginningDateTime"]).tzinfo is None \
        else pd.Timestamp(rng["BeginningDateTime"]).tz_convert("UTC").tz_localize(None)
    en = pd.Timestamp(rng.get("EndingDateTime", rng["BeginningDateTime"]))
    en = en.tz_localize(None) if en.tzinfo is None else en.tz_convert("UTC").tz_localize(None)
    return (b + (en - b) / 2).to_pydatetime()


def _earthaccess_login():
    import earthaccess
    for strat in ("environment", "netrc"):
        try:
            auth = earthaccess.login(strategy=strat)
            if getattr(auth, "authenticated", False):
                return earthaccess
        except Exception as ex:  # noqa: BLE001
            log.debug("earthaccess login %s failed: %s", strat, ex)
    raise RuntimeError(
        "Earthdata login failed: set EARTHDATA_USERNAME/EARTHDATA_PASSWORD or ~/.netrc")


def _cftime_to_ts(t) -> pd.Timestamp:
    """cftime (julian dahil) veya datetime64 -> pandas Timestamp."""
    if hasattr(t, "year") and not isinstance(t, (pd.Timestamp, datetime)):
        return pd.Timestamp(year=t.year, month=t.month, day=t.day, hour=t.hour, minute=t.minute)
    return pd.Timestamp(t)


def _read_smap(fobj, bbox, variables) -> pd.DataFrame:
    import h5py
    import numpy as np
    w, s, e, n = bbox
    with h5py.File(fobj, "r") as f:
        la, lo = f["cell_lat"][:], f["cell_lon"][:]
        m = (la >= s) & (la <= n) & (lo >= w) & (lo <= e)
        if not m.any():
            return pd.DataFrame()
        d = {"lat": la[m].astype("float64"), "lon": lo[m].astype("float64")}
        for v in variables:
            d[v] = np.asarray(f["Geophysical_Data"][v][:][m], dtype="float64")
    return pd.DataFrame(d)


def _read_gpm(fobj, bbox, variables) -> pd.DataFrame:
    import xarray as xr
    w, s, e, n = bbox
    ds = xr.open_dataset(fobj, engine="h5netcdf", decode_times=False)
    try:
        ds = xr.decode_cf(ds, use_cftime=True)
    except Exception:  # noqa: BLE001
        pass
    sub = ds[list(variables)].sel(lon=slice(w, e), lat=slice(s, n))
    df = sub.to_dataframe().reset_index()
    df["time"] = [_cftime_to_ts(t) for t in df["time"]]
    df["lat"] = df["lat"].astype("float64")
    df["lon"] = df["lon"].astype("float64")
    ds.close()
    return df


def fetch_earthaccess(area_id: str, bbox: Sequence[float], start, end, short_name: str,
                      variables: Sequence[str], hours: Sequence[int] = (0, 6, 12, 18)) -> pd.DataFrame:
    """SPL4SMGP / GPM_3IMERGDF granüllerini bbox'a kırparak okur (tam dosya saklanmaz)."""
    ea = _earthaccess_login()
    s, e = _d(start), _d(end)
    results = ea.search_data(short_name=short_name,
                             temporal=(s.isoformat(), e.isoformat()),
                             bounding_box=tuple(bbox))
    sub_daily = short_name.startswith("SPL4")
    items = [(_granule_center(g), g) for g in results]
    if sub_daily:
        items = select_granules_by_hour(items, s, e, hours)
    reader = _read_smap if sub_daily else _read_gpm
    frames = []
    for ts, g in items:
        try:
            files = ea.open([g])
            df = reader(files[0], bbox, variables)
        except Exception as ex:  # noqa: BLE001
            log.warning("%s granule %s failed: %s", short_name, ts, ex)
            continue
        if df.empty:
            continue
        if not sub_daily:  # günlük ürün: zamanı dosyadan gelir
            pass
        else:
            df["time"] = pd.Timestamp(ts).round("min")
        frames.append(df)
    if not frames:
        return _empty(variables)
    out = pd.concat(frames, ignore_index=True)
    out["time"] = out["time"].astype("datetime64[ns]")
    out.insert(0, "source", short_name)
    out.insert(1, "area_id", area_id)
    return out[[*BASE_COLS, *variables]]


# ---------------------------------------------------------------- yazma
def write_raw(df: pd.DataFrame, out_dir: str | Path) -> Path:
    """out_dir/<source>.parquet: mevcut dosyayla birleştir, tekrarları at, atomik yaz."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    source = str(df["source"].iloc[0])
    path = out_dir / f"{source}.parquet"
    if path.exists():
        df = pd.concat([pd.read_parquet(path), df], ignore_index=True)
    df = (df.drop_duplicates(subset=KEY_COLS, keep="last")
            .sort_values(["area_id", "time", "lat", "lon"]).reset_index(drop=True))
    tmp = path.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)
    return path


# ---------------------------------------------------------------- CLI
_write_lock = threading.Lock()


def run(areas: list[dict], start, end, out_dir, sources: Sequence[str], workers: int = 3) -> list[dict]:
    """Seçili kaynakları tüm alanlar için indirir (alan/kaynak başına bir görev); hataları döner."""
    from concurrent.futures import ThreadPoolExecutor
    ea_specs = {
        "smap": ("SPL4SMGP", ["sm_surface", "sm_rootzone"]),
        "gpm": ("GPM_3IMERGDF", ["precipitation"]),
    }

    def task(a: dict, src: str) -> dict | None:
        aid, lat, lon, bbox = a["area_id"], a["lat"], a["lon"], a.get("bbox")
        try:
            if src == "power":
                df = fetch_power(aid, lat, lon, start, end)
            elif src == "modis":
                df = fetch_modis_ornl(aid, lat, lon, start, end, "MOD13Q1")
            elif src == "modis_lst":
                df = fetch_modis_ornl(aid, lat, lon, start, end, "MOD11A1")
            elif src in ea_specs:
                if not bbox:
                    raise ValueError("bbox required")
                sn, vs = ea_specs[src]
                df = fetch_earthaccess(aid, bbox, start, end, sn, vs)
            else:
                raise ValueError(f"unknown source {src}")
            if df.empty:
                raise RuntimeError("no rows returned")
            with _write_lock:
                write_raw(df, out_dir)
            log.info("%s %s: %d rows", src, aid, len(df))
            return None
        except Exception as ex:  # noqa: BLE001
            log.error("FAILED %s %s: %s", src, aid, ex)
            return {"area_id": aid, "source": src, "error": str(ex)}

    with ThreadPoolExecutor(max_workers=workers) as ex:
        res = list(ex.map(lambda t: task(*t), [(a, s) for a in areas for s in sources]))
    return [r for r in res if r]


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--areas", required=True)
    p.add_argument("--start", default="2024-01-01")
    p.add_argument("--end", default="2025-12-31")
    p.add_argument("--out", default="ml/cache/raw")
    p.add_argument("--sources", default="power,modis",
                   help="power,modis,modis_lst,smap,gpm")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    areas = json.loads(Path(a.areas).read_text())
    fails = run(areas, a.start, a.end, a.out, [s.strip() for s in a.sources.split(",")])
    print(f"done; failures: {len(fails)}")
    for f in fails:
        print(f"  {f['source']} {f['area_id']}: {f['error']}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
