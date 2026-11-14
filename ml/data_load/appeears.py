"""NASA AppEEARS nokta çıkarımı istemcisi + CLI.

Ürün başına tek görev (tüm noktalar birlikte) gönderilir; sunucuda paralel çalışır.
Sonuç CSV'leri "ham nokta tablosu"na çevrilir (source, area_id, lat, lon, time + değişkenler)
ve `download.write_raw` ile yazılır. Değerler kayıt (registry) için HAM MODIS tamsayısına
normalize edilir (NDVI x0.0001, LST x0.02); ET gerçek birimde (mm/8 gün) verilir.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import math
import netrc
import os
import random
import re
import time as _time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

from ml.data_load.download import BASE_COLS, write_raw

log = logging.getLogger("ml.data_load.appeears")

API = "https://appeears.earthdatacloud.nasa.gov/api"
USER_AGENT = "field-shift-research/0.1"
LOGIN_TIMEOUT_S = 120
TIMEOUT_S = 90
MAX_RETRIES = 4
POLL_S = 30
DEFAULT_STATIC = "ml/cache/static"

# Kolay düzenlenen ürün ön ayarları: ad -> ürün, katmanlar, kaynak kısa adı.
PRESETS: dict[str, dict[str, Any]] = {
    "ndvi": {"product": "MOD13Q1.061", "source": "MOD13Q1",
             "layers": ["_250m_16_days_NDVI", "_250m_16_days_pixel_reliability"]},
    "ndvi_aqua": {"product": "MYD13Q1.061", "source": "MYD13Q1",
                  "layers": ["_250m_16_days_NDVI", "_250m_16_days_pixel_reliability"]},
    "lst": {"product": "MOD11A2.061", "source": "MOD11A2",
            "layers": ["LST_Day_1km", "QC_Day"]},
    "et": {"product": "MOD16A2GF.061", "source": "MOD16A2GF",
           "layers": ["ET_500m", "ET_QC_500m"]},
    "smap": {"product": "SPL4SMGP.008", "source": "SPL4SMGP",
             "layers": ["Geophysical_Data_sm_surface", "Geophysical_Data_sm_rootzone"]},
    "elev": {"product": "SRTMGL1_NC.003", "source": "SRTMGL1_NC",
             "layers": ["SRTMGL1_DEM"]},
    "hls": {"product": "HLSL30_VI.020", "source": "HLSL30_VI",
            "layers": ["NDVI"],  # VI ürünlerinde Fmask katmanı yok
            "extra": [{"product": "HLSS30_VI.020", "layers": ["NDVI"]}]},
}

NEIGHBOR_DEG = 0.0005
NEIGHBOR_SUFFIX = {"n": (1, 0), "s": (-1, 0), "e": (0, 1), "w": (0, -1)}  # (dlat, dlon) işareti


# ---------------------------------------------------------------- HTTP
def _credentials() -> tuple[str, str]:
    u, p = os.environ.get("EARTHDATA_USERNAME"), os.environ.get("EARTHDATA_PASSWORD")
    if u and p:
        return u, p
    auth = netrc.netrc().authenticators("urs.earthdata.nasa.gov")
    if not auth or not auth[0] or not auth[2]:
        raise RuntimeError("Earthdata credentials missing: set EARTHDATA_USERNAME/PASSWORD or ~/.netrc")
    return auth[0], auth[2]


def _request(method: str, path: str, token: str | None = None, body: Any = None,
             timeout: float = TIMEOUT_S, retries: int = MAX_RETRIES, raw: bool = False,
             headers: dict | None = None, data: bytes | None = None):
    """JSON (veya raw=True ise bytes) döndürür; 5xx/ağ hatalarında üstel geri çekilmeyle tekrar dener."""
    url = path if path.startswith("http") else f"{API}/{path.lstrip('/')}"
    h = {"User-Agent": USER_AGENT, **(headers or {})}
    if token:
        h["Authorization"] = f"Bearer {token}"
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=h, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                content = r.read()
            if raw:
                return content
            return json.loads(content.decode("utf-8")) if content.strip() else {}
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "replace")[:300]
            if 400 <= e.code < 500 and e.code != 429:
                raise RuntimeError(f"HTTP {e.code} {method} {path}: {msg}") from e
            last = e
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as e:
            last = e
        if attempt < retries:
            delay = 2 ** attempt + random.uniform(0, 1)
            log.warning("retry %d/%d in %.1fs (%s): %s", attempt + 1, retries, delay, path, last)
            _time.sleep(delay)
    raise RuntimeError(f"giving up on {method} {path}: {last}")


def login() -> str:
    """HTTP Basic ile bearer token alır (giriş yavaş olabilir: 120 sn, tekrar)."""
    u, p = _credentials()
    basic = base64.b64encode(f"{u}:{p}".encode()).decode()
    resp = _request("POST", "login", timeout=LOGIN_TIMEOUT_S, retries=3,
                    headers={"Authorization": f"Basic {basic}"}, data=b"")
    return resp["token"]


def logout(token: str) -> None:
    try:
        _request("POST", "logout", token, timeout=30, retries=0, raw=True)
    except Exception as ex:  # noqa: BLE001
        log.debug("logout failed: %s", ex)


# ---------------------------------------------------------------- API
def list_layers(token: str | None, product: str) -> dict:
    """Ürün katmanları (uç nokta herkese açık; token gerekmez)."""
    return _request("GET", f"product/{product}", token)


def build_task_body(name: str, start, end, layers: Sequence[dict], points: Sequence[dict]) -> dict:
    s, e = date.fromisoformat(str(start)[:10]), date.fromisoformat(str(end)[:10])
    return {
        "task_type": "point", "task_name": name,
        "params": {
            "dates": [{"startDate": s.strftime("%m-%d-%Y"), "endDate": e.strftime("%m-%d-%Y")}],
            "layers": list(layers),
            "coordinates": [{"id": p["id"], "latitude": float(p["lat"]), "longitude": float(p["lon"]),
                             "category": "field"} for p in points],
        },
    }


def submit_task(token: str, body: dict) -> str:
    return _request("POST", "task", token, body)["task_id"]


def task_status(token: str, task_id: str) -> dict:
    return _request("GET", f"task/{task_id}", token)


def bundle_files(token: str, task_id: str) -> list[dict]:
    return _request("GET", f"bundle/{task_id}", token).get("files", [])


def download_file(token: str, task_id: str, file_id: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    # /bundle/... S3'e yönlendirir; urllib Authorization'ı yönlendirmede düşürür (doğru davranış)
    content = _request("GET", f"bundle/{task_id}/{file_id}", token, timeout=300, raw=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_bytes(content)
    os.replace(tmp, dest)
    return dest


# ---------------------------------------------------------------- noktalar
def points_from_areas(areas: list[dict], with_neighbors: bool = False) -> list[dict]:
    pts = []
    for a in areas:
        pts.append({"id": str(a["area_id"]), "lat": float(a["lat"]), "lon": float(a["lon"])})
        if with_neighbors:
            for suf, (dy, dx) in NEIGHBOR_SUFFIX.items():
                pts.append({"id": f"{a['area_id']}__{suf}", "lat": float(a["lat"]) + dy * NEIGHBOR_DEG,
                            "lon": float(a["lon"]) + dx * NEIGHBOR_DEG})
    return pts


def task_key(product: str, layers: Sequence[str], points: Sequence[dict], start, end) -> str:
    h = hashlib.sha1(json.dumps([[p["id"], round(p["lat"], 6), round(p["lon"], 6)] for p in points],
                                sort_keys=True).encode()).hexdigest()[:12]
    return f"{product}|{','.join(layers)}|{h}|{str(start)[:10]}|{str(end)[:10]}"


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True))
    os.replace(tmp, path)


# ---------------------------------------------------------------- ayrıştırma
_LAYER_PREFIXES = ("Geophysical_Data_",)  # SMAP katmanlarında grup öneki


def _strip_prefix(col: str, product: str) -> str:
    """'MOD13Q1_061__250m_16_days_NDVI' -> '250m_16_days_NDVI'."""
    pre = re.sub(r"[^0-9A-Za-z]+", "_", product) + "_"
    c = col[len(pre):] if col.startswith(pre) else col
    c = c.lstrip("_")
    for g in _LAYER_PREFIXES:
        if c.startswith(g):
            c = c[len(g):]
    return c


def _value_columns(columns: Sequence[str], product: str, skip: set[str]) -> dict[str, str]:
    """Ham sütun -> sadeleşmiş ad. Çözülmüş QA bit sütunları ve tile/line/sample atılır.

    Çözülmüş sütunlar başka bir katman adının '<katman>_...' uzantısıdır (ör. *_MODLAND_Description).
    """
    names = {c: _strip_prefix(c, product) for c in columns if c not in skip}
    keep = {}
    for c, n in names.items():
        low = n.lower()
        if re.match(r"^(line_y|sample_x)", low) or "tile" in low or low.endswith("_description"):
            continue
        if any(o != n and n.startswith(o + "_") for o in names.values()):
            continue
        keep[c] = n
    return keep


def _median_abs(s: pd.Series) -> float:
    v = s.dropna().to_numpy(dtype="float64")
    return float(np.median(np.abs(v))) if v.size else float("nan")


def _ndvi_like(name: str) -> bool:
    return name.lower().endswith("ndvi")


def parse_point_csv(path: str | Path, product: str, source: str | None = None) -> pd.DataFrame:
    """AppEEARS '*-results.csv' -> ham nokta tablosu (kayıt-ham ölçek)."""
    raw = pd.read_csv(path)
    src = source or re.split(r"[._]", product)[0]
    cols = {c.lower(): c for c in raw.columns}
    need = ["id", "latitude", "longitude", "date"]
    if any(n not in cols for n in need):
        raise ValueError(f"unexpected CSV columns: {list(raw.columns)[:8]}")
    out = pd.DataFrame({
        "source": src,
        "area_id": raw[cols["id"]].astype(str),
        "lat": raw[cols["latitude"]].astype("float64"),
        "lon": raw[cols["longitude"]].astype("float64"),
        "time": pd.to_datetime(raw[cols["date"]], utc=True, errors="coerce").dt.tz_localize(None).astype("datetime64[ns]"),
    })
    meta = {cols[n] for n in need} | {cols.get("category", "")}
    for c, n in _value_columns(list(raw.columns), product, meta).items():
        if pd.api.types.is_numeric_dtype(raw[c]):
            out[n] = raw[c].astype("float64").to_numpy()
    return _normalize_scaling(out)


_FILL_MARKS = (-3000.0, -0.3, -19999.0, -1.9999, -28672.0, -9999.0, 32767.0, 3276.7, 0.0)
ET_COL = "ET_500m"
ET_OUT = "modis_et_8day_mm"


def _median_valid(s: pd.Series) -> float:
    v = s.dropna()
    v = v[~np.isin(v.round(4), _FILL_MARKS)]
    return float(np.median(np.abs(v))) if len(v) else float("nan")


def _decide(col: str, s: pd.Series) -> str:
    """Ölçek kararı: 'none' | 'x10000' (NDVI ölçekli geldi) | 'div0.02' (LST Kelvin geldi) | 'x0.1' (ET ham geldi)."""
    m = _median_valid(s)
    if math.isnan(m):
        return "none"
    if col.lower().endswith("ndvi"):
        return "x10000" if m <= 1.5 else "none"
    if col.startswith("LST_"):
        return "div0.02" if m < 400 else "none"
    if col == ET_COL:
        v = s.dropna()
        v = v[v < 3000]
        return "x0.1" if len(v) and v.max() > 300 else "none"  # ham ET 0..~1000, gerçek mm 0..~100
    return "none"


def _normalize_scaling(df: pd.DataFrame) -> pd.DataFrame:
    """NDVI/LST'yi kayıttaki ham ölçeğe (x1e4, /0.02) çevirir; ET'yi gerçek mm/8 gün yapar."""
    df = df.copy()
    decisions: dict[str, str] = {}
    for c in [c for c in df.columns if c not in BASE_COLS]:
        d = decisions[ET_OUT if c == ET_COL else c] = _decide(c, df[c])
        if d == "x10000":
            df[c] = (df[c] * 10000.0).round(1)
        elif d == "div0.02":
            df[c] = (df[c] / 0.02).round(1)
        elif d == "x0.1":
            df[c] = df[c] * 0.1
        if c == ET_COL:
            df[c] = df[c].where(df[c] < 3000)  # fill (32767 * 0.1 = 3276.7) -> NaN
    df = df.rename(columns={ET_COL: ET_OUT})
    df.attrs["scaling"] = decisions
    return df


def scaling_report(df: pd.DataFrame) -> pd.DataFrame:
    """Sütun başına min/medyan/maks (ayrıştırılmış tablo) + ölçek kararı."""
    rows = []
    for c in [c for c in df.columns if c not in BASE_COLS]:
        v = df[c].dropna()
        rows.append({"column": c, "n": int(v.size),
                     "min": float(v.min()) if v.size else float("nan"),
                     "median": float(v.median()) if v.size else float("nan"),
                     "max": float(v.max()) if v.size else float("nan"),
                     "decision": df.attrs.get("scaling", {}).get(c, "n/a")})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- yükseklik / eğim
def compute_slope_aspect(z: dict[str, float], lat: float, step_deg: float = 0.0005) -> tuple[float, float]:
    """4 komşudan eğim (%) ve bakı (derece, 0=K, saat yönü). z: n/s/e/w yükseklikleri (m)."""
    dx = 2 * step_deg * 111320.0 * math.cos(math.radians(lat))
    dy = 2 * step_deg * 110540.0
    dzdx = (z["e"] - z["w"]) / dx
    dzdy = (z["n"] - z["s"]) / dy
    slope = 100.0 * math.hypot(dzdx, dzdy)
    # bakı: yokuş AŞAĞI yönü (eğimin tersi), kuzeyden saat yönünde
    aspect = (math.degrees(math.atan2(-dzdx, -dzdy)) + 360.0) % 360.0 if slope > 0 else float("nan")
    return slope, aspect


def elevation_table(df: pd.DataFrame, elev_col: str | None = None) -> pd.DataFrame:
    """Parse edilmiş DEM tablosundan (komşular dahil) area_id, elevation_m, slope_pct, aspect_deg."""
    elev_col = elev_col or next(c for c in df.columns if c not in BASE_COLS and "dem" in c.lower())
    z = df.groupby("area_id")[elev_col].mean()
    rows = []
    for aid in sorted({i for i in z.index if "__" not in i}):
        nb = {s: z.get(f"{aid}__{s}") for s in NEIGHBOR_SUFFIX}
        lat = float(df.loc[df["area_id"] == aid, "lat"].iloc[0])
        if any(v is None or pd.isna(v) for v in nb.values()):
            slope, aspect = float("nan"), float("nan")
        else:
            slope, aspect = compute_slope_aspect(nb, lat)
        rows.append({"area_id": aid, "elevation_m": float(z[aid]), "slope_pct": slope, "aspect_deg": aspect})
    return pd.DataFrame(rows, columns=["area_id", "elevation_m", "slope_pct", "aspect_deg"])


# ---------------------------------------------------------------- iş akışı
def _layer_objs(product: str, layers: Sequence[str]) -> list[dict]:
    return [{"product": product, "layer": l} for l in layers]


def _jobs(presets: Sequence[str], areas: list[dict]) -> list[dict]:
    """Ön ayarlardan görev listesi: her ürün bir görev (hls iki ürün tek görevde)."""
    jobs = []
    for name in presets:
        if name not in PRESETS:
            raise ValueError(f"unknown product preset: {name} (known: {', '.join(PRESETS)})")
        p = PRESETS[name]
        pts = points_from_areas(areas, with_neighbors=(name == "elev"))
        layers = _layer_objs(p["product"], p["layers"])
        prods = [p["product"]]
        for ex in p.get("extra", []):
            layers += _layer_objs(ex["product"], ex["layers"])
            prods.append(ex["product"])
        jobs.append({"name": name, "preset": p, "points": pts, "layers": layers, "products": prods})
    return jobs


def _parse_job_csvs(job: dict, csvs: list[Path]) -> pd.DataFrame:
    """Görevin CSV'lerini ayrıştırır; çoklu ürünlü (HLS) görevde her CSV kendi ürün önekiyle."""
    frames = []
    for f in csvs:
        prod = next((p for p in job["products"] if re.sub(r"[^0-9A-Za-z]+", "_", p) in f.name), job["products"][0])
        frames.append(parse_point_csv(f, prod, job["preset"]["source"]))
    if not frames:
        return pd.DataFrame(columns=BASE_COLS)
    out = pd.concat(frames, ignore_index=True)
    out.attrs["scaling"] = {k: v for f in frames for k, v in f.attrs.get("scaling", {}).items()}
    return out


def _fetch_results(token: str, task_id: str, dest_dir: Path) -> list[Path]:
    files = bundle_files(token, task_id)
    out = []
    for f in files:
        nm = f.get("file_name", "")
        if nm.endswith("-results.csv"):
            out.append(download_file(token, task_id, f["file_id"], dest_dir / Path(nm).name))
    return out


def run(areas: list[dict], start, end, out_dir, presets: Sequence[str], static_dir=DEFAULT_STATIC,
        timeout_min: float = 240, check: bool = False, poll_s: float = POLL_S,
        tasks_path: str | Path | None = None, token: str | None = None) -> dict:
    """Görevleri gönder/yeniden kullan, bitenleri indirip yaz. {name: {...durum}} döner."""
    out_dir = Path(out_dir)
    tasks_path = Path(tasks_path) if tasks_path else out_dir.parent / "appeears_tasks.json"
    own_token = token is None
    token = token or login()
    results: dict[str, dict] = {}
    try:
        jobs = _jobs(presets, areas)
        store = _load_json(tasks_path)
        for j in jobs:
            key = task_key("+".join(j["products"]), [l["layer"] for l in j["layers"]], j["points"], start, end)
            j["key"] = key
            tid = store.get(key, {}).get("task_id")
            if tid:  # önceki görev hâlâ geçerli mi?
                try:
                    st = task_status(token, tid)
                    if st.get("status") in ("error", "deleted"):
                        tid = None
                except RuntimeError:
                    tid = None
            try:
                if not tid:
                    tid = submit_task(token, build_task_body(f"fs_{j['name']}_{str(start)[:7]}", start, end,
                                                             j["layers"], j["points"]))
                    log.info("submitted %s -> %s", j["name"], tid)
                else:
                    log.info("reusing %s -> %s", j["name"], tid)
                store[key] = {"task_id": tid, "name": j["name"]}
                _save_json(tasks_path, store)
                j["task_id"] = tid
                results[j["name"]] = {"task_id": tid, "state": "pending"}
            except Exception as ex:  # noqa: BLE001
                log.error("submit failed %s: %s", j["name"], ex)
                results[j["name"]] = {"state": "failed", "error": str(ex)}

        deadline = _time.monotonic() + timeout_min * 60
        pending = [j for j in jobs if results[j["name"]]["state"] == "pending"]
        while pending:
            still = []
            for j in pending:
                r = results[j["name"]]
                try:
                    st = task_status(token, j["task_id"]).get("status", "")
                except Exception as ex:  # noqa: BLE001
                    log.warning("status %s: %s", j["name"], ex)
                    still.append(j)
                    continue
                if st == "done":
                    try:
                        _finish(token, j, out_dir, static_dir, r, check)
                    except Exception as ex:  # noqa: BLE001
                        log.error("FAILED %s: %s", j["name"], ex)
                        r.update(state="failed", error=str(ex))
                elif st in ("error", "deleted"):
                    r.update(state="failed", error=f"task status {st}")
                else:
                    still.append(j)
            pending = still
            if pending:
                if _time.monotonic() > deadline:
                    for j in pending:
                        results[j["name"]].update(state="timeout", error="timed out (task id saved; re-run to resume)")
                    break
                _time.sleep(poll_s)
    finally:
        if own_token:
            logout(token)
    return results


def _finish(token: str, j: dict, out_dir: Path, static_dir, r: dict, check: bool) -> None:
    raw_dir = out_dir / "appeears_downloads" / j["task_id"]
    csvs = _fetch_results(token, j["task_id"], raw_dir)
    if not csvs:
        raise RuntimeError("no *-results.csv in bundle")
    df = _parse_job_csvs(j, csvs)
    if df.empty:
        raise RuntimeError("no rows returned")
    scal = dict(df.attrs.get("scaling", {}))
    if j["name"] == "elev":
        et = elevation_table(df)
        static_dir = Path(static_dir)
        static_dir.mkdir(parents=True, exist_ok=True)
        et.to_parquet(static_dir / "elevation.parquet", index=False)
        df = df[~df["area_id"].str.contains("__")].copy()
        df.attrs["scaling"] = scal
        r["elevation"] = et.to_dict("records")
    write_raw(df, out_dir)
    r.update(state="done", rows=len(df))
    if check:
        r["report"] = scaling_report(df)
        r["per_area"] = df.groupby("area_id").size().to_dict()
        r["head"] = df.head(3)


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--areas")
    p.add_argument("--start", default="2024-01-01")
    p.add_argument("--end", default="2025-12-31")
    p.add_argument("--out", default="ml/cache/raw")
    p.add_argument("--products", default="ndvi,ndvi_aqua,lst,et,smap,elev",
                   help=",".join(PRESETS))
    p.add_argument("--static-out", default=DEFAULT_STATIC)
    p.add_argument("--timeout-min", type=float, default=240)
    p.add_argument("--check", action="store_true")
    p.add_argument("--list-layers", metavar="PRODUCT")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if a.list_layers:
        for k, v in list_layers(None, a.list_layers).items():
            print(f"{k}\t{v.get('Units')}\t{v.get('FillValue')}\t{v.get('ScaleFactor')}\t{v.get('Description')}")
        return 0
    if not a.areas:
        p.error("--areas required")
    areas = json.loads(Path(a.areas).read_text())
    res = run(areas, a.start, a.end, a.out, [s.strip() for s in a.products.split(",") if s.strip()],
              a.static_out, a.timeout_min, a.check)
    bad = 0
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 30)
    print("\n== summary ==")
    for name, r in res.items():
        print(f"{name}: {r['state']} {r.get('rows', '')} {r.get('error', '')} task={r.get('task_id', '')}")
        if r["state"] != "done":
            bad += 1
        if a.check and r["state"] == "done":
            print(r["report"].to_string(index=False))
            print("rows per area:", r["per_area"])
            print(r["head"].to_string())
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
