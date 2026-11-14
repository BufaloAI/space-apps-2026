"""appeears.py testleri (HTTP mock'lanır; gerçek ağ yok)."""
import json
import math

import pandas as pd
import pytest

from ml.data_load import appeears as ap
from ml.data_load.clean import clean_frame

NDVI_COLS = ("ID,Latitude,Longitude,Date,MODIS_Tile,MOD13Q1_061_Line_Y_250m,MOD13Q1_061_Sample_X_250m,"
             "MOD13Q1_061__250m_16_days_NDVI,MOD13Q1_061__250m_16_days_pixel_reliability,"
             "MOD13Q1_061__250m_16_days_pixel_reliability_PixelReliability_Description")


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return p


def _ndvi_csv(tmp_path, scaled: bool):
    v = (lambda x: x / 10000) if scaled else (lambda x: x)
    rows = [f"iowa,42.03,-93.74,2024-06-01,h11v04,1000,2000,{v(6500)},0,Good data",
            f"iowa,42.03,-93.74,2024-06-17,h11v04,1000,2000,{v(7200)},1,Marginal data",
            f"iowa,42.03,-93.74,2024-07-03,h11v04,1000,2000,{v(-3000)},-1,Fill"]
    return _write(tmp_path, "m-results.csv", NDVI_COLS + "\n" + "\n".join(rows) + "\n")


@pytest.mark.parametrize("scaled", [True, False])
def test_parse_ndvi_prefix_and_scaling(tmp_path, scaled):
    df = ap.parse_point_csv(_ndvi_csv(tmp_path, scaled), "MOD13Q1.061", "MOD13Q1")
    assert list(df.columns) == ["source", "area_id", "lat", "lon", "time",
                                "250m_16_days_NDVI", "250m_16_days_pixel_reliability"]
    assert df["250m_16_days_NDVI"].tolist() == [6500.0, 7200.0, -3000.0]
    assert df["250m_16_days_pixel_reliability"].tolist() == [0, 1, -1]
    assert (df["source"] == "MOD13Q1").all() and df["time"].dtype == "datetime64[ns]"
    rep = ap.scaling_report(df).set_index("column")
    assert rep.loc["250m_16_days_NDVI", "decision"] == ("x10000" if scaled else "none")


def test_ndvi_through_clean_frame(tmp_path):
    df = ap.parse_point_csv(_ndvi_csv(tmp_path, True), "MOD13Q1.061", "MOD13Q1")
    long, _ = clean_frame(df)
    nd = long[long["var"] == "ndvi"]["value"]
    assert len(nd) == 2 and nd.between(0, 1).all()


@pytest.mark.parametrize("scaled", [True, False])
def test_parse_lst(tmp_path, scaled):
    k = (lambda x: x) if scaled else (lambda x: x / 0.02)
    text = ("ID,Latitude,Longitude,Date,MODIS_Tile,MOD11A2_061_Line_Y_1km,MOD11A2_061_Sample_X_1km,"
            "MOD11A2_061_LST_Day_1km,MOD11A2_061_QC_Day,MOD11A2_061_QC_Day_MODLAND_Description\n"
            f"a,42.0,-93.7,2024-06-01,h11v04,1,2,{k(300.5)},0,LST produced\n"
            f"a,42.0,-93.7,2024-06-09,h11v04,1,2,{k(0)},2,Not produced\n")
    df = ap.parse_point_csv(_write(tmp_path, "l-results.csv", text), "MOD11A2.061", "MOD11A2")
    assert list(df.columns)[5:] == ["LST_Day_1km", "QC_Day"]
    assert df["LST_Day_1km"].iloc[0] == pytest.approx(15025.0)
    assert df["LST_Day_1km"].iloc[1] == 0
    long, _ = clean_frame(df)
    c = long[long["var"] == "lst_day_c"]["value"]
    assert len(c) == 1 and c.iloc[0] == pytest.approx(27.35, abs=0.01)


@pytest.mark.parametrize("scaled", [True, False])
def test_parse_et(tmp_path, scaled):
    k = (lambda x: x / 10) if scaled else (lambda x: x)
    text = ("ID,Latitude,Longitude,Date,MODIS_Tile,MOD16A2GF_061_Line_Y_500m,MOD16A2GF_061_Sample_X_500m,"
            "MOD16A2GF_061_ET_500m,MOD16A2GF_061_ET_QC_500m\n"
            f"a,42,-93.7,2024-06-01,h11v04,1,2,{k(250)},0\n"
            f"a,42,-93.7,2024-06-09,h11v04,1,2,{k(800)},0\n"
            f"a,42,-93.7,2024-06-17,h11v04,1,2,{k(32767)},32\n")
    df = ap.parse_point_csv(_write(tmp_path, "e-results.csv", text), "MOD16A2GF.061", "MOD16A2GF")
    assert df["modis_et_8day_mm"].iloc[:2].tolist() == pytest.approx([25.0, 80.0])
    assert math.isnan(df["modis_et_8day_mm"].iloc[2])
    assert "ET_QC_500m" in df.columns


def test_parse_smap_prefix(tmp_path):
    text = ("ID,Latitude,Longitude,Date,SPL4SMGP_008_Geophysical_Data_sm_surface,"
            "SPL4SMGP_008_Geophysical_Data_sm_rootzone\n"
            "a,42,-93.7,2024-06-01 01:30:00,0.31,0.35\na,42,-93.7,2024-06-01 04:30:00,-9999,-9999\n")
    df = ap.parse_point_csv(_write(tmp_path, "s-results.csv", text), "SPL4SMGP.008", "SPL4SMGP")
    assert list(df.columns)[5:] == ["sm_surface", "sm_rootzone"]
    assert df["time"].iloc[0] == pd.Timestamp("2024-06-01 01:30")


def test_slope_math():
    # doğuya doğru her 111.32 m'de 1 m yükselme (ekvator) -> %: dx = 111.32 m toplam adım
    lat = 0.0
    dx = 2 * 0.0005 * 111320.0
    slope, aspect = ap.compute_slope_aspect({"n": 10, "s": 10, "e": 10 + dx * 0.1, "w": 10}, lat)
    assert slope == pytest.approx(10.0)
    assert aspect == pytest.approx(270.0)  # yokuş aşağı batıya bakar
    s2, a2 = ap.compute_slope_aspect({"n": 5, "s": 5, "e": 5, "w": 5}, 42.0)
    assert s2 == 0 and math.isnan(a2)
    s3, a3 = ap.compute_slope_aspect({"n": 11, "s": 10, "e": 10, "w": 10}, 42.0)
    assert a3 == pytest.approx(180.0) and s3 > 0


def test_elevation_table_and_neighbor_points():
    areas = [{"area_id": "a", "lat": 42.0, "lon": -93.0}]
    pts = ap.points_from_areas(areas, with_neighbors=True)
    assert [p["id"] for p in pts] == ["a", "a__n", "a__s", "a__e", "a__w"]
    assert pts[1]["lat"] == pytest.approx(42.0005) and pts[4]["lon"] == pytest.approx(-93.0005)
    df = pd.DataFrame({"area_id": [p["id"] for p in pts], "lat": [p["lat"] for p in pts],
                       "lon": [p["lon"] for p in pts], "SRTMGL1_DEM": [100.0, 101, 99, 100, 100]})
    et = ap.elevation_table(df)
    assert len(et) == 1 and et["elevation_m"].iloc[0] == 100.0
    assert et["slope_pct"].iloc[0] > 0 and et["aspect_deg"].iloc[0] == pytest.approx(180.0)


class FakeApi:
    """AppEEARS HTTP katmanını taklit eder."""

    def __init__(self, tmp_path, fail=()):
        self.tmp, self.fail = tmp_path, set(fail)
        self.submitted, self.polls, self.logged_out, self.seen = [], 0, False, set()

    def install(self, monkeypatch):
        monkeypatch.setattr(ap, "login", lambda: "tok")
        monkeypatch.setattr(ap, "logout", lambda t: setattr(self, "logged_out", True))
        monkeypatch.setattr(ap, "submit_task", self.submit)
        monkeypatch.setattr(ap, "task_status", self.status)
        monkeypatch.setattr(ap, "bundle_files", lambda t, tid: [
            {"file_id": "f1", "file_name": "x/data-MOD13Q1-061-results.csv"},
            {"file_id": "f2", "file_name": "x/readme.txt"}])
        monkeypatch.setattr(ap, "download_file", self.download)

    def submit(self, token, body):
        layer = body["params"]["layers"][0]["product"]
        if layer in self.seen:
            self.fail.discard(layer)  # yeniden gönderilen görev bu kez başarılı olur
        self.seen.add(layer)
        self.submitted.append(layer)
        return "task-" + layer

    def status(self, token, tid):
        self.polls += 1
        return {"status": "error" if tid.removeprefix("task-") in self.fail else "done"}

    def download(self, token, tid, fid, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        _ndvi_csv(dest.parent, True).rename(dest)
        return dest


AREAS = [{"area_id": "iowa", "lat": 42.03, "lon": -93.74}]


def test_run_partial_failure_and_reuse(tmp_path, monkeypatch):
    out = tmp_path / "raw"
    fake = FakeApi(tmp_path, fail={"MYD13Q1.061"})
    fake.install(monkeypatch)
    res = ap.run(AREAS, "2024-06-01", "2024-07-31", out, ["ndvi", "ndvi_aqua"], tmp_path / "st", poll_s=0)
    assert res["ndvi"]["state"] == "done" and res["ndvi"]["rows"] == 3
    assert res["ndvi_aqua"]["state"] == "failed"
    assert (out / "MOD13Q1.parquet").exists() and not (out / "MYD13Q1.parquet").exists()
    assert fake.logged_out
    tasks = json.loads((tmp_path / "appeears_tasks.json").read_text())
    assert len(tasks) == 2
    # tekrar çalıştırma: bitmiş görev yeniden gönderilmez; hatalı olan yeniden gönderilir
    fake.submitted.clear()
    res = ap.run(AREAS, "2024-06-01", "2024-07-31", out, ["ndvi", "ndvi_aqua"], tmp_path / "st", poll_s=0)
    assert fake.submitted == ["MYD13Q1.061"]
    assert all(r["state"] == "done" for r in res.values())
    # farklı tarih aralığı = yeni anahtar
    assert ap.task_key("P", ["L"], ap.points_from_areas(AREAS), "2024-01-01", "2024-02-01") != \
        ap.task_key("P", ["L"], ap.points_from_areas(AREAS), "2024-01-01", "2024-03-01")


def test_build_task_body_and_presets():
    b = ap.build_task_body("t", "2024-06-01", "2024-07-31", ap._layer_objs("MOD11A2.061", ["LST_Day_1km"]),
                           ap.points_from_areas(AREAS))
    assert b["task_type"] == "point"
    assert b["params"]["dates"] == [{"startDate": "06-01-2024", "endDate": "07-31-2024"}]
    assert b["params"]["coordinates"][0]["id"] == "iowa"
    for name in ("ndvi", "ndvi_aqua", "lst", "et", "smap", "elev", "hls"):
        assert name in ap.PRESETS
