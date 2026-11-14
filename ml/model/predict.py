"""Kararlı tahmin arayüzü (backend ileride bunu çağıracak).

    predict(features_df) -> list[dict]   # ml-bot-instructions.md bölüm 9 şeması (+ birkaç ek alan)
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb


RUNS_DIR = Path(__file__).resolve().parents[1] / "runs"


class SchemaError(ValueError):
    pass


class Model:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.schema = json.loads((self.path / "feature_schema.json").read_text())
        self.features: list[str] = self.schema["features"]
        self.point = xgb.Booster()
        self.point.load_model(self.path / "model.ubj")
        self.quant = xgb.Booster()
        self.quant.load_model(self.path / "quantile_model.ubj")
        cal = self.path / "calibration.json"
        self.calibration: dict = json.loads(cal.read_text()) if cal.exists() else {}
        self.climatology = pd.read_parquet(self.path / "climatology.parquet")
        ref = self.path / "area_reference.parquet"
        self.area_reference = pd.read_parquet(ref) if ref.exists() else pd.DataFrame()

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Model":
        if path is None:
            latest = RUNS_DIR / "LATEST"
            if not latest.exists():
                raise FileNotFoundError("Eğitilmiş model yok (ml/runs/LATEST). Önce ml.model.train çalıştırın.")
            path = latest.read_text().strip()
        return cls(Path(path))

    def validate(self, df: pd.DataFrame) -> None:
        missing = [c for c in self.features if c not in df.columns]
        if missing:
            raise SchemaError(f"{len(missing)} özellik eksik (ilk 10): {missing[:10]}")
        if "schema_version" in df and (df["schema_version"] != self.schema["schema_version"]).any():
            raise SchemaError(f"schema_version uyuşmuyor: veri {sorted(df['schema_version'].unique())}, "
                              f"model {self.schema['schema_version']}")
        bad = [c for c in self.features if not pd.api.types.is_numeric_dtype(df[c])]
        if bad:
            raise SchemaError(f"Sayısal olmayan özellik sütunları: {bad[:10]}")

    def _X(self, df):
        return df[self.features].astype("float64")

    def raw_quantiles(self, df: pd.DataFrame) -> np.ndarray:
        self.validate(df)
        it = (0, self.quant.best_iteration + 1)
        q = self.quant.predict(xgb.DMatrix(self._X(df), missing=np.nan), iteration_range=it)
        q = np.sort(q.reshape(len(df), -1), axis=1)
        if self.schema.get("target_mode") == "delta":  # model değişimi öğrendi: bugünkü NDVI'yı ekle
            if "ndvi" not in df:
                raise SchemaError("delta modundaki model için 'ndvi' sütunu gerekli")
            q = q + df["ndvi"].to_numpy("float64")[:, None]
        return q

    def contributions(self, df: pd.DataFrame) -> np.ndarray:
        it = (0, self.point.best_iteration + 1)
        return self.point.predict(xgb.DMatrix(self._X(df), missing=np.nan), pred_contribs=True, iteration_range=it)

    def predict(self, df: pd.DataFrame, top_k: int = 5) -> list[dict]:
        q = self.raw_quantiles(df)
        contrib = self.contributions(df)[:, :-1]  # son sütun sabit terim (bias)
        gq = float(self.schema.get("conformal_q", 0.0))
        out = []
        for i, (_, r) in enumerate(df.iterrows()):
            aid = str(r["area_id"])
            cal = self.calibration.get(aid)
            bias, qq = (cal["bias"], cal["q"]) if cal else (0.0, gq)
            p10, p50, p90 = q[i, 0] + bias - qq, q[i, 1] + bias, q[i, -1] + bias + qq
            order = np.argsort(-np.abs(contrib[i]))[:top_k]
            out.append({
                "area_id": aid,
                "week": pd.Timestamp(r["week"]).date().isoformat(),
                "horizon_weeks": int(self.schema["horizon_weeks"]),
                "ndvi_p10": float(np.clip(p10, -1, 1)),
                "ndvi_p50": float(np.clip(p50, -1, 1)),
                "ndvi_p90": float(np.clip(p90, -1, 1)),
                "top_features": [{"name": self.features[j], "contribution": float(contrib[i, j]),
                                  "value": None if pd.isna(r[self.features[j]]) else float(r[self.features[j]])}
                                 for j in order],
                "model_version": self.schema["model_version"],
                "schema_version": int(self.schema["schema_version"]),
                "calibrated_with_local": bool(cal),
                "n_local_obs": int(cal["n"]) if cal else 0,
                "synthetic_model": bool(self.schema.get("synthetic_input")),
            })
        return out


_MODEL: Model | None = None


def predict(features: pd.DataFrame) -> list[dict]:
    """Bir satır = bir alan-hafta. Model ilk çağrıda yüklenir."""
    global _MODEL
    if _MODEL is None:
        _MODEL = Model.load()
    return _MODEL.predict(features)


