import asyncio
import datetime as dt
from pathlib import Path

import pytest

from app.agents import load_agents
from app.geo import parse_polygon
from app.ml import FakeMLProvider, Layer, run_providers

PARCEL = parse_polygon(
    {"type": "Polygon", "coordinates": [[[29, 41], [29.01, 41], [29.01, 41.01], [29, 41.01], [29, 41]]]}
)
DAY = dt.date(2026, 5, 1)


def test_fake_provider_is_deterministic_and_sane():
    p = FakeMLProvider()
    a = asyncio.run(p.analyze(PARCEL, DAY))
    b = asyncio.run(p.analyze(PARCEL, DAY))
    assert a == b
    assert {layer.name for layer in a} == {"yagis", "toprak", "nem"}
    for layer in a:
        assert layer.min <= layer.mean <= layer.max
        assert 0 <= layer.confidence <= 1
    assert asyncio.run(p.analyze(PARCEL, dt.date(2026, 5, 2))) != a  # tarih değişince sonuç değişir


def test_layer_summary_is_compact_text():
    layer = Layer("nem", "Toprak nemi", "%", 30.0, 25.0, 35.0, 0.8, "not")
    assert layer.summary() == "Toprak nemi: ortalama 30.0 % (aralık 25.0–35.0), eminlik yüksek (%80). not"


@pytest.mark.parametrize("conf, level", [(0.95, "yüksek"), (0.8, "yüksek"), (0.79, "orta"), (0.6, "orta"), (0.59, "düşük"), (0.0, "düşük")])
def test_confidence_levels(conf, level):
    assert Layer("x", "X", "u", 1, 0, 2, conf).confidence_level == level


class Boom:
    name = "boom"

    async def analyze(self, parcel, date):
        raise RuntimeError("iç hata")


def test_one_failing_provider_keeps_the_others():
    layers, errors = asyncio.run(run_providers([FakeMLProvider(), Boom()], PARCEL, DAY))
    assert len(layers) == 3
    assert errors == ["boom sonuç üretemedi"]


def test_default_agents_file_is_valid():
    agents = load_agents(Path(__file__).resolve().parents[1] / "agents.toml")
    assert len(agents) == 2
    # gerçek ML (ml/service.py to_layers) katman adları
    real = {"ndvi_tahmin", "risk", "iklim", "rotasyon_1", "rotasyon_2", "rotasyon_3", "rotasyon_mevcut",
            "rotasyon_secilen", "hava_tahmini", "yagis_tahmini", *(f"rotasyon_ozel_{i}" for i in range(1, 6))}
    for a in agents:
        assert set(a.layers) | set(a.optional_layers) <= real
    assert agents[0].layers == ("ndvi_tahmin", "risk", "iklim")
    assert agents[0].optional_layers == ("hava_tahmini", "yagis_tahmini")
    assert agents[1].optional_layers[:2] == ("rotasyon_mevcut", "rotasyon_secilen")


@pytest.mark.parametrize(
    "content, msg",
    [
        ("", "en az bir"),
        ('[[agent]]\nid="a"\nname="x"\nlayers=["l"]\n', "system_prompt"),
        ('[[agent]]\nid="a"\nname="x"\nsystem_prompt="p"\nlayers=[]\n', "boş"),
        ('[[agent]]\nid="a"\nname="x"\nsystem_prompt="p"\nlayers=["l"]\n'
         '[[agent]]\nid="a"\nname="y"\nsystem_prompt="p"\nlayers=["l"]\n', "benzersiz"),
    ],
)
def test_invalid_agent_config(tmp_path, content, msg):
    f = tmp_path / "agents.toml"
    f.write_text(content)
    with pytest.raises(ValueError, match=msg):
        load_agents(f)


def test_layer_dict_carries_confidence_level_for_the_frontend():
    d = Layer("nem", "Toprak nemi", "%", 30.0, 25.0, 35.0, 0.65).to_dict()
    assert d["confidence_level"] == "orta" and d["confidence"] == 0.65
    assert set(d) >= {"name", "label", "unit", "mean", "min", "max", "confidence", "confidence_level", "note"}
