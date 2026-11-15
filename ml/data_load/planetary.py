"""MODIS NDVI (MOD13Q1 Terra + MYD13Q1 Aqua, v061) — Microsoft Planetary Computer'dan nokta okuma.

ORNL web servisi ve AppEEARS kesintideyken aynı ürünü buradan alırız: dosyalar bulutta "cloud-optimized
GeoTIFF"; tüm dosya inmez, yalnızca tarlanın pikselinin bulunduğu küçük blok okunur (HTTP range).
Giriş gerekmez (Planetary Computer'ın herkese açık SAS anahtarı otomatik alınır).

Değerler ORNL ile BİREBİR aynı (Iowa'da aynı tarihlerde doğrulandı): NDVI ham tamsayı (x0.0001),
pixel_reliability 0..3. Çıktı ORNL indiricisiyle aynı ham nokta tablosu; temizleyici fark görmez.
Terra (MOD13Q1) ve Aqua (MYD13Q1) 8 gün kaydırmalı olduğundan birlikte ~8 günde bir ölçüm verir.

    uv run python -m ml.data_load.planetary --areas ml/data_load/areas.us.json \\
        --start 2024-01-01 --end 2025-12-31 --out ml/cache/raw --workers 8
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import random
import socket
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import pandas as pd

from .download import BASE_COLS, write_raw

log = logging.getLogger("ml.data_load.planetary")

# Bazı ağlarda planetarycomputer.microsoft.com'a IPv6 yolu çalışmıyor (DNS önce IPv6 verir, bağlantı asılı kalır;
# IPv4 anında yanıt verir). Yalnızca bu sunucular için IPv4'ü öne al; diğer bağlantılara dokunma.
_IPV4_HOSTS = ("planetarycomputer.microsoft.com",)
_orig_getaddrinfo = socket.getaddrinfo


def _getaddrinfo_v4_first(host, *args, **kwargs):
    res = _orig_getaddrinfo(host, *args, **kwargs)
    if isinstance(host, str) and host.endswith(_IPV4_HOSTS):
        v4 = [r for r in res if r[0] == socket.AF_INET]
        return v4 or res
    return res


socket.getaddrinfo = _getaddrinfo_v4_first

STAC_SEARCH = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
SAS_TOKEN = "https://planetarycomputer.microsoft.com/api/sas/v1/token/{account}/{container}"
COLLECTION = "modis-13Q1-061"
BANDS = ("250m_16_days_NDVI", "250m_16_days_pixel_reliability")
UA = {"User-Agent": "field-shift-research/0.1"}
R_SINU = 6371007.181  # MODIS sinüzoidal küre yarıçapı (m)
SEARCH_PAD_DEG = 0.1  # arama kutusu yarı genişliği (derece)

# GDAL: yalnızca gereken blokları oku, klasör listeleme yapma (bulut COG için standart ayarlar)
GDAL_ENV = {"GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR", "GDAL_HTTP_MULTIRANGE": "YES",
            "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES", "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
            "GDAL_HTTP_MAX_RETRY": "4", "GDAL_HTTP_RETRY_DELAY": "2", "CPL_LOG": os.devnull,
            # zaman aşımı ŞART: yoksa yanıt vermeyen bir istek iş parçacığını sonsuza kadar kilitler
            "GDAL_HTTP_TIMEOUT": "30", "GDAL_HTTP_CONNECTTIMEOUT": "10"}
SCENE_TIMEOUT_S = 180  # bir sahne (2 bant) bundan uzun sürerse atlanır; tekrar çalıştırınca yeniden denenir


def _json(url: str, data: dict | None = None, timeout: float = 60, retries: int = 4) -> dict:
    body = json.dumps(data).encode() if data is not None else None
    headers = {**UA, **({"Content-Type": "application/json"} if body else {})}
    last = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=headers), timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500 and e.code != 429:
                raise RuntimeError(f"HTTP {e.code}: {e.read()[:200]!r}") from e
            last = e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
        if attempt < retries:
            time.sleep(2 ** attempt + random.uniform(0, 1))
    raise RuntimeError(f"giving up on {url}: {last}")


def sinusoidal(lat: float, lon: float) -> tuple[float, float]:
    """WGS84 enlem/boylam -> MODIS sinüzoidal x, y (metre)."""
    phi, lam = math.radians(lat), math.radians(lon)
    return R_SINU * lam * math.cos(phi), R_SINU * phi


def search(lat: float, lon: float, start, end) -> list[dict]:
    """Noktayı kapsayan tüm MOD13Q1/MYD13Q1 sahneleri (sayfalı)."""
    # Nokta değil küçük kutu: katalogdaki karo sınırları sadeleştirilmiş; karo kenarındaki tarlada nokta araması
    # doğru karonun sahnelerini kaçırıyordu (örn. -96.0 boylamı h10/h11 sınırı). Doğru karo read_pixel'de seçilir.
    d = SEARCH_PAD_DEG
    body = {"collections": [COLLECTION], "bbox": [lon - d, lat - d, lon + d, lat + d],
            "datetime": f"{start}/{end}", "limit": 250}
    out, url, data = [], STAC_SEARCH, body
    for _ in range(50):
        page = _json(url, data)
        out += page.get("features", [])
        nxt = next((ln for ln in page.get("links", []) if ln.get("rel") == "next"), None)
        if not nxt:
            break
        url, data = nxt["href"], nxt.get("body") or None
        if nxt.get("method", "GET").upper() == "POST" and data is None:
            data = body
    return out


class TokenCache:
    """Planetary Computer SAS anahtarı (depo/klasör başına); süresi dolmadan yenilenir."""

    def __init__(self):
        self._tok: dict[tuple[str, str], tuple[str, float]] = {}
        self._lock = threading.Lock()

    def sign(self, href: str) -> str:
        # https://<account>.blob.core.windows.net/<container>/...
        host, path = href.split("://", 1)[1].split("/", 1)
        key = (host.split(".")[0], path.split("/", 1)[0])
        with self._lock:
            tok, exp = self._tok.get(key, ("", 0.0))
            if time.time() > exp - 300:
                d = _json(SAS_TOKEN.format(account=key[0], container=key[1]))
                tok = d["token"]
                try:
                    exp = pd.Timestamp(d.get("msft:expiry")).timestamp()
                except Exception:  # noqa: BLE001
                    exp = time.time() + 1800
                self._tok[key] = (tok, exp)
        return f"{href}?{tok}"


def read_pixel(href: str, x: float, y: float) -> float | None:
    """COG'dan tek piksel; nokta dosyanın dışındaysa None."""
    import rasterio  # noqa: PLC0415 (ağır içe aktarma)

    with rasterio.Env(**GDAL_ENV), rasterio.open(href) as ds:
        b = ds.bounds
        if not (b.left <= x <= b.right and b.bottom <= y <= b.top):
            return None
        return float(next(ds.sample([(x, y)]))[0])


def fetch_area(area_id: str, lat: float, lon: float, start, end, tokens: TokenCache | None = None,
               workers: int = 8, skip_dates: set | None = None) -> pd.DataFrame:
    """Bir tarla için Terra+Aqua NDVI + kalite. Döner: ham nokta tablosu (source = MOD13Q1 / MYD13Q1)."""
    tokens = tokens or TokenCache()
    items = search(lat, lon, start, end)
    x, y = sinusoidal(lat, lon)
    skip_dates = skip_dates or set()

    def one(it: dict) -> dict | None:
        src = it["id"].split(".")[0]  # MOD13Q1 / MYD13Q1
        t = pd.Timestamp(it["properties"].get("start_datetime") or it["properties"]["datetime"]).tz_localize(None)
        if (src, t.normalize()) in skip_dates:
            return None
        row = {"source": src, "area_id": area_id, "lat": float(lat), "lon": float(lon), "time": t.normalize()}
        for band in BANDS:
            v = read_pixel(tokens.sign(it["assets"][band]["href"]), x, y)
            if v is None:
                return None  # nokta bu karonun dışında (komşu karoda okunur)
            row[band] = v
        return row

    rows = []
    ex = ThreadPoolExecutor(max_workers=workers)
    futs = [ex.submit(one, it) for it in items]
    try:
        for fut in as_completed(futs, timeout=SCENE_TIMEOUT_S * max(1, len(items) / workers)):
            try:
                r = fut.result()
            except Exception as e:  # noqa: BLE001  (bir sahne düşerse diğerleri korunur)
                log.warning("%s sahne okunamadı: %s", area_id, str(e)[:150])
                continue
            if r:
                rows.append(r)
    except TimeoutError:
        log.warning("%s: %d sahne zamanında bitmedi, atlandı (tekrar çalıştırınca denenir)", area_id,
                    sum(not f.done() for f in futs))
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    if not rows:
        return pd.DataFrame(columns=[*BASE_COLS, *BANDS])
    df = pd.DataFrame(rows).sort_values(["source", "time"]).drop_duplicates(["source", "area_id", "time"])
    df["time"] = df["time"].astype("datetime64[ns]")
    for b in BANDS:
        df[b] = df[b].astype("float64")
    return df[[*BASE_COLS, *BANDS]].reset_index(drop=True)


def existing_dates(out_dir: Path) -> dict[str, set]:
    """Önceden inmiş (kaynak, tarih) çiftleri tarla başına (devam edebilmek için)."""
    have: dict[str, set] = {}
    for src in ("MOD13Q1", "MYD13Q1"):
        p = out_dir / f"{src}.parquet"
        if p.exists():
            d = pd.read_parquet(p, columns=["area_id", "time", "250m_16_days_NDVI"])
            d = d[d["250m_16_days_NDVI"].notna()]
            for aid, g in d.groupby("area_id"):
                have.setdefault(str(aid), set()).update((src, t) for t in pd.to_datetime(g["time"]).dt.normalize())
    return have


def run(areas: list[dict], start, end, out_dir, workers: int = 8, area_workers: int = 2) -> list[dict]:
    """Tüm tarlalar; her tarla bitince diske yazılır (yarıda kalırsa kaldığı yerden devam)."""
    out = Path(out_dir)
    have = existing_dates(out)
    tokens = TokenCache()
    lock = threading.Lock()
    fails: list[dict] = []

    def task(a: dict) -> None:
        aid = a["area_id"]
        t0 = time.time()
        try:
            df = fetch_area(aid, a["lat"], a["lon"], start, end, tokens, workers, have.get(aid))
            if not df.empty:
                with lock:
                    for src, g in df.groupby("source"):
                        write_raw(g, out)
            n_old = len(have.get(aid, ()))
            log.info("ndvi %s: %d yeni sahne (+%d önceden), %.0f sn", aid, len(df), n_old, time.time() - t0)
        except Exception as e:  # noqa: BLE001
            log.error("FAILED ndvi %s: %s", aid, str(e)[:200])
            fails.append({"area_id": aid, "error": str(e)[:200]})

    with ThreadPoolExecutor(max_workers=area_workers) as ex:
        list(ex.map(task, areas))
    return fails


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("rasterio").setLevel(logging.ERROR)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--areas", required=True)
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--out", default="ml/cache/raw")
    ap.add_argument("--workers", type=int, default=8, help="tarla başına paralel sahne okuma")
    ap.add_argument("--area-workers", type=int, default=2, help="aynı anda kaç tarla")
    ap.add_argument("--max", type=int, help="yalnızca ilk N tarla (deneme)")
    a = ap.parse_args(argv)
    areas = json.loads(Path(a.areas).read_text())[: a.max]
    t0 = time.time()
    fails = run(areas, date.fromisoformat(a.start), date.fromisoformat(a.end), a.out, a.workers, a.area_workers)
    print(f"done; {len(areas)} tarla, {(time.time() - t0) / 60:.1f} dk; failures: {len(fails)}")
    for f in fails:
        print(f"  {f['area_id']}: {f['error']}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
