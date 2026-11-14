"""run_all: AppEEARS kesintisinde ORNL yedeğine geçiş (ağ yok, komutlar sahte)."""
import json
import types

from ml import run_all as R


def test_fallback_to_ornl_when_appeears_down(tmp_path, monkeypatch):
    areas = tmp_path / "areas.json"
    areas.write_text(json.dumps([{"area_id": a, "lat": 40.0, "lon": -90.0} for a in ("a", "b", "c")]))
    monkeypatch.setattr(R, "CACHE", tmp_path)
    monkeypatch.setattr(R, "LOGS", tmp_path)
    monkeypatch.setattr(R, "STATE", tmp_path / "state.json")
    (tmp_path / "appeears_smoke.log").write_text("RuntimeError: giving up on POST login: HTTP Error 504")
    have = {"MOD13Q1": {"a": 10}}  # a zaten var
    calls = []

    def fake_run(ctx, step, args, timeout_h=8):
        calls.append((step, args))
        if step == "ornl_ndvi":
            asked = [x["area_id"] for x in json.loads(open(args[args.index("--areas") + 1]).read())]
            assert "a" not in asked  # yalnızca eksikler istenir
            have["MOD13Q1"].update({k: 5 for k in asked})
        return step != "appeears_smoke"

    monkeypatch.setattr(R, "run_cmd", fake_run)
    monkeypatch.setattr(R, "raw_coverage", lambda src, hint=None: dict(have.get(src, {})))
    a = types.SimpleNamespace(areas=str(areas), max_fields=None, start="2024-01-01", end="2025-12-31",
                              appeears_products="ndvi", appeears_timeout=1)
    ctx = R.Ctx(a)
    ctx.force = lambda s: False
    assert R.step_appeears(ctx) is True
    assert [c[0] for c in calls] == ["appeears_smoke", "ornl_ndvi"]
    assert any("kesintisi bitince" in t for t in ctx.todo)
