"""ml/ katmanı testleri. Veriler SENTETİK (ml.data_load.sample)."""
import json

import numpy as np
import pandas as pd
import pytest

from ml.data_load import clean, features as F, prune as P, registry as reg
from ml.data_load.build import build, load_config
from ml.data_load.sample import make_sample, SAMPLE_AREAS


# ------------------------------------------------------------ registry / clean
def test_normalize_and_resolve_hdf_names():
    assert reg.normalize_name("250m 16 days NDVI") == "250m_16_days_ndvi"
    assert reg.resolve("250m 16 days NDVI").spec.canonical == "ndvi"
    assert reg.resolve("PRECTOTCORR").spec.canonical == "precip_mm"
    assert reg.is_meta("precipitation_cnt") and reg.is_meta("time_bnds") and reg.is_meta("zaman_formatli")
    assert not reg.is_meta("sm_surface")


def _raw(**cols):
    base = {"time": ["2024-06-03 00:00:00"] * 3, "lat": [42.0] * 3, "lon": [-93.6] * 3}
    base.update(cols)
    return pd.DataFrame(base)


def test_fill_values_become_nan_and_scale_applied():
    df = _raw(**{"250m 16 days NDVI": [8000, -3000, 5000],
                 "250m 16 days pixel reliability": [0, 0, 3]})
    long, rep = clean.clean_frame(df, grid_deg=1)
    nd = long[long["var"] == "ndvi"]["value"].to_numpy()
    # -3000 dolgu ve bulutlu (rel=3) piksel atılır; 8000 -> 0.8
    assert np.allclose(nd, [0.8])
    assert rep.fills_to_nan["ndvi"] == 1 and rep.qa_masked["ndvi"] == 1


def test_lst_scale_kelvin_to_celsius_and_qc():
    df = _raw(LST_Day_1km=[15000, 0, 15000], QC_Day=[0, 2, 3])
    long, _ = clean.clean_frame(df, grid_deg=1)
    v = long[long["var"] == "lst_day_c"]["value"].to_numpy()
    assert np.allclose(v, [15000 * 0.02 - 273.15])


def test_generic_unknown_column_fill():
    df = _raw(mystery_var=[1.5, -9999.0, -999.0])
    long, _ = clean.clean_frame(df, grid_deg=1)
    assert long[long["var"] == "mystery_var"]["value"].tolist() == [1.5]


def test_time_from_zaman_formatli_and_yil_columns():
    df = pd.DataFrame({"lat": [1.0, 1.0], "lon": [2.0, 2.0], "zaman_formatli": ["2024.03.05.06", "2024.03.05.18"],
                       "T2M": [10.0, 12.0]})
    long, _ = clean.clean_frame(df, grid_deg=1)
    assert sorted(long["time"].dt.hour.tolist()) == [6, 18]
    df2 = pd.DataFrame({"lat": [1.0], "lon": [2.0], "yil": [2024], "ay": [3], "gun": [5], "saat": [0], "T2M": [1.0]})
    long2, _ = clean.clean_frame(df2, grid_deg=1)
    assert long2["time"].iloc[0] == pd.Timestamp("2024-03-05")


def test_subdaily_hour_filter_keeps_0_6_12_18():
    t = pd.date_range("2024-01-01 01:30", periods=8, freq="3h")  # 01:30, 04:30, ..., 22:30
    df = pd.DataFrame({"time": t, "lat": 1.0, "lon": 2.0, "sm_surface": 0.2})
    long, rep = clean.clean_frame(df, grid_deg=1)
    # 01:30->00, 07:30->06, 13:30->12, 19:30->18; 04:30/10:30/16:30 elenir.
    # 22:30, ertesi günün 00'ına en yakın tek örnek olduğu için kalır.
    assert sorted(long["time"].dt.hour.unique()) == [1, 7, 13, 19, 22]
    assert rep.hour_filtered_rows == 3
    # iki ayrı piksel bağımsız seçilir; günlük veri (00:00) hiç elenmez
    daily = pd.DataFrame({"time": pd.date_range("2024-01-01", periods=5, freq="D"), "lat": 1.0, "lon": 2.0,
                          "T2M": 1.0})
    assert len(clean.clean_frame(daily, grid_deg=1)[0]) == 5


def test_nv_duplicates_averaged_and_area_assignment():
    df = pd.DataFrame({"time": ["2024-01-01"] * 4, "lat": [42.0, 42.0, 10.0, 10.0], "lon": [-93.6] * 2 + [5.0] * 2,
                       "nv": [0, 1, 0, 1], "precipitation": [2.0, 2.0, 1.0, 1.0]})
    areas = [{"area_id": "a", "lat": 42.0, "lon": -93.6, "bbox": [-94, 41.5, -93, 42.5]}]
    long, rep = clean.clean_frame(df, areas=areas)
    assert len(long) == 1 and long["area_id"].iloc[0] == "a"
    assert rep.unassigned_rows == 2


def test_missing_latlon_raises():
    with pytest.raises(ValueError):
        clean.clean_frame(pd.DataFrame({"time": ["2024-01-01"], "x": [1]}))


# ------------------------------------------------------------ features / prune
@pytest.fixture(scope="module")
def sample_paths(tmp_path_factory):
    d = tmp_path_factory.mktemp("sample")
    raw = d / "SYNTHETIC_raw.csv"
    make_sample("2024-01-01", "2025-12-31", seed=3).to_csv(raw, index=False)
    areas = d / "areas.json"
    areas.write_text(json.dumps([{"area_id": a, "lat": la, "lon": lo, "radius_deg": 0.2}
                                 for a, la, lo, _ in SAMPLE_AREAS]))
    out = d / "features.parquet"
    rep = build([str(raw)], str(out), str(areas), load_config())
    return {"dir": d, "raw": raw, "areas": areas, "features": out, "report": rep}


def test_build_output_is_xgboost_ready(sample_paths):
    df = pd.read_parquet(sample_paths["features"])
    rep = sample_paths["report"]
    assert rep["synthetic"] is True
    assert set(df["area_id"]) == {a for a, *_ in SAMPLE_AREAS}
    assert not df.duplicated(["area_id", "week"]).any()
    feats = F.feature_columns(df)
    assert all(df[c].dtype == "float64" for c in feats)
    assert (df["week"].dt.day_of_week == 0).all()  # pazartesi
    # hedef: aynı alanda 2 hafta sonraki NDVI
    a = df[df["area_id"] == "ia_story"].set_index("week").sort_index()
    w = a.index[20]
    assert a.loc[w, "target_ndvi"] == pytest.approx(a.loc[w + pd.Timedelta(weeks=2), "ndvi"])
    # placeholder değerler kalmadı
    assert not (df[feats] <= -999).any().any()


def test_prune_rules():
    rng = np.random.default_rng(0)
    x = rng.normal(size=200)
    df = pd.DataFrame({"ndvi": x, "dup": x * 2 + 1e-6 * rng.normal(size=200), "const": 1.0,
                       "sparse": np.where(rng.random(200) < 0.7, np.nan, 1.0) * rng.normal(size=200),
                       "ok": rng.normal(size=200)})
    plan = P.fit_prune(df, list(df.columns))
    assert plan.kept == ["ndvi", "ok"]
    assert set(plan.dropped) == {"dup", "const", "sparse"}
    nd = pd.concat([df, df.iloc[:10] + 1e-9])
    assert len(P.drop_near_duplicates(nd, ["ndvi", "ok"], "ok")) == 200


# ------------------------------------------------------------ model
@pytest.fixture(scope="module")
def trained(sample_paths):
    from ml.model import train

    cfg = load_config()
    cfg["model"]["num_boost_round"] = 300
    out = train.run(str(sample_paths["features"]), cfg, out_dir=sample_paths["dir"] / "run",
                    set_latest=False)  # gerçek ml/runs/LATEST'e dokunma
    return out


def test_train_artifacts_and_metrics(trained):
    for f in ("model.ubj", "quantile_model.ubj", "feature_schema.json", "metrics.json", "importance.csv",
              "climatology.parquet"):
        assert (trained / f).exists(), f
    m = json.loads((trained / "metrics.json").read_text())
    assert m["synthetic_input"] is True
    assert m["test_temporal"]["n"] > 0 and np.isfinite(m["test_temporal"]["xgb"]["rmse"])
    assert m["area_holdout"]["folds"] >= 2


def test_predict_schema(trained, sample_paths):
    from ml.model.predict import Model, SchemaError

    model = Model.load(trained)
    df = pd.read_parquet(sample_paths["features"]).dropna(subset=["ndvi"]).head(5)
    out = model.predict(df)
    keys = {"area_id", "week", "horizon_weeks", "ndvi_p10", "ndvi_p50", "ndvi_p90", "top_features",
            "model_version", "schema_version"}
    assert keys <= set(out[0])
    assert all(o["ndvi_p10"] <= o["ndvi_p50"] <= o["ndvi_p90"] for o in out)
    assert len(out[0]["top_features"]) == 5
    with pytest.raises(SchemaError):
        model.predict(df.drop(columns=[model.features[0]]))


def test_analyze_and_rotation(trained, sample_paths):
    from ml.model.analyze import analyze_area, to_layers
    from ml.model.predict import Model

    feats = pd.read_parquet(sample_paths["features"])
    res = analyze_area(feats, "ks_reno", existing="winter_wheat>fallow", model=Model.load(trained))
    rot = res["rotations"]
    assert rot["existing"]["key"] == "wheat_fallow"
    assert len(rot["recommended"]) == 3 and all(not r["is_existing"] for r in rot["recommended"])
    assert 0 <= res["risk"]["confidence"] <= 0.3  # sentetik model -> güven tavanı
    layers = to_layers(res)
    assert layers[0]["name"] == "ndvi_tahmin" and layers[0]["min"] <= layers[0]["mean"] <= layers[0]["max"]


def test_rotation_canonical():
    from ml.model.rotation import canonical, parse_rotation

    assert canonical(["corn", "corn"]) == canonical(["corn"])
    assert canonical(["soybean", "corn"]) == canonical(["corn", "soybean"])
    assert parse_rotation("corn_soy") == ["corn", "soybean"]
    with pytest.raises(ValueError):
        parse_rotation("corn>banana")


def test_local_obs_merge_and_calibration(trained, sample_paths, tmp_path):
    from ml.model import local as L

    feats = pd.read_parquet(sample_paths["features"])
    a = feats[(feats["area_id"] == "il_champaign") & feats["target_ndvi"].notna()].tail(8)
    tw = a["week"] + pd.Timedelta(weeks=2)
    rows = [{"area_id": "il_champaign", "date": d.date().isoformat(), "variable": "ndvi", "value": v + 0.05}
            for d, v in zip(tw, a["target_ndvi"])]
    rows.append({"area_id": "il_champaign", "date": "", "variable": "irrigation", "value": "1"})
    p = tmp_path / "local.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    loc = L.load_local(p)
    merged, info = L.merge_local_targets(feats, loc, weight=3.0)
    assert info["matched"] == 8 and merged["is_local"].sum() == 8
    assert L.area_profile(loc, "il_champaign") == {"irrigation": 1.0}
    cal = L.calibrate(trained, feats, loc)
    c = cal["il_champaign"]
    assert c["n"] == 8 and c["bias"] > 0 and c["mae_after"] <= c["mae_before"]
    (trained / "calibration.json").unlink()


# ------------------------------------------------------------ statik (toprak + CDL)
def test_static_features_no_leak():
    from ml.data_load import static as S

    weeks = pd.to_datetime(["2024-06-03", "2025-06-02"])
    feat = pd.DataFrame({"area_id": ["a", "a"], "week": weeks})
    cdl = {"a": {2021: "corn", 2022: "soybean", 2023: "corn", 2024: "soybean", 2025: "BANANA_THIS_YEAR"}}
    soil = {"a": {"om_pct": 3.5, "clay_pct": 21, "sand_pct": 45, "awc_cm_cm": 0.21, "ph": 6.2, "ksat_um_s": 9.2,
                  "slope_pct": 3, "hydgrp": "C/D", "drainagecl": "Well drained"}}
    out, rep = S.add_static_features(feat, soil=soil, cdl=cdl)
    r24, r25 = out.iloc[0], out.iloc[1]
    # 2024 satırı: önceki yıl 2023 = mısır, 2 yıl önce soya -> dönüşüm sürüyor, beklenen soya
    assert r24["crop_prev1"] == S.CROP_CODES["corn"] and r24["crop_expected"] == S.CROP_CODES["soybean"]
    # 2025 satırı 2025'in kendi ürününü (BANANA_THIS_YEAR) asla görmez
    assert r25["crop_prev1"] == S.CROP_CODES["soybean"] and r25["prev1_legume"] == 1.0
    assert r24["soil_hydgrp"] == 3.5 and r24["soil_drainage"] == 3 and r24["soil_om_pct"] == 3.5
    assert all(out[c].dtype == "float64" for c in out.columns if c.startswith(("soil_", "crop_", "prev1")))
    out2, rep2 = S.add_static_features(pd.DataFrame({"area_id": ["b"], "week": weeks[:1]}), soil=soil, cdl=cdl)
    assert np.isnan(out2["soil_om_pct"].iloc[0]) and rep2["missing_soil"] == ["b"]
