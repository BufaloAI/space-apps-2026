"""2026-11-15 eklemeleri: hava görünümü, küresel toprak/eğim hesabı, çiftçi ürünleri, rotasyon üretimi."""
import math

import numpy as np
import pandas as pd
import pytest

from ml.data_load import soil_global as SG
from ml.model import outlook as OL
from ml.model import risk as R
from ml.model import rotation as ROT


def _synthetic_daily(years=10, persistence=0.9, seed=1):
    """Mevsimsel sinüs + güçlü kalıcı (AR1) anomali: modelin öğrenebileceği bir sinyal."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2015-01-01", periods=365 * years, freq="D")
    season = 12 + 12 * np.sin(2 * np.pi * (idx.day_of_year - 110) / 365)
    a = np.zeros(len(idx))
    for i in range(1, len(idx)):
        a[i] = persistence * a[i - 1] + rng.normal(0, 1.5)
    t = season + a
    d = pd.DataFrame({"t2m": t, "tmax": t + 6, "tmin": t - 6,
                      "precip": rng.gamma(0.4, 4, len(idx))}, index=idx)
    return OL.derive(d)


def test_outlook_shape_bounds_and_skill():
    o = OL.outlook(_synthetic_daily())
    assert o is not None and len(o["weeks"]) == 4
    for w in o["weeks"]:
        for var, v in w["vars"].items():
            assert v["p10"] <= v["p50"] <= v["p90"], (var, v)
        assert 0 <= w["vars"]["frost_days"]["p50"] <= 7
        assert w["vars"]["precip"]["p10"] >= 0
    # güçlü kalıcı anomalide 1. hafta becerisi belirgin, 4. haftada daha düşük
    assert o["skill"]["t2m"][0] > 0.2
    assert o["skill"]["t2m"][3] < o["skill"]["t2m"][0]
    cov = o["interval_coverage"]["t2m"][0]
    assert 0.6 <= cov <= 0.95  # ayrı yarıda ölçüldü, tam 0.80 olmak zorunda değil
    assert 0.3 <= OL.confidence(o, "t2m") <= 0.9


def test_outlook_needs_history():
    assert OL.outlook(_synthetic_daily(years=3)) is None


def test_outlook_power_fill_values_are_nan():
    df = pd.DataFrame({"time": pd.date_range("2020-01-01", periods=3), "T2M": [1.0, -999.0, 3.0],
                       "T2M_MAX": [5.0, 6.0, 7.0], "T2M_MIN": [-1.0, -2.0, -3.0], "PRECTOTCORR": [0.0, 1.0, -999.0]})
    d = OL.daily_from_power(df)
    assert math.isnan(d["t2m"].iloc[1]) and math.isnan(d["precip"].iloc[2])
    assert d["frost_days"].tolist() == [1.0, 1.0, 1.0]


def test_risk_uses_outlook_frost():
    row = pd.Series({"week": pd.Timestamp("2026-04-06"), "frost_day_4w": 0.0, "heat_day_4w": 0.0})
    base = R.short_term_risk(row, None, None, None)
    o = {"weeks": [{"vars": {"frost_days": {"p50": 2.0}, "heat_days": {"p50": 0.0},
                             "precip": {"p50": 1.0, "normal": 20.0}}},
                   {"vars": {"frost_days": {"p50": 1.0}, "heat_days": {"p50": 0.0},
                             "precip": {"p50": 1.0, "normal": 20.0}}}]}
    ahead = R.short_term_risk(row, None, None, None, outlook=o)
    assert base["components"]["frost_risk"]["score"] == 0.0
    assert ahead["components"]["frost_risk"]["score"] == 1.0  # 3 donlu gün bekleniyor, mevsim içi
    assert ahead["components"]["water_stress"]["score"] > 0.8  # normalin çok altında yağış bekleniyor


def test_saxton_rawls_loam_matches_published_values():
    r = SG.saxton_rawls(40, 20, 2.5)  # Saxton & Rawls (2006) tablo 3, tın: θ1500≈0.13, θ33≈0.28
    assert abs(r["theta1500"] - 0.13) < 0.02 and abs(r["theta33"] - 0.28) < 0.02
    assert 2 < r["ksat_um_s"] < 8
    sand = SG.saxton_rawls(88, 5, 1.0)
    assert sand["ksat_um_s"] > r["ksat_um_s"] and sand["awc_cm_cm"] < r["awc_cm_cm"]
    assert SG.hydgrp_from_ksat(sand["ksat_um_s"]) in ("A", "B")


def test_slope_horn_plane():
    step = 90.0
    z = [[r * -0.05 * step + 100 for _ in range(3)] for r in range(3)]  # kuzeyden güneye %5 iniş
    assert abs(SG.slope_horn(z, step) - 5.0) < 1e-6
    assert SG.slope_horn([[7.0] * 3] * 3, step) == 0.0


def test_estimate_marks_values_as_estimated(monkeypatch, tmp_path):
    monkeypatch.setattr(SG, "soilgrids_point", lambda lat, lon: {"clay": 30.0, "sand": 30.0, "soc": 12.0,
                                                                  "phh2o": 7.2})
    monkeypatch.setattr(SG, "dem_slope_pct", lambda lat, lon: 4.2)
    out = SG.estimate(39.5, 32.1, tmp_path)
    s = out["soil"]
    assert s["estimated"] and s["slope_pct"] == 4.2 and s["soil_texture"] == "clay_loam"
    assert abs(s["om_pct"] - 12.0 * 1.724 / 10) < 0.01
    assert "slope_pct" in s["estimated_keys"] and (tmp_path / "39.500_32.100.json").exists()


COND_DRY = {"water_stress": 0.7, "heat_stress": 0.5, "frost_stress": 0.1, "annual_gdd": 1600.0, "wetness": 0.0,
            "erosion_pressure": 0.2, "years": [2023, 2024, 2025]}
COND_WET = {**COND_DRY, "water_stress": 0.1, "annual_gdd": 1700.0, "wetness": 0.3}
LENTIL = {"id": "custom_lentil", "name": "Mercimek", "family": "fabaceae", "season": "cool", "water_need": 0.3,
          "drought_tol": 0.7, "heat_tol": 0.4, "frost_tol": 0.6, "n_fix": True, "residue_cover": 0.3, "om": 0.3,
          "market": 0.75, "forage": 0.3, "n_need": 0.1, "wet_tol": 0.6,
          "yield_t_ha": 1.6, "price_per_t": 30000, "cost_per_ha": 20000}


def test_generated_candidates_follow_rules():
    cat = ROT.build_catalog([LENTIL])
    gen = ROT.generate_candidates(cat, COND_WET, {})
    assert len(gen) > 20
    for _name, seq in gen.values():
        fams = [cat["crops"][s.split("+")[0]]["family"] for s in seq]
        assert all(fams[i] != fams[(i + 1) % len(fams)] for i in range(len(fams)))
    assert any("+" in "".join(seq) for _n, seq in gen.values())  # nemli alanda örtülü sürümler var
    dry = ROT.generate_candidates(cat, COND_DRY, {"irrigation": 0})
    assert not any("+" in "".join(seq) for _n, seq in dry.values())  # kurak + sulamasız: örtü bitkisi yok
    assert any("custom_lentil" in seq for _n, seq in dry.values())  # çiftçi ürünü adaylara giriyor


def test_recommend_with_farmer_crop_rotation_and_economics():
    wheat = {**ROT.catalog()["crops"]["winter_wheat"], "id": "winter_wheat", "yield_t_ha": 4.0,
             "price_per_t": 9000, "cost_per_ha": 18000}
    r = ROT.recommend(COND_DRY, "wheat_fallow", profile={"irrigation": 0, "soil_om_pct": 1.9,
                                                       "_estimated": ["soil_om_pct"]},
                      extra_crops=[LENTIL, wheat],
                      extra_rotations=[{"name": "Buğday-mercimek", "seq": "winter_wheat>custom_lentil"}])
    assert r["n_generated"] > 0 and r["custom"] and r["custom"][0]["source"] == "çiftçi"
    mine = r["custom"][0]
    assert mine["name"] == "Buğday-mercimek" and "delta_vs_existing" in mine
    e = mine["economics"]
    assert len(e["per_season"]) == 2 and e["per_season"][0]["gross_margin"] < 4.0 * 9000 - 18000  # kayıp düşüldü
    picks = [ROT._mains_key(p["sequence"]) for p in r["recommended"]]
    assert len(set(picks)) == len(picks)  # öneriler birbirinin örtülü kopyası değil
    assert any("hesaplandı" in g for g in r["information_gaps"])


def test_custom_crop_validation():
    with pytest.raises(ValueError):
        ROT.normalize_crop({"id": "bad id!", "name": "x"})
    c = ROT.normalize_crop({"id": "teff", "name": "Teff", "water_need": 7})
    assert c["water_need"] == 1.0 and c["drought_tol"] == 0.5 and c["custom"]
    with pytest.raises(ValueError):
        ROT.parse_rotation("corn>teff")  # katalogda yok
    assert ROT.parse_rotation("corn>teff", ROT.build_catalog([{"id": "teff", "name": "Teff"}])) == ["corn", "teff"]


def test_picks_are_distinct_and_keep_a_known_rotation():
    for cond in (COND_WET, COND_DRY):
        r = ROT.recommend(cond, profile={"irrigation": 0})
        sets = [frozenset(x.split("+")[0] for x in p["sequence"]) for p in r["recommended"]]
        assert len(r["recommended"]) == 3 and len(set(sets)) == len(sets)
        assert any(p["source"] == "katalog" for p in r["recommended"])


def test_bilingual_strings_survive_and_library_suggestions():
    import copy
    import json

    from ml.model.i18n import Bi, en

    b = Bi("merhaba", "hello")
    assert b == "merhaba" and en(b) == "hello" and en(copy.deepcopy(b)) == "hello" and json.dumps(b) == '"merhaba"'
    lib = ROT.library()
    assert len(lib) >= 15 and not set(lib) & set(ROT.catalog()["crops"])
    sug = ROT.suggest_crops(COND_DRY, {"irrigation": 0}, exclude={"chickpea"})
    ids = [s["id"] for s in sug]
    assert "chickpea" not in ids and "rice" not in ids  # çeltik kurak + sulamasız alana önerilmez
    assert all(s["fit"] >= 0.5 for s in sug) and all(s["name_en"] for s in sug)
    wet = ROT.suggest_crops(COND_WET, {"irrigation": 1})
    assert wet and wet[0]["fit"] >= wet[-1]["fit"]
