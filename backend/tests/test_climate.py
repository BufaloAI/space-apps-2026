import asyncio
import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.climate import ClimateError, ClimateStore


def _rows(year: int, n: int = 3) -> list[dict]:
    return [{"date": f"{year}-01-0{i + 1}", "temperature": 5.0 + i, "rainfall": 0.0, "humidity": 70.0,
             "solar": 8.0, "moisture": None} for i in range(n)]


def test_past_year_cached_permanently(tmp_path):
    calls = []

    def fetch(lat, lon, start, end):
        calls.append((lat, lon, start, end))
        return _rows(start.year)

    store = ClimateStore(tmp_path, fetch, today=lambda: dt.date(2026, 11, 15))
    a = asyncio.run(store.daily(41.0123, 29.0456, 2024))
    b = asyncio.run(store.daily(41.0301, 29.0499, 2024))  # aynı 0.1° hücre
    assert len(calls) == 1 and calls[0][:2] == (41.0, 29.0)
    assert calls[0][3] == dt.date(2024, 12, 31)
    assert a["cached"] is False and b["cached"] is True and a["complete"] is True
    assert a["days"][0]["temperature"] == 5.0


def test_current_year_refreshes_daily_and_falls_back_when_offline(tmp_path):
    day = [dt.date(2026, 11, 15)]
    fail = [False]

    def fetch(lat, lon, start, end):
        if fail[0]:
            raise RuntimeError("offline")
        return _rows(2026)

    store = ClimateStore(tmp_path, fetch, today=lambda: day[0])
    first = asyncio.run(store.daily(10, 10, 2026))
    assert first["complete"] is False and first["fetched_on"] == "2026-11-15"
    day[0] = dt.date(2026, 11, 16)
    fail[0] = True
    stale = asyncio.run(store.daily(10, 10, 2026))
    assert stale["stale"] is True and stale["days"]
    with pytest.raises(ClimateError):
        asyncio.run(store.daily(20, 20, 2026))  # önbellek yok + kaynak yok


def test_validation(tmp_path):
    store = ClimateStore(tmp_path, lambda *a: [], today=lambda: dt.date(2026, 11, 15))
    with pytest.raises(ValueError):
        asyncio.run(store.daily(10, 10, 2027))
    with pytest.raises(ValueError):
        asyncio.run(store.daily(95, 10, 2020))


def test_endpoint(monkeypatch, tmp_path):
    monkeypatch.setenv("VLLM_API_KEY", "test-key")
    monkeypatch.setenv("CLIMATE_CACHE_DIR", str(tmp_path))
    from app.main import app

    with TestClient(app) as c:
        c.app.state.climate.fetcher = lambda lat, lon, s, e: _rows(s.year)
        r = c.get("/api/climate/daily", params={"lat": 41, "lon": 29, "year": 2023})
        assert r.status_code == 200 and r.json()["days"][0]["date"] == "2023-01-01"
        assert c.get("/api/climate/daily", params={"lat": 41, "lon": 29, "year": 1900}).status_code == 422

        def boom(*_a):
            raise RuntimeError("down")

        c.app.state.climate.fetcher = boom
        assert c.get("/api/climate/daily", params={"lat": 1, "lon": 1, "year": 2023}).status_code == 502


def test_outlook_endpoint_cached(monkeypatch, tmp_path):
    monkeypatch.setenv("VLLM_API_KEY", "test-key")
    monkeypatch.setenv("CLIMATE_CACHE_DIR", str(tmp_path))
    from app.main import app

    calls = []

    def fake(lat, lon, today):
        calls.append((lat, lon))
        return {"weeks": [{"week": 1, "vars": {}}], "skill": {}, "history_years": 15}

    with TestClient(app) as c:
        c.app.state.climate.outlook_fn = fake
        a = c.get("/api/climate/outlook", params={"lat": 39.58, "lon": 32.15})
        b = c.get("/api/climate/outlook", params={"lat": 39.61, "lon": 32.13})  # aynı 0.1° hücre
        assert a.status_code == 200 and a.json()["lat"] == 39.6 and b.json()["cached"] is True
        assert len(calls) == 1
        c.app.state.climate.outlook_fn = lambda *a: None
        assert c.get("/api/climate/outlook", params={"lat": 10, "lon": 10}).status_code == 502
