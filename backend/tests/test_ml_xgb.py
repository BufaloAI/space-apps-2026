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
    assert client.calls == {"power": 1, "ndvi": 1}
    start(client, sid)  # aynı gün aynı parsel -> önbellek
    assert client.calls == {"power": 1, "ndvi": 1}

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
