"""usda.py testleri: yalnızca sahte HTTP, ağ yok."""
import io
import json
import urllib.error

import numpy as np
import pytest

from ml.data_load import usda


class _Resp:
    def __init__(self, obj):
        self.b = json.dumps(obj).encode()

    def read(self):
        return self.b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(usda, "MIN_INTERVAL_S", 0)
    monkeypatch.setattr(usda._time, "sleep", lambda s: None)


def _mock(monkeypatch, handler):
    calls = []

    def fake(req, timeout=None):
        calls.append(req)
        return handler(req)
    monkeypatch.setattr(usda.urllib.request, "urlopen", fake)
    return calls


def test_parse_county():
    r = {"result": {"geographies": {"Counties": [
        {"STATE": "19", "COUNTY": "015", "BASENAME": "Boone", "NAME": "Boone County"}]}}}
    assert usda.parse_county(r) == {"state_fips": "19", "county_fips": "015",
                                    "state_alpha": "IA", "county_name": "Boone"}
    assert usda.parse_county({"result": {"geographies": {}}}) is None


def test_county_of_cached(monkeypatch):
    resp = {"result": {"geographies": {"Counties": [{"STATE": "19", "COUNTY": "015", "BASENAME": "Boone"}]}}}
    calls = _mock(monkeypatch, lambda r: _Resp(resp))
    cache = usda.JsonCache(None)
    assert usda.county_of(42.03, -93.74, cache)["county_name"] == "Boone"
    usda.county_of(42.03, -93.74, cache)
    assert len(calls) == 1


def test_parse_value():
    assert usda.parse_value("1,234.5") == 1234.5
    for v in ("(D)", "(Z)", "(NA)", "", "abc"):
        assert np.isnan(usda.parse_value(v))


def test_conversions():
    assert usda.yield_to_t_ha(1, "BU / ACRE", "corn") == pytest.approx(0.0628, abs=1e-4)
    assert usda.yield_to_t_ha(1, "BU / ACRE", "soybean") == pytest.approx(0.0673, abs=1e-4)
    assert usda.yield_to_t_ha(1000, "LB / ACRE", "cotton") == pytest.approx(1.1209, abs=1e-3)
    assert usda.yield_to_t_ha(1, "TONS / ACRE", "alfalfa") == pytest.approx(2.2417, abs=1e-3)
    assert usda.price_usd_per_t(4.25, "$ / BU", "corn") == pytest.approx(167.3, abs=0.1)
    assert usda.price_usd_per_t(10, "$ / CWT", "sunflower") == pytest.approx(220.46, abs=0.1)
    assert usda.price_usd_per_t(100, "$ / TON", "alfalfa") == pytest.approx(110.23, abs=0.1)


def test_crop_mapping():
    assert usda.crop_of_desc("CORN, GRAIN - YIELD, MEASURED IN BU / ACRE") == "corn"
    assert usda.crop_of_desc("HAY, ALFALFA - YIELD, MEASURED IN TONS / ACRE") == "alfalfa"
    assert usda.crop_of_desc("WHEAT, SPRING - YIELD, MEASURED IN BU / ACRE") is None


def _row(val, year="2024", **kw):
    d = {"short_desc": "CORN, GRAIN - YIELD, MEASURED IN BU / ACRE", "Value": val, "year": year,
         "state_alpha": "IA", "county_name": "BOONE", "county_code": "015"}
    return {**d, **kw}


def test_parse_yields_skips_codes():
    df = usda.parse_yields([_row("1,234"), _row("(D)", "2023"), _row("200.5", "2022")])
    assert sorted(df["year"]) == [2022, 2024]
    assert df.loc[df.year == 2024, "yield_value"].iloc[0] == 1234.0


def test_county_yields_http(monkeypatch):
    monkeypatch.setenv("NASS_API_KEY", "SECRETKEY123")
    calls = _mock(monkeypatch, lambda r: _Resp({"data": [_row("200"), _row("210", "2020")]}))
    df = usda.county_yields("IA", commodities=["corn"], years=(2023, 2024), county_ansi="015")
    assert list(df["year"]) == [2024]  # aralık dışı süzülür
    assert "county_ansi=015" in calls[0].full_url


def test_parse_prices_prefers_marketing_year():
    rows = [{"short_desc": "CORN, GRAIN - PRICE RECEIVED, MEASURED IN $ / BU", "Value": "4.00", "year": "2024",
             "state_alpha": "IA", "freq_desc": "ANNUAL", "reference_period_desc": "YEAR"},
            {"short_desc": "CORN, GRAIN - PRICE RECEIVED, MEASURED IN $ / BU", "Value": "4.25", "year": "2024",
             "state_alpha": "IA", "freq_desc": "ANNUAL", "reference_period_desc": "MARKETING YEAR"},
            {"short_desc": "CORN, GRAIN - PRICE RECEIVED, MEASURED IN $ / BU", "Value": "9", "year": "2024",
             "state_alpha": "IA", "freq_desc": "MONTHLY", "reference_period_desc": "JAN"}]
    df = usda.parse_prices(rows)
    assert len(df) == 1 and df["price"].iloc[0] == 4.25


def test_nass_400_is_empty_and_key_scrubbed(monkeypatch):
    monkeypatch.setenv("NASS_API_KEY", "SECRETKEY123")

    def h(req):
        raise urllib.error.HTTPError(req.full_url, 400, "bad", {}, io.BytesIO(b"bad key=SECRETKEY123"))
    _mock(monkeypatch, h)
    assert usda.county_yields("IA", commodities=["corn"], county_ansi="015").empty


def test_key_not_in_error(monkeypatch):
    monkeypatch.setenv("NASS_API_KEY", "SECRETKEY123")

    def h(req):
        raise urllib.error.URLError(f"fail {req.full_url}")
    _mock(monkeypatch, h)
    with pytest.raises(RuntimeError) as ei:
        usda.county_yields("IA", commodities=["corn"], county_ansi="015")
    assert "SECRETKEY123" not in str(ei.value)
    assert "SECRETKEY123" not in usda.scrub("x?key=SECRETKEY123&a=1")


def test_missing_key(monkeypatch):
    monkeypatch.delenv("NASS_API_KEY", raising=False)
    monkeypatch.setattr(usda, "load_key", lambda n: None)
    with pytest.raises(usda.MissingKey):
        usda.county_yields("IA", commodities=["corn"], county_ansi="015")


def test_openet_parse_and_raw(monkeypatch):
    monkeypatch.setenv("OPENET_API_KEY", "OPENETSECRET")
    calls = _mock(monkeypatch, lambda r: _Resp([{"time": "2024-01-01", "et": 29.0},
                                                {"time": "2024-02-01", "et": 56.0}]))
    out, why = usda.openet_raw({"area_id": "a", "lat": 36.5, "lon": -120.1, "state_alpha": "CA"},
                               "2024-01-01", "2024-02-29")
    assert why is None and len(calls) == 1
    assert list(out.columns) == usda.OPENET_COLS
    assert out["source"].iloc[0] == "OPENET" and out["openet_et_month_mm"].tolist() == [29.0, 56.0]
    assert str(out["time"].dtype) == "datetime64[ns]"
    body = json.loads(calls[0].data)
    assert body["geometry"] == [-120.1, 36.5] and body["interval"] == "monthly"


def test_openet_outside_coverage_no_request(monkeypatch):
    calls = _mock(monkeypatch, lambda r: _Resp([]))
    out, why = usda.openet_raw({"area_id": "a", "lat": 42, "lon": -93.7, "state_alpha": "IA"}, "2024-01-01", "2024-02-01")
    assert out.empty and "coverage" in why and not calls


def test_openet_4xx_is_empty(monkeypatch):
    monkeypatch.setenv("OPENET_API_KEY", "OPENETSECRET")

    def h(req):
        raise urllib.error.HTTPError(req.full_url, 400, "bad", {}, io.BytesIO(b"outside OPENETSECRET"))
    _mock(monkeypatch, h)
    out, why = usda.openet_raw({"area_id": "a", "lat": 1, "lon": 1}, "2024-01-01", "2024-02-01")
    assert out.empty and "OPENETSECRET" not in why
