"""fields.py testleri (ağ mock'lanır)."""
import json

import pandas as pd

from ml.data_load import fields as f

CDL_XML = ('<ns1:GetCDLValueResponse xmlns:ns1="http://cropscape.csiss.gmu.edu/CDLService/"><Result>'
           '{x: -250000.0, y: 2000000.0, value: 176, category: "Grass/Pasture", color: "#E9FFBE"}'
           '</Result></ns1:GetCDLValueResponse>')


def test_albers_known_points():
    assert f.albers_5070(23.0, -96.0) == (0.0, 0.0)           # projeksiyon başlangıcı
    x, y = f.albers_5070(37.5, -96.0)
    assert abs(x) < 1e-6 and 1_606_000 < y < 1_607_500        # orta meridyen
    x, y = f.albers_5070(42.03, -93.74)
    assert 185_000 < x < 187_000 and 2_115_000 < y < 2_118_000
    assert f.albers_5070(40, -100)[0] < 0 < f.albers_5070(40, -90)[0]


def test_parse_cdl():
    assert f.parse_cdl_response(CDL_XML) == (176, "Grass/Pasture")
    assert f.parse_cdl_response("<Result>no data</Result>") is None


def test_crop_key_mapping():
    assert f.crop_key_for(1) == "corn" and f.crop_key_for(24) == "winter_wheat"
    assert f.crop_key_for(26) == "winter_wheat>soybean"
    assert f.crop_key_for(61) == "fallow" and f.crop_key_for(176) is None
    assert f.crop_key_for(3) is None  # çeltik modelde yok
    assert f.cdl_category(121) == "developed" and f.cdl_category(141) == "forest"
    assert f.cdl_category(111) == "water" and f.cdl_category(176) == "grass"
    assert f.is_crop_code(5) and not f.is_crop_code(61) and not f.is_crop_code(176)


def test_cdl_value_and_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(f, "_http", lambda url, *a, **k: calls.append(url) or CDL_XML)
    c = f.DiskCache(None)
    assert f.cdl_value(42.0, -93.7, 2020, c) == (176, "Grass/Pasture")
    assert f.cdl_value(42.0, -93.7, 2020, c) == (176, "Grass/Pasture")
    assert len(calls) == 1 and "year=2020" in calls[0]


def _h(keys):
    return pd.DataFrame({"year": range(2018, 2018 + len(keys)), "crop_key": keys})


def test_infer_rotation():
    r = f.infer_rotation(_h(["corn", "soybean"] * 3))
    assert r["rotation"] == "corn>soybean" and r["confidence"] == 1.0
    assert f.infer_rotation(_h(["corn"] * 6))["rotation"] == "corn"
    r = f.infer_rotation(_h(["winter_wheat", "sorghum", "fallow"] * 2))
    assert r["rotation"] == "winter_wheat>sorghum>fallow" and r["period"] == 3
    r = f.infer_rotation(_h(["corn", "soybean", "corn", "soybean", "corn", "corn"]))  # tek aykırı yıl
    assert r["rotation"] == "corn>soybean" and 0.7 < r["confidence"] < 1
    r = f.infer_rotation(_h(["corn", "corn", "corn", "soybean", "corn", "corn"]))
    assert r["rotation"] == "corn"
    assert f.infer_rotation(_h([None, None, "corn"]))["rotation"] is None
    # çift ürün ana ürüne indirgenir
    assert f.infer_rotation(_h(["winter_wheat>soybean"] * 6))["rotation"] == "winter_wheat"


def test_texture_triangle():
    assert f.texture_class(45, 21, 34) == "loam"
    assert f.texture_class(92, 4, 4) == "sand"
    assert f.texture_class(10, 10, 80) == "silt"
    assert f.texture_class(15, 15, 70) == "silt_loam"
    assert f.texture_class(20, 55, 25) == "clay"
    assert f.texture_class(10, 35, 55) == "silty_clay_loam"
    assert f.texture_class(60, 25, 15) == "sandy_clay_loam"
    assert f.texture_class(None, 20) is None


SDA = json.dumps({"Table": [
    ["mukey", "muname", "cokey", "compname", "comppct_r", "slope_r", "hydgrp", "drainagecl", "hzdept_r",
     "hzdepb_r", "om_r", "claytotal_r", "sandtotal_r", "silttotal_r", "awc_r", "ph1to1h2o_r", "ksat_r"],
    ["2765522", "Clarion loam, Bemis moraine, 2 to 6 percent slopes", "1", "Clarion", "85", "3", "B",
     "Well drained", "0", "23", "3.5", "21", "45", "34", "0.21", "6.2", "9.17"]]})


def test_parse_sda():
    s = f.parse_sda_response(SDA)
    assert s["mukey"] == "2765522" and s["comppct"] == 85.0 and s["om_pct"] == 3.5
    assert s["soil_texture"] == "loam" and s["hydgrp"] == "B" and s["ksat_um_s"] == 9.17
    assert f.parse_sda_response("") is None
    assert f.parse_sda_response('{"Table":[["mukey"]]}') is None


def test_soil_at(monkeypatch):
    seen = []
    monkeypatch.setattr(f, "_http", lambda url, data=None, **k: seen.append(data) or SDA)
    c = f.DiskCache(None)
    assert f.soil_at(42.03, -93.74, c)["compname"] == "Clarion"
    f.soil_at(42.03, -93.74, c)
    assert len(seen) == 1 and b"point(-93.740000 42.030000)" in seen[0]


def test_candidates_and_select(monkeypatch):
    cands = f.candidate_points()
    assert len(cands) >= 100 and len({c["area_id"] for c in cands}) == len(cands)
    assert {"corn_belt", "great_plains", "delta_southeast", "central_valley", "pacific_nw"} <= {c["region"] for c in cands}

    def fake_hist(lat, lon, years, cache=None, workers=1):
        years = list(years)
        if lat < 0:  # kasaba: ekili yıl yok
            codes = [121] * len(years)
        else:
            codes = [1 if i % 2 == 0 else 5 for i in range(len(years))]
        return pd.DataFrame({"year": years, "cdl_code": pd.array(codes, dtype="Int64"),
                             "cdl_name": "x", "crop_key": [f.crop_key_for(c) for c in codes],
                             "category": "crop"})

    monkeypatch.setattr(f, "cdl_history", fake_hist)
    kept, hist = f.select_fields([{"area_id": "a", "lat": 42.0, "lon": -93.0, "state": "IA", "region": "r"},
                                  {"area_id": "b", "lat": -1.0, "lon": -93.0, "state": "IA", "region": "r"}])
    assert [k["area_id"] for k in kept] == ["a"]
    assert kept[0]["existing_rotation"] == "soybean>corn" and len(kept[0]["bbox"]) == 4
    assert set(hist["area_id"]) == {"a"} and len(hist) == 11
