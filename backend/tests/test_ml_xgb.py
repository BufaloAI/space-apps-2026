"""Gerçek ML yolu (ML_PROVIDER=xgboost): sentetik eğitilmiş küçük model + ağ çağrıları sahte.

Model ve veri SENTETİK (ml.data_load.sample); amaç uçtan uca entegrasyonu denemek.
"""
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml.data_load import download  # noqa: E402
from ml.data_load.build import build, load_config  # noqa: E402
from ml.data_load.sample import SAMPLE_AREAS, make_sample  # noqa: E402

IOWA = {  # ~16 ha, ia_story sentetik alanının yakını
    "type": "Polygon",
    "coordinates": [[[-93.61, 42.02], [-93.60, 42.02], [-93.60, 42.03], [-93.61, 42.03], [-93.61, 42.02]]],
}


@pytest.fixture(scope="module")
def model_dir(tmp_path_factory):
    from ml.model import train

    d = tmp_path_factory.mktemp("xgb")
    raw = d / "SYNTHETIC_raw.csv"
    make_sample("2024-01-01", "2025-12-31", seed=5).to_csv(raw, index=False)
    areas = d / "areas.json"
    areas.write_text(json.dumps([{"area_id": a, "lat": la, "lon": lo, "radius_deg": 0.2}
                                 for a, la, lo, _ in SAMPLE_AREAS]))
    build([str(raw)], str(d / "f.parquet"), str(areas), load_config())
    cfg = load_config()
    cfg["model"]["num_boost_round"] = 200
    return train.run(str(d / "f.parquet"), cfg, out_dir=d / "run", set_latest=False)


@pytest.fixture(scope="module")
def fake_raw():
    """ia_story alanının sentetik ham verisi, indiricinin döndüreceği biçimde."""
    df = make_sample("2024-01-01", "2025-12-31", seed=5)
    df = df[(df["lat"].between(41.9, 42.2)) & (df["lon"].between(-93.8, -93.4))].copy()
    df["time"] = df["time"].astype("datetime64[ns]")
    power = df[df["T2M"].notna()][["time", "lat", "lon", "T2M", "T2M_MAX", "T2M_MIN"]].copy()
    rain = df[df["precipitation"].notna()].groupby("time", as_index=False)["precipitation"].mean()
    power = power.merge(rain.rename(columns={"precipitation": "PRECTOTCORR"}), on="time", how="left")
    ndvi = df[df["250m 16 days NDVI"].notna()].groupby("time", as_index=False)[
        ["250m 16 days NDVI", "250m 16 days pixel reliability"]].median()
    ndvi = ndvi.rename(columns={"250m 16 days NDVI": "250m_16_days_NDVI",
                                "250m 16 days pixel reliability": "250m_16_days_pixel_reliability"})
    for f, src in ((power, "POWER"), (ndvi, "MOD13Q1")):
        f.insert(0, "source", src)
        f.insert(1, "area_id", "parcel")
        f["lat"], f["lon"] = 42.025, -93.605
    return power, ndvi


class EchoLLM:
    def __init__(self):
        self.calls = []

    async def stream(self, messages):
        self.calls.append(messages)
        yield "tamam"

    async def close(self):
        pass


@pytest.fixture
def client(monkeypatch, model_dir, fake_raw, tmp_path):
    power, ndvi = fake_raw
    calls = {"power": 0, "ndvi": 0}

    def fp(*a, **k):
        calls["power"] += 1
        return power.copy()

    def fn(*a, **k):
        calls["ndvi"] += 1
        return ndvi.copy()

    monkeypatch.setattr(download, "fetch_power", fp)
    monkeypatch.setattr(download, "fetch_modis_ornl", fn)
    from ml.data_load import planetary

    def fpc(*a, **k):  # Planetary Computer (birincil NDVI kaynağı) da ağa çıkmasın
        calls["planetary"] = calls.get("planetary", 0) + 1
        if calls.get("planetary_down"):
            raise RuntimeError("planetary kapalı")
        return ndvi.copy()

    monkeypatch.setattr(planetary, "fetch_area", fpc)
    from ml import service as S

    static = {"soil": {"om_pct": 3.5, "clay_pct": 21, "sand_pct": 45, "slope_pct": 3, "hydgrp": "B",
                       "drainagecl": "Well drained", "soil_texture": "loam"},
              "cdl": {"2020": "corn", "2021": "soybean", "2022": "corn", "2023": "soybean", "2024": "corn",
                      "2025": "soybean"},
              "rotation": {"rotation": "corn>soybean", "confidence": 1.0}, "errors": []}
    monkeypatch.setattr(S.ParcelService, "fetch_soil", lambda self, lat, lon, today: {"soil": static["soil"],
                                                                                      "errors": []})  # ağa çıkma
    monkeypatch.setattr(S.ParcelService, "fetch_cdl", lambda self, lat, lon, today: {
        "cdl": static["cdl"], "rotation": static["rotation"], "errors": []})
    monkeypatch.setenv("VLLM_API_KEY", "test-key")
    monkeypatch.setenv("ML_PROVIDER", "xgboost")
    monkeypatch.setenv("ML_MODEL_DIR", str(model_dir))
    monkeypatch.setenv("ML_CACHE_DIR", str(tmp_path / "parcels"))
    monkeypatch.setenv("AGENTS_FILE", str(ROOT / "backend" / "agents.toml"))
    from app.main import app

    with TestClient(app) as c:
        c.app.state.llm = EchoLLM()
        c.calls = calls
        yield c


def events(client, job_id, sid):
    out = []
    with client.stream("GET", f"/api/jobs/{job_id}/events", params={"session_id": sid}) as r:
        for line in r.iter_lines():
            if line.startswith("data: "):
                out.append(json.loads(line[6:]))
    return out


def start(client, sid=None, **extra):
    sid = sid or client.post("/api/session").json()["session_id"]
    r = client.post("/api/analysis", json={"session_id": sid, "polygon": IOWA, **extra})
    assert r.status_code == 202, r.text
    return sid, events(client, r.json()["job_id"], sid)


def test_analysis_produces_real_layers_and_progress(client):
    sid, ev = start(client, existing_rotation="corn>corn", priorities={"water": 2},
                    profile={"irrigation": False, "slope_pct": 3})
    progress = [e["progress"] for e in ev if e.get("status") == "progress"]
    assert any(p["stage"] == "data" for p in progress) and any(p["stage"] == "model" for p in progress)
    done = next(e for e in ev if e.get("stage") == "ml" and e.get("status") == "done")
    names = [lay["name"] for lay in done["layers"]]
    for n in ("ndvi_tahmin", "risk", "iklim", "rotasyon_1", "rotasyon_2", "rotasyon_3", "rotasyon_mevcut"):
        assert n in names, names
    nd = done["layers"][0]
    assert nd["min"] <= nd["mean"] <= nd["max"] and nd["confidence_level"] == "düşük"  # sentetik model
    assert "_meta" in nd["details"] and nd["details"]["_meta"]["warnings"] is not None
    mevcut = next(lay for lay in done["layers"] if lay["name"] == "rotasyon_mevcut")
    assert mevcut["details"]["key"] == "corn_corn"
    assert any(d["risk"].startswith("zararlı") for d in mevcut["details"]["damages"])  # monokültür zararı
    assert "Olası zararlar" in mevcut["note"]
    json.dumps(done)  # NaN yok, geçerli JSON
    assert "NaN" not in json.dumps(done)
    # botlar: a iklim/risk katmanlarını, b rotasyonları görür; ikisi de veri sınırları notunu alır
    msgs = {m[0]["content"].split("'")[1]: m[1]["content"] for m in client.app.state.llm.calls}
    assert "Rotasyon önerisi #1" in msgs["Rotasyon Danışmanı"] and "Mevcut rotasyon" in msgs["Rotasyon Danışmanı"]
    assert "Genel risk skoru" in msgs["Risk ve İklim Analisti"]
    assert "Rotasyon önerisi" not in msgs["Risk ve İklim Analisti"]
    assert ev[-1] == {"done": True}


def test_cache_reused_and_rescore_updates_bots(client):
    sid, _ = start(client)
    # POWER iki kez: parsel verisi + hava görünümü için uzun geçmiş. NDVI Planetary'den, ORNL'ye gerek yok
    assert client.calls == {"power": 2, "ndvi": 0, "planetary": 1}
    start(client, sid)  # aynı gün aynı parsel -> önbellek (görünüm dahil)
    assert client.calls == {"power": 2, "ndvi": 0, "planetary": 1}

    r = client.post("/api/rotations", json={"session_id": sid, "chosen_rotation": "sorghum>soybean>winter_wheat",
                                            "existing_rotation": "corn>soybean"})
    assert r.status_code == 200, r.text
    names = [lay["name"] for lay in r.json()["layers"]]
    assert "rotasyon_secilen" in names and "rotasyon_mevcut" in names
    secilen = next(lay for lay in r.json()["layers"] if lay["name"] == "rotasyon_secilen")
    assert secilen["details"]["key"] == "sorghum_soy_wheat" and "delta_vs_existing" in secilen["details"]

    llm = client.app.state.llm
    llm.calls.clear()
    j = client.post("/api/ask", json={"session_id": sid, "message": "Seçtiğim rotasyon nasıl?", "agent": "b"})
    events(client, j.json()["job_id"], sid)
    q = llm.calls[0][-1]["content"]
    assert q.startswith("[Güncellenmiş veri]") and "Çiftçinin seçtiği rotasyon" in q
    # güncelleme bir kez eklenir
    llm.calls.clear()
    j = client.post("/api/ask", json={"session_id": sid, "message": "Peki ya su?", "agent": "b"})
    events(client, j.json()["job_id"], sid)
    assert llm.calls[0][-1]["content"] == "Peki ya su?"


def test_validation_and_catalog(client):
    cat = client.get("/api/rotations/catalog").json()
    assert {c["id"] for c in cat["crops"]} >= {"corn", "soybean", "cereal_rye"}
    assert any(p["id"] == "water" for p in cat["priorities"])
    sid = client.post("/api/session").json()["session_id"]
    r = client.post("/api/analysis", json={"session_id": sid, "polygon": IOWA, "priorities": {"luck": 1}})
    assert r.status_code == 422
    r = client.post("/api/rotations", json={"session_id": sid})
    assert r.status_code == 409  # önce analiz
    _, ev = start(client, sid, existing_rotation="corn>banana")
    assert ev[-1]["error"].startswith("Geçersiz rotasyon girdisi")


def test_catalog_unavailable_with_fake_provider(monkeypatch):
    monkeypatch.setenv("VLLM_API_KEY", "test-key")
    monkeypatch.setenv("ML_PROVIDER", "fake")
    from app.main import app

    with TestClient(app) as c:
        assert c.get("/api/rotations/catalog").status_code == 503


def test_cache_key_keeps_lon_and_date(tmp_path, model_dir):
    import datetime as dt

    from ml.service import ParcelService

    svc = ParcelService(model_dir=model_dir, cache_dir=tmp_path)
    a = svc._cache_paths(40.505, -88.985, dt.date(2026, 11, 14))[0]
    b = svc._cache_paths(40.505, -88.105, dt.date(2026, 11, 14))[0]
    c = svc._cache_paths(40.505, -88.985, dt.date(2026, 11, 15))[0]
    assert a.name == "40.505_-88.985_2026-11-14.parquet"
    assert len({a, b, c}) == 3  # farklı boylam ve farklı gün çakışmaz
    for p in (a, tmp_path / "40.505_-88.parquet"):  # güncel dosya kalır, eski tarihsiz ad silinir
        p.write_text("x")
    svc._prune_cache(dt.date(2026, 11, 14))
    assert a.exists() and not (tmp_path / "40.505_-88.parquet").exists()


def test_existing_rotation_auto_from_cdl(client):
    _, ev = start(client)  # çiftçi mevcut rotasyon vermedi -> CDL'den
    done = next(e for e in ev if e.get("stage") == "ml" and e.get("status") == "done")
    mevcut = next(lay for lay in done["layers"] if lay["name"] == "rotasyon_mevcut")
    assert mevcut["details"]["key"] == "corn_soy"
    meta = done["layers"][0]["details"]["_meta"]
    assert meta["field_info"]["existing_rotation_source"] == "USDA CDL"
    assert meta["field_info"]["soil"]["soil_texture"] == "loam"
    assert any("CDL" in w for w in meta["warnings"])


def test_ndvi_falls_back_to_ornl_when_planetary_down(client):
    client.calls["planetary_down"] = True
    _, ev = start(client)
    prov = [e["progress"].get("provider") for e in ev if e.get("status") == "progress"
            and e["progress"].get("source") == "ndvi" and e["progress"].get("status") == "done"]
    assert prov == ["ornl"] and client.calls["ndvi"] == 1
    done = next(e for e in ev if e.get("stage") == "ml" and e.get("status") == "done")
    assert any(lay["name"] == "ndvi_tahmin" for lay in done["layers"])


def test_slow_cdl_does_not_block_soil(model_dir, tmp_path, monkeypatch):
    import datetime as dt
    import time as _t

    from ml.service import ParcelService

    svc = ParcelService(model_dir=model_dir, cache_dir=tmp_path)
    svc.static_wait_s = 0.5
    monkeypatch.setattr(ParcelService, "fetch_soil", lambda self, *a: {"soil": {"om_pct": 3.0}, "errors": []})
    monkeypatch.setattr(ParcelService, "fetch_cdl", lambda self, *a: _t.sleep(3) or {"cdl": {}, "rotation": None,
                                                                                     "errors": []})
    t0 = _t.monotonic()
    out = svc.fetch_static(42.0, -93.6, dt.date(2026, 11, 15))
    assert _t.monotonic() - t0 < 2  # CDL'yi beklemedi
    assert out["soil"] == {"om_pct": 3.0} and any("CDL" in e for e in out["errors"])


def test_ndvi_label():
    from ml.service import ndvi_label

    assert "çok seyrek" in ndvi_label(0.27) and "yoğun, sağlıklı" in ndvi_label(0.85)


LENTIL = {"id": "custom_lentil", "name": "Mercimek", "family": "fabaceae", "season": "cool", "water_need": 0.3,
          "drought_tol": 0.7, "n_fix": True, "market": 0.75, "yield_t_ha": 1.6, "price_per_t": 30000,
          "cost_per_ha": 20000}


def test_farmer_crops_and_rotations_are_scored(client):
    sid, ev = start(client, custom_crops=[LENTIL],
                    custom_rotations=[{"name": "Mısır-mercimek", "seq": "corn>custom_lentil"}])
    done = next(e for e in ev if e.get("stage") == "ml" and e.get("status") == "done")
    ozel = next(lay for lay in done["layers"] if lay["name"] == "rotasyon_ozel_1")
    assert ozel["details"]["source"] == "çiftçi" and "Mercimek" in " ".join(ozel["details"]["sequence_names"])
    msgs = {m[0]["content"].split("'")[1]: m[1]["content"] for m in client.app.state.llm.calls}
    assert "Çiftçinin oluşturduğu rotasyon" in msgs["Rotasyon Danışmanı"]
    # yeniden puanlamada ürün listesi değişebilir; ürünü kaldırınca onu kullanan rotasyon da gönderilmemeli
    r = client.post("/api/rotations", json={"session_id": sid, "custom_crops": [LENTIL],
                                            "custom_rotations": [{"name": "Mercimek-sorgum",
                                                                  "seq": "custom_lentil>sorghum"}]})
    assert r.status_code == 200, r.text
    assert any(lay["details"]["name"] == "Mercimek-sorgum" for lay in r.json()["layers"])
    bad = client.post("/api/rotations", json={"session_id": sid, "custom_rotations": [{"name": "x",
                                                                                        "seq": "corn>teff"}],
                                              "custom_crops": []})
    assert bad.status_code == 422 and "teff" in bad.json()["detail"]


def test_custom_crop_schema_rejected(client):
    sid = client.post("/api/session").json()["session_id"]
    r = client.post("/api/analysis", json={"session_id": sid, "polygon": IOWA,
                                           "custom_crops": [{"id": "Bad Id", "name": "x"}]})
    assert r.status_code == 422


def test_catalog_exposes_crop_fields(client):
    cat = client.get("/api/rotations/catalog").json()
    assert "water_need" in cat["crop_fields"]["floats_0_1"] and "yield_t_ha" in cat["crop_fields"]["economics"]
    corn = next(c for c in cat["crops"] if c["id"] == "corn")
    assert 0 <= corn["profile"]["water_need"] <= 1


def test_english_mode_and_crop_suggestions(client):
    sid, ev = start(client, lang="en")
    systems = [m[0]["content"] for m in client.app.state.llm.calls]
    assert all("answer in clear, short English" in s for s in systems)
    data = [m[1]["content"] for m in client.app.state.llm.calls]
    assert all(d.startswith("Parcel: about") and d.endswith("in English.") for d in data)
    assert all("Mısır" not in d and "Soya" not in d for d in data)  # rotasyon verisi İngilizce ürün adlarıyla
    assert any("Rotation suggestion #1" in d for d in data) and any("confidence" in d for d in data)
    done = next(e for e in ev if e.get("stage") == "ml" and e.get("status") == "done")
    meta = done["layers"][0]["details"]["_meta"]
    assert meta["disclaimer_en"].startswith("This ranking") and meta["information_gaps_en"]
    rot = next(lay for lay in done["layers"] if lay["name"] == "rotasyon_1")["details"]
    assert rot["name_en"] and rot["label_en"].endswith("(0-100 points)") and rot["sequence_names_en"]
    assert all("risk_en" in d and "impact_en" in d for d in rot["damages"])
    ask = client.post("/api/ask", json={"session_id": sid, "message": "Why?", "lang": "en"})
    assert ask.status_code == 202
    events(client, ask.json()["job_id"], sid)
    assert client.app.state.llm.calls[-1][-1]["content"].endswith("Answer in English.")

    sug = client.get("/api/crops/suggestions", params={"session_id": sid, "exclude": "corn,barley"})
    assert sug.status_code == 200, sug.text
    items = sug.json()
    assert items and all(i["id"] not in ("corn", "barley") for i in items)
    assert items == sorted(items, key=lambda i: -i["fit"]) and all(i["name_en"] for i in items)
    agents = client.get("/api/agents").json()
    assert agents[0]["name_en"] == "Risk & Climate Analyst"


def test_suggestions_need_analysis(client):
    sid = client.post("/api/session").json()["session_id"]
    assert client.get("/api/crops/suggestions", params={"session_id": sid}).status_code == 409


def test_catalog_has_library_and_english_names(client):
    cat = client.get("/api/rotations/catalog").json()
    lib = {c["id"]: c for c in cat["library"]}
    assert "lentil" in lib and lib["lentil"]["name_en"] == "Lentil" and lib["lentil"]["profile"]["n_fix"] is True
    assert all(c["name_en"] for c in cat["crops"]) and all(r["name_en"] for r in cat["rotations"])
    assert all(p["label_en"] for p in cat["priorities"])
