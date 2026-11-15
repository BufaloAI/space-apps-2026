"""Seçili bölge için günlük iklim serisi (NASA POWER), disk önbellekli.

Frontend'in "İklim analizi / Tarımsal göstergeler / Veri gezgini" sayfaları için. Oturum gerektirmez
(LLM/ML slotunu kullanmaz); yalnızca veri çeker. İndirici `ml/data_load/download.fetch_power`'dır.

Önbellek: nokta POWER ızgarasından daha ince (0.1°) yuvarlanır, yıl başına tek dosya.
- Geçmiş yıllar tamdır: dosya kalıcıdır.
- İçinde bulunulan yıl her gün yenilenir (POWER birkaç gün gecikmeli yayınlar).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import math
import sys
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger("backend.climate")

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE_DIR = REPO_ROOT / "ml" / "cache" / "climate"
FIRST_YEAR = 1981  # POWER günlük verinin başlangıcı
GRID_DEG = 0.1

# POWER parametresi -> frontend alanı
PARAMS = {
    "T2M": "temperature",
    "PRECTOTCORR": "rainfall",
    "RH2M": "humidity",
    "ALLSKY_SFC_SW_DWN": "solar",
    "GWETTOP": "moisture",
}
UNITS = {
    "temperature": "°C",
    "rainfall": "mm/gün",
    "humidity": "%",
    "solar": "MJ/m²/gün",
    "moisture": "0-1 (yüzey toprak ıslaklığı)",
}

Fetcher = Callable[[float, float, dt.date, dt.date], list[dict]]


class ClimateError(RuntimeError):
    """Veri alınamadı (kullanıcıya gösterilebilir mesaj)."""


def _power_fetch(lat: float, lon: float, start: dt.date, end: dt.date) -> list[dict]:
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from ml.data_load import download  # noqa: PLC0415

    df = download.fetch_power("climate", lat, lon, start, end, params=list(PARAMS), timeout=30, retries=2)
    rows = []
    for rec in df.to_dict("records"):
        row = {"date": rec["time"].date().isoformat()}
        for p, key in PARAMS.items():
            v = rec.get(p)
            # POWER eksik değeri -999 ile işaretler
            row[key] = None if v is None or not math.isfinite(v) or v <= -900 else round(float(v), 3)
        rows.append(row)
    return rows


def snap(v: float) -> float:
    return round(round(v / GRID_DEG) * GRID_DEG, 2)


class ClimateStore:
    def __init__(self, cache_dir: str | Path | None = None, fetcher: Fetcher | None = None,
                 today: Callable[[], dt.date] = dt.date.today) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
        self.fetcher = fetcher or _power_fetch
        self.today = today
        self._locks: dict[str, asyncio.Lock] = {}

    def _path(self, lat: float, lon: float, year: int) -> Path:
        return self.cache_dir / f"power_{lat:.2f}_{lon:.2f}_{year}.json"

    def _fresh(self, data: dict, year: int) -> bool:
        today = self.today()
        return year < today.year or data.get("fetched_on") == today.isoformat()

    async def daily(self, lat: float, lon: float, year: int) -> dict:
        today = self.today()
        if not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise ValueError("Koordinat aralık dışında")
        if not FIRST_YEAR <= year <= today.year:
            raise ValueError(f"Yıl {FIRST_YEAR}-{today.year} arasında olmalı")
        lat, lon = snap(lat), snap(lon)
        path = self._path(lat, lon, year)
        lock = self._locks.setdefault(path.name, asyncio.Lock())
        async with lock:  # aynı bölge/yıl için eşzamanlı istekler tek indirme yapar
            stale = None
            if path.exists():
                try:
                    stale = json.loads(path.read_text())
                    if self._fresh(stale, year):
                        return {**stale, "cached": True}
                except (OSError, ValueError):
                    stale = None
            start = dt.date(year, 1, 1)
            end = min(dt.date(year, 12, 31), today - dt.timedelta(days=1))
            try:
                rows = await asyncio.to_thread(self.fetcher, lat, lon, start, end)
            except Exception as e:  # noqa: BLE001
                log.warning("POWER iklim verisi alınamadı (%s, %s, %s): %s", lat, lon, year, e)
                if stale:  # eski kopya varsa onu ver (bugünün güncellemesi yok)
                    return {**stale, "cached": True, "stale": True}
                raise ClimateError("NASA POWER iklim verisine şu an ulaşılamıyor; biraz sonra tekrar deneyin.") from e
            rows = [r for r in rows if r["date"].startswith(str(year))]
            if not rows:
                raise ClimateError("Bu konum ve yıl için iklim verisi dönmedi.")
            last = max((r["date"] for r in rows if any(r[k] is not None for k in UNITS)), default=None)
            data = {
                "source": "NASA POWER (günlük, AG topluluğu)",
                "lat": lat, "lon": lon, "year": year,
                "fetched_on": today.isoformat(),
                "complete": year < today.year,
                "last_date": last,
                "units": UNITS,
                "days": rows,
            }
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False))
            tmp.replace(path)
            return {**data, "cached": False}
