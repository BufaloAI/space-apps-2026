"""Ham NASA tablosu -> temiz "uzun" tablo (area_id, lat, lon, time, var, value).

Girdi şekli değişebilir (nasa_pipeline CSV'si, indirici parquet'i, başka bir to_dataframe çıktısı).
Bu yüzden sütunlar adlarından ve tiplerinden tanınır:
- lat/lon: lat, latitude, cell_lat (lon için benzeri)
- zaman: time / valid_time / date; yoksa yil+ay+gun(+saat); yoksa zaman_formatli (YYYY.MM.DD.HH)
- değer: sayısal (ya da sayıya çevrilebilen) ve meta olmayan her sütun

Yapılanlar: dolgu değerleri (-999, -3000, -9999.9 ...) -> NaN, QA ile bulut/kar maskesi, ölçek/ofset,
geçerli aralık dışı -> NaN, alt-günlük veride yalnızca 00/06/12/18 UTC'ye yakın örnekler,
alan ataması (area_id sütunu, alan dosyası ya da ızgara), aynı piksel+zaman tekrarlarının ortalaması.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from . import registry as reg

log = logging.getLogger("ml.clean")

LAT_NAMES = ("lat", "latitude", "cell_lat", "y_lat")
LON_NAMES = ("lon", "longitude", "cell_lon", "x_lon")
TIME_NAMES = ("time", "valid_time", "date", "datetime", "calendar_date")


@dataclass
class CleanReport:
    rows_in: int = 0
    rows_out: int = 0
    synthetic: bool = False
    dropped_columns: dict[str, str] = field(default_factory=dict)
    fills_to_nan: dict[str, int] = field(default_factory=dict)
    qa_masked: dict[str, int] = field(default_factory=dict)
    out_of_range: dict[str, int] = field(default_factory=dict)
    hour_filtered_rows: int = 0
    unassigned_rows: int = 0
    bad_time_rows: int = 0
    variables: list[str] = field(default_factory=list)

    def merge(self, o: "CleanReport") -> None:
        self.rows_in += o.rows_in
        self.rows_out += o.rows_out
        self.synthetic |= o.synthetic
        self.dropped_columns.update(o.dropped_columns)
        for a, b in ((self.fills_to_nan, o.fills_to_nan), (self.qa_masked, o.qa_masked),
                     (self.out_of_range, o.out_of_range)):
            for k, v in b.items():
                a[k] = a.get(k, 0) + v
        self.hour_filtered_rows += o.hour_filtered_rows
        self.unassigned_rows += o.unassigned_rows
        self.bad_time_rows += o.bad_time_rows
        self.variables = sorted(set(self.variables) | set(o.variables))

    def to_dict(self) -> dict:
        return self.__dict__.copy()


# ---------------------------------------------------------------- alanlar
def load_areas(path: str | Path | None) -> list[dict] | None:
    """Alan dosyası: JSON liste [{area_id, lat, lon, bbox?: [w,s,e,n]}] ya da GeoJSON FeatureCollection
    (properties.area_id + Polygon). bbox yoksa noktanın etrafında `radius_deg` (varsayılan 0.1°) kutu."""
    if not path:
        return None
    data = json.loads(Path(path).read_text())
    areas = []
    if isinstance(data, dict) and data.get("type") == "FeatureCollection":
        from shapely.geometry import shape

        for i, f in enumerate(data["features"]):
            geom = shape(f["geometry"])
            aid = str((f.get("properties") or {}).get("area_id", f"area_{i}"))
            c = geom.centroid
            areas.append({"area_id": aid, "lat": c.y, "lon": c.x, "geometry": geom})
        return areas
    for a in data:
        lat, lon = float(a["lat"]), float(a["lon"])
        r = float(a.get("radius_deg", 0.1))
        bbox = a.get("bbox") or [lon - r, lat - r, lon + r, lat + r]
        areas.append({"area_id": str(a["area_id"]), "lat": lat, "lon": lon, "bbox": [float(v) for v in bbox]})
    return areas


def assign_areas(lat: np.ndarray, lon: np.ndarray, areas: list[dict] | None, grid_deg: float) -> np.ndarray:
    """Her noktaya area_id verir. Hiçbir alana düşmeyen nokta None olur."""
    if not areas:
        la = np.floor(lat / grid_deg) * grid_deg
        lo = np.floor(lon / grid_deg) * grid_deg
        return np.array([f"g{a:+.2f}_{b:+.2f}" for a, b in zip(la, lo)], dtype=object)
    out = np.full(len(lat), None, dtype=object)
    for a in areas:  # ilk eşleşen alan kazanır
        free = out == None  # noqa: E711 (numpy eleman bazlı)
        if "geometry" in a:
            import shapely

            inside = shapely.contains_xy(a["geometry"], lon, lat)
        else:
            w, s, e, n = a["bbox"]
            inside = (lon >= w) & (lon <= e) & (lat >= s) & (lat <= n)
        out[free & inside] = a["area_id"]
    return out


# ---------------------------------------------------------------- sütun tanıma
def _find(cols_norm: dict[str, str], names) -> str | None:
    for n in names:
        if n in cols_norm:
            return cols_norm[n]
    return None


def parse_time(df: pd.DataFrame, cols_norm: dict[str, str]) -> pd.Series:
    tcol = _find(cols_norm, TIME_NAMES)
    if tcol is not None:
        s = df[tcol]
        if pd.api.types.is_datetime64_any_dtype(s):
            t = s
        elif pd.api.types.is_numeric_dtype(s):
            # MODIS/ORNL tarzı YYYYDDD ya da unix saniye olabilir
            v = s.astype("float64")
            if v.dropna().between(1_900_001, 2_100_366).all():
                t = pd.to_datetime(v.astype("Int64").astype(str), format="%Y%j", errors="coerce")
            else:
                t = pd.to_datetime(v, unit="s", errors="coerce")
        else:
            # cftime (julian) metinleri dahil: "2024-01-01 00:00:00"; "A2024001" (MODIS) da desteklenir
            st = s.astype("string").str.strip()
            modis = st.str.match(r"^A\d{7}$", na=False)
            t = pd.to_datetime(st.where(~modis), format="mixed", errors="coerce", utc=True)
            t = t.dt.tz_convert(None)
            if modis.any():
                t = t.where(~modis, pd.to_datetime(st[modis].str[1:], format="%Y%j", errors="coerce"))
        if getattr(t.dt, "tz", None) is not None:
            t = t.dt.tz_convert(None)
        return t.astype("datetime64[ns]")
    y, m, d = (_find(cols_norm, (k,)) for k in ("yil", "ay", "gun"))
    if y and m and d:
        h = _find(cols_norm, ("saat",))
        parts = pd.DataFrame({"year": df[y], "month": df[m], "day": df[d],
                              "hour": df[h] if h else 0})
        return pd.to_datetime(parts, errors="coerce").astype("datetime64[ns]")
    z = _find(cols_norm, ("zaman_formatli",))
    if z:
        return pd.to_datetime(df[z].astype("string"), format="%Y.%m.%d.%H", errors="coerce").astype("datetime64[ns]")
    raise ValueError("Zaman sütunu bulunamadı (time/date/yil-ay-gun/zaman_formatli).")


def select_hours(time: pd.Series, lat, lon, hours, tol_h: float, ok: np.ndarray,
                 signature: np.ndarray | None = None) -> np.ndarray:
    """Her piksel, her ürün (dolu sütun imzası) ve her hedef saat (00/06/12/18 UTC) için en yakın zaman
    damgasını seçer; o zamandaki bütün satırlar kalır (aynı zamandaki tekrarlar sonra ortalanır).

    Eşitlikte hedeften sonra başlayan örnek tercih edilir (SMAP 01:30 granülü 00-03 aralığını kapsar).
    Günlük ürünler (00:00) hedefe tam denk gelir ve etkilenmez."""
    keep = np.zeros(len(time), dtype=bool)
    idx = np.flatnonzero(ok)
    if len(idx) == 0:
        return keep
    t = time.to_numpy()[idx]
    day = t.astype("datetime64[D]")
    hod = (t - day) / np.timedelta64(1, "h")
    hs = np.asarray(sorted(hours), dtype="float64")
    # aday hedefler: aynı günün saatleri + ertesi günün ilk saati (gece yarısını saran durum için)
    cand = np.concatenate([hs, [hs[0] + 24]])
    off = hod[:, None] - cand[None, :]
    j = np.argmin(np.abs(off) + (off < 0) * 1e-6, axis=1)
    o = off[np.arange(len(idx)), j]
    target = day.astype("datetime64[m]") + (cand[j] * 60).astype("timedelta64[m]")
    sig = signature[idx] if signature is not None else np.zeros(len(idx), dtype="int64")
    d = pd.DataFrame({"i": idx, "lat": np.asarray(lat)[idx], "lon": np.asarray(lon)[idx], "sig": sig,
                      "target": target, "t": t, "rank": np.abs(o) + (o < 0) * 1e-6})
    d = d[np.abs(o) <= tol_h]
    g = ["lat", "lon", "sig", "target"]
    best_rank = d.groupby(g)["rank"].transform("min")
    cand_t = d["t"].where(d["rank"] == best_rank)
    best_t = cand_t.groupby([d[c] for c in g]).transform("min")
    keep[d.loc[d["t"] == best_t, "i"].to_numpy()] = True
    return keep


# ---------------------------------------------------------------- ana temizlik
def clean_frame(
    df: pd.DataFrame,
    areas: list[dict] | None = None,
    hours: tuple[int, ...] | None = (0, 6, 12, 18),
    hour_tolerance_h: float = 1.5,
    grid_deg: float = 0.25,
) -> tuple[pd.DataFrame, CleanReport]:
    rep = CleanReport(rows_in=len(df))
    cols_norm = {reg.normalize_name(c): c for c in df.columns}
    if "synthetic" in cols_norm:
        rep.synthetic = bool(pd.to_numeric(df[cols_norm["synthetic"]], errors="coerce").fillna(0).max() > 0)

    lat_c, lon_c = _find(cols_norm, LAT_NAMES), _find(cols_norm, LON_NAMES)
    if lat_c is None or lon_c is None:
        raise ValueError(f"lat/lon sütunu yok. Sütunlar: {list(df.columns)[:20]}")
    time = parse_time(df, cols_norm)
    lat = pd.to_numeric(df[lat_c], errors="coerce").to_numpy("float64")
    lon = pd.to_numeric(df[lon_c], errors="coerce").to_numpy("float64")
    lon = np.where(lon > 180, lon - 360, lon)  # 0..360 ızgaraları
    ok = time.notna().to_numpy() & np.isfinite(lat) & np.isfinite(lon) & (np.abs(lat) <= 90) & (np.abs(lon) <= 180)
    rep.bad_time_rows = int((~ok).sum())

    # Alt-günlük örnekleri 00/06/12/18'e indir (günlük ürünler 00:00 olduğu için etkilenmez)
    if hours:
        vcols = [c for c in df.columns if c not in (lat_c, lon_c) and not reg.is_meta(c)
                 and pd.api.types.is_numeric_dtype(df[c])]
        sig = np.zeros(len(df), dtype="int64")
        for k, c in enumerate(vcols[:62]):
            sig |= df[c].notna().to_numpy().astype("int64") << k
        keep_h = select_hours(time, lat, lon, hours, hour_tolerance_h, ok, sig)
        rep.hour_filtered_rows = int((ok & ~keep_h).sum())
        ok &= keep_h

    # Alan ataması
    area_c = _find(cols_norm, ("area_id",))
    if area_c is not None:
        area = df[area_c].astype("string").to_numpy(dtype=object)
    else:
        area = assign_areas(lat, lon, areas, grid_deg)
    unassigned = pd.isna(area)
    rep.unassigned_rows = int((ok & unassigned).sum())
    ok &= ~unassigned

    # Değer sütunları
    skip = {lat_c, lon_c, area_c, _find(cols_norm, TIME_NAMES)}
    values: dict[str, np.ndarray] = {}
    raw_by_canonical: dict[str, np.ndarray] = {}
    specs: dict[str, reg.VarSpec] = {}
    for c in df.columns:
        if c in skip:
            continue
        if reg.is_meta(c):
            rep.dropped_columns[str(c)] = "meta"
            continue
        s = df[c]
        if not pd.api.types.is_numeric_dtype(s) or pd.api.types.is_bool_dtype(s):
            conv = pd.to_numeric(s, errors="coerce")
            if conv.notna().sum() < 0.5 * s.notna().sum():
                rep.dropped_columns[str(c)] = f"sayısal değil ({s.dtype})"
                continue
            s = conv
        spec = reg.resolve(c).spec
        v = s.to_numpy("float64", na_value=np.nan).copy()
        raw_by_canonical[spec.canonical] = v.copy()
        # dolgu
        is_fill = np.zeros(len(v), dtype=bool)
        for f in spec.fills:
            is_fill |= np.isclose(v, f, rtol=0, atol=1e-3)
        if spec.fills is reg.GENERIC_FILLS:
            is_fill |= v <= -999
        rep.fills_to_nan[spec.canonical] = rep.fills_to_nan.get(spec.canonical, 0) + int((is_fill & ok).sum())
        v[is_fill] = np.nan
        lo_r, hi_r = spec.valid_raw
        bad = np.zeros(len(v), dtype=bool)
        if lo_r is not None:
            bad |= v < lo_r
        if hi_r is not None:
            bad |= v > hi_r
        v = v * spec.scale + spec.offset
        lo, hi = spec.valid
        if lo is not None:
            bad |= v < lo
        if hi is not None:
            bad |= v > hi
        bad &= ~np.isnan(v)
        rep.out_of_range[spec.canonical] = rep.out_of_range.get(spec.canonical, 0) + int((bad & ok).sum())
        v[bad] = np.nan
        if spec.canonical in values:  # aynı kanonik ada iki kaynak (örn. IMERG + POWER yağış): birleştir
            prev = values[spec.canonical]
            v = np.where(np.isnan(prev), v, prev)
        values[spec.canonical] = v
        specs[spec.canonical] = spec

    # QA maskeleri
    qa_names = {s.qa for s in specs.values() if s.qa}
    for name, spec in specs.items():
        if spec.qa and spec.qa in raw_by_canonical:
            badq = reg.qa_bad(spec.qa, raw_by_canonical[spec.qa]) & ~np.isnan(values[name])
            rep.qa_masked[name] = rep.qa_masked.get(name, 0) + int((badq & ok).sum())
            values[name][badq] = np.nan
    for q in qa_names | {"ndvi_reliability", "lst_qc_day", "lst_qc_night"}:
        if values.pop(q, None) is not None:
            rep.dropped_columns[q] = "QA (maske olarak kullanıldı)"

    # Uzun biçim
    idx = np.flatnonzero(ok)
    parts = []
    for name, v in values.items():
        vv = v[idx]
        m = ~np.isnan(vv)
        if not m.any():
            rep.dropped_columns[name] = "tamamen boş (dolgu/QA sonrası)"
            continue
        sel = idx[m]
        parts.append(pd.DataFrame({
            "area_id": area[sel].astype(str), "lat": lat[sel], "lon": lon[sel],
            "time": time.to_numpy()[sel], "var": name, "value": vv[m],
        }))
    if not parts:
        return pd.DataFrame(columns=["area_id", "lat", "lon", "time", "var", "value"]), rep
    long = pd.concat(parts, ignore_index=True)
    long["var"] = long["var"].astype("category")
    # Aynı piksel + zaman + değişken tekrarları (örn. GPM'deki nv boyutu): ortalama
    long = long.groupby(["area_id", "lat", "lon", "time", "var"], observed=True, as_index=False)["value"].mean()
    rep.rows_out = len(long)
    rep.variables = sorted(long["var"].astype(str).unique())
    return long, rep


def read_any(path: Path, chunksize: int = 500_000):
    """CSV/parquet dosyasını parça parça okur (büyük dosyalar belleği doldurmasın)."""
    suf = path.suffix.lower()
    if suf == ".parquet":
        import pyarrow.parquet as pq

        pf = pq.ParquetFile(path)
        for b in pf.iter_batches(batch_size=chunksize):
            yield b.to_pandas()
    elif suf in (".csv", ".gz", ".txt"):
        yield from pd.read_csv(path, chunksize=chunksize, low_memory=False)
    else:
        raise ValueError(f"Desteklenmeyen dosya: {path}")


def clean_files(paths: list[Path], **kw) -> tuple[pd.DataFrame, CleanReport]:
    rep = CleanReport()
    outs = []
    for p in paths:
        for chunk in read_any(p):
            long, r = clean_frame(chunk, **kw)
            rep.merge(r)
            outs.append(long)
        log.info("temizlendi: %s", p)
    if not outs:
        raise ValueError("Okunacak veri yok")
    long = pd.concat(outs, ignore_index=True)
    long = long.groupby(["area_id", "lat", "lon", "time", "var"], observed=True, as_index=False)["value"].mean()
    rep.rows_out = len(long)
    return long, rep
