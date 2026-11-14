"""Ham dosyalar -> ML'e hazır özellik tablosu (parquet).

    uv run python -m ml.data_load.build --raw ml/cache/raw --areas ml/data_load/areas.example.json \
        --out ml/cache/features.parquet

--raw: dosya ya da klasör (içindeki *.csv / *.parquet). nasa_pipeline'ın CSV'si de olur, indiricinin parquet'leri de.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import tomllib
from pathlib import Path

import pandas as pd

from . import clean, features

log = logging.getLogger("ml.build")
DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config.toml"


def load_config(path: str | Path | None = None) -> dict:
    p = Path(path) if path else DEFAULT_CONFIG
    return tomllib.loads(p.read_text()) if p.exists() else {}


def collect(raw: list[str]) -> list[Path]:
    out = []
    for r in raw:
        p = Path(r)
        if p.is_dir():
            out += sorted([*p.glob("*.parquet"), *p.glob("*.csv")])
        elif p.exists():
            out.append(p)
        else:
            raise FileNotFoundError(p)
    return out


def build(raw: list[str], out: str, areas: str | None = None, cfg: dict | None = None) -> dict:
    cfg = cfg or load_config()
    dc = cfg.get("data", {})
    paths = collect(raw)
    area_list = clean.load_areas(areas)
    long, rep = clean.clean_files(
        paths, areas=area_list, hours=tuple(dc.get("hours", (0, 6, 12, 18))) or None,
        hour_tolerance_h=dc.get("hour_tolerance_h", 1.5), grid_deg=dc.get("grid_deg", 0.25),
    )
    daily, centers = features.to_daily(long)
    weekly = features.to_weekly(daily)
    feat = features.build_features(
        weekly, centers, horizon_weeks=dc.get("horizon_weeks", 2), ndvi_lags=dc.get("ndvi_lags", 8),
        max_gap_weeks=dc.get("max_gap_weeks", 2),
    )
    outp = Path(out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    tmp = outp.with_suffix(".tmp.parquet")
    feat.to_parquet(tmp, index=False)
    os.replace(tmp, outp)  # atomik yazım
    missing = feat[features.feature_columns(feat)].isna().mean().sort_values(ascending=False)
    report = {
        "inputs": [str(p) for p in paths],
        "synthetic": rep.synthetic,
        "clean": rep.to_dict(),
        "areas": sorted(feat["area_id"].unique().tolist()),
        "weeks": [str(feat["week"].min().date()), str(feat["week"].max().date())],
        "rows": len(feat),
        "rows_with_observed_target": int(feat.get("target_observed", pd.Series(dtype=float)).sum()),
        "n_features": len(features.feature_columns(feat)),
        "feature_missing_top": {k: round(float(v), 3) for k, v in missing.head(15).items()},
        "dtypes": {str(k): int(v) for k, v in feat.dtypes.astype(str).value_counts().items()},
    }
    outp.with_suffix(".report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str))
    return report


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", nargs="+", required=True)
    ap.add_argument("--areas")
    ap.add_argument("--out", default="ml/cache/features.parquet")
    ap.add_argument("--config")
    a = ap.parse_args(argv)
    rep = build(a.raw, a.out, a.areas, load_config(a.config))
    if rep["synthetic"]:
        print("UYARI: girdi SENTETİK veri içeriyor; sonuçlar yalnızca deneme amaçlıdır.")
    print(json.dumps({k: rep[k] for k in ("areas", "weeks", "rows", "rows_with_observed_target", "n_features")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
