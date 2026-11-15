"""run_all: NDVI adımı (Planetary -> ORNL yedeği) ve AppEEARS'ın akışı durdurmaması (ağ yok, komutlar sahte)."""
import json
import types

from ml import run_all as R


def _ctx(tmp_path, monkeypatch, areas_ids=("a", "b", "c"), start="2024-01-01", end="2025-12-31"):
    areas = tmp_path / "areas.json"
    areas.write_text(json.dumps([{"area_id": a, "lat": 40.0, "lon": -90.0} for a in areas_ids]))
    monkeypatch.setattr(R, "CACHE", tmp_path)
    monkeypatch.setattr(R, "LOGS", tmp_path)
    monkeypatch.setattr(R, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(R, "STATIC", tmp_path / "static")
    a = types.SimpleNamespace(areas=str(areas), max_fields=None, start=start, end=end,
                              appeears_products="lst,smap", appeears_timeout=1)
    ctx = R.Ctx(a)
    ctx.force = lambda s: False
    return ctx


def test_ndvi_planetary_then_ornl_only_if_planetary_gives_nothing(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path, monkeypatch)
    have = {"MOD13Q1": {}, "MYD13Q1": {}}
    calls = []

    def fake_run(ctx, step, args, timeout_h=8):
        calls.append(step)
        if step == "ndvi":  # Planetary: hepsine Terra + Aqua
            for src in have:
                have[src].update({k: 46 for k in "abc"})
        return True

    monkeypatch.setattr(R, "run_cmd", fake_run)
    monkeypatch.setattr(R, "raw_coverage", lambda src, hint=None: dict(have.get(src, {})))
    assert R.step_ndvi(ctx) is True
    assert calls == ["ndvi"]  # ORNL'ye gerek kalmadı


def test_ndvi_falls_back_to_ornl_for_missing_and_partial(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path, monkeypatch)
    have = {"MOD13Q1": {"a": 46, "b": 12}}  # önceden ORNL'den: a tam, b yarım; Planetary yeni veri getirmiyor
    calls = []

    def fake_run(ctx, step, args, timeout_h=8):
        calls.append(step)
        if step == "ornl_ndvi":
            asked = [x["area_id"] for x in json.loads(open(args[args.index("--areas") + 1]).read())]
            assert "a" not in asked and "b" in asked  # tam olan istenmez, yarım olan istenir
            have["MOD13Q1"].update({k: 46 for k in asked})
        return True

    monkeypatch.setattr(R, "run_cmd", fake_run)
    monkeypatch.setattr(R, "raw_coverage", lambda src, hint=None: dict(have.get(src, {})))
    assert R.step_ndvi(ctx) is True
    assert calls[0] == "ndvi" and "ornl_ndvi" in calls


def test_appeears_outage_does_not_stop_pipeline(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path, monkeypatch)
    (tmp_path / "appeears_smoke.log").write_text("RuntimeError: giving up on POST login: HTTP Error 504")
    monkeypatch.setattr(R, "run_cmd", lambda *a, **k: False)
    monkeypatch.setattr(R, "raw_coverage", lambda src, hint=None: {})
    assert R.step_appeears(ctx) is True  # akış durmaz
    assert any("kesintisi bitince" in t for t in ctx.todo)


def test_power_extends_when_range_not_covered(tmp_path, monkeypatch):
    import pandas as pd

    ctx = _ctx(tmp_path, monkeypatch, ("a",), start="2018-01-01")
    span = {"a": (pd.Timestamp("2024-01-01"), pd.Timestamp("2025-12-31"), 731)}  # yalnızca 2024-2025 var
    calls = []
    monkeypatch.setattr(R, "raw_span", lambda src, hint=None: span)
    monkeypatch.setattr(R, "run_cmd", lambda ctx, step, args, timeout_h=8: calls.append(args) or True)
    R.step_power(ctx)
    assert calls and "2018-01-01" in calls[0]  # 2018'e kadar uzatmak için çalıştı
