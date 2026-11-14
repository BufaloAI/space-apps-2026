"""download.py testleri (ağ mock'lanır)."""
from datetime import datetime

import pandas as pd

from ml.data_load import download as dl


def test_power_parse(monkeypatch):
    calls = []

    def fake(url, *_a, **_k):
        calls.append(url)
        if "FOO" in url:
            raise RuntimeError("HTTP 422: One of your parameters is incorrect: FOO.")
        return {"properties": {"parameter": {
            "T2M": {"20240101": -3.5, "20240102": -999.0},
            "GWETTOP": {"20240101": 0.5, "20240102": 0.6}}}, "messages": []}

    monkeypatch.setattr(dl, "_fetch_json", fake)
    df = dl.fetch_power("a", 42.0, -93.0, "2024-01-01", "2024-01-02", ["T2M", "GWETTOP", "FOO"])
    assert len(calls) == 2
    assert list(df.columns) == ["source", "area_id", "lat", "lon", "time", "T2M", "GWETTOP"]
    assert df["time"].dtype == "datetime64[ns]" and df["lat"].dtype == "float64"
    assert df["T2M"].iloc[1] == -999.0  # fill korunur


def test_ornl_parse_chunks(monkeypatch):
    dates = [{"modis_date": f"A2024{d:03d}", "calendar_date": str(datetime(2024, 1, 1) + pd.Timedelta(days=d - 1))[:10]}
             for d in range(1, 365, 16)]
    subs = []

    def fake(url, *_a, **_k):
        if "/dates" in url:
            return {"dates": dates}
        subs.append(url)
        n = 3 if "reliability" in url else 1
        ds = [x for x in dates if x["modis_date"] >= url.split("startDate=")[1][:8]
              and x["modis_date"] <= url.split("endDate=")[1][:8]]
        return {"subset": [{"calendar_date": x["calendar_date"], "data": [1] * (n // 2) + [4367] + [1] * (n // 2)} for x in ds]}

    monkeypatch.setattr(dl, "_fetch_json", fake)
    df = dl.fetch_modis_ornl("a", 42.0, -93.0, "2024-01-01", "2024-12-31", "MOD13Q1",
                             ["250m_16_days_NDVI", "250m_16_days_pixel_reliability"])
    assert len(df) == 23 and len(subs) == 2 * 3  # 23 tarih -> 3 parça/bant
    assert (df["250m_16_days_NDVI"] == 4367).all()
    assert (df["250m_16_days_pixel_reliability"] == 4367).all()  # merkez piksel
    assert df["time"].dtype == "datetime64[ns]"


def test_select_hours():
    items = [(datetime(2024, 1, 1, 1, 30) + pd.Timedelta(hours=3 * i), i) for i in range(16)]
    sel = dl.select_granules_by_hour([(t, o) for t, o in items], "2024-01-01", "2024-01-02")
    assert [t.strftime("%d %H:%M") for t, _ in sel] == [
        "01 01:30", "01 07:30", "01 13:30", "01 19:30",
        "02 01:30", "02 07:30", "02 13:30", "02 19:30"]


def test_write_raw_merge(tmp_path):
    def mk(vals):
        return pd.DataFrame({"source": "POWER", "area_id": "a", "lat": 1.0, "lon": 2.0,
                             "time": pd.to_datetime(["2024-01-01", "2024-01-02"][:len(vals)]).astype("datetime64[ns]"),
                             "T2M": vals})
    dl.write_raw(mk([1.0, 2.0]), tmp_path)
    p = dl.write_raw(mk([1.0]), tmp_path)
    assert len(pd.read_parquet(p)) == 2
    other = mk([5.0]).assign(area_id="b")
    dl.write_raw(other, tmp_path)
    assert len(pd.read_parquet(p)) == 3
    assert not list(tmp_path.glob("*.tmp"))


def test_composite_dates_and_parallel(monkeypatch):
    assert dl.composite_dates("MOD13Q1", "2024-12-15", "2025-01-20") == ["A2024353", "A2025001", "A2025017"]

    def fake(url, *_a, **_k):
        assert "/dates" not in url  # 16 günlük ürün için yavaş /dates çağrılmaz
        return {"subset": [{"calendar_date": "2025-01-01", "data": [5000]}]}

    monkeypatch.setattr(dl, "_fetch_json", fake)
    df = dl.fetch_modis_ornl("a", 1.0, 2.0, "2025-01-01", "2025-01-10", parallel=True)
    assert df["250m_16_days_NDVI"].tolist() == [5000.0]


def test_ornl_slow_backoff(monkeypatch):
    import urllib.error

    sleeps = []
    monkeypatch.setattr(dl._time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(dl, "_throttle", lambda host: None)

    def boom(*a, **k):
        raise urllib.error.HTTPError("u", 503, "Service Unavailable", {}, None)

    monkeypatch.setattr(dl.urllib.request, "urlopen", boom)
    try:
        dl._fetch_json("https://modis.ornl.gov/rst/api/v1/MOD13Q1/subset?x=1", timeout=1, retries=1)
    except RuntimeError:
        pass
    assert len(sleeps) == 6 and sleeps[0] >= 15 and max(sleeps) <= 125  # ORNL: en az 6 deneme, uzun bekleme


def test_ornl_impatient_for_live_requests(monkeypatch):
    import urllib.error

    sleeps = []
    monkeypatch.setattr(dl._time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(dl, "_throttle", lambda host: None)
    monkeypatch.setattr(dl.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(urllib.error.HTTPError("u", 503, "x", {}, None)))
    try:
        dl._fetch_json("https://modis.ornl.gov/rst/api/v1/x", timeout=1, retries=2, patient=False)
    except RuntimeError:
        pass
    assert len(sleeps) == 2 and max(sleeps) < 10  # backend: kısa bekleme, az deneme
