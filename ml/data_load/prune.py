"""Özellik budama: çok boş / sabit / güçlü korelasyonlu sütunlar, çok boş satırlar, neredeyse aynı satırlar.

Kurallar YALNIZCA eğitim kısmında öğrenilir (fit) ve aynen doğrulama/test/tahmine uygulanır (apply);
böylece test verisinden bilgi sızmaz. Sonuç `feature_schema.json`'a yazılır.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# Bu sütunlar korelasyon yüzünden atılmaz (mevsim ve şimdiki NDVI modelin omurgası).
PROTECTED = ("ndvi", "week_sin", "week_cos", "lat", "lon")


@dataclass
class PrunePlan:
    kept: list[str]
    dropped: dict[str, str] = field(default_factory=dict)  # sütun -> neden

    def to_dict(self) -> dict:
        return {"kept": self.kept, "dropped": self.dropped}


def fit_prune(train: pd.DataFrame, features: list[str], col_missing_max: float = 0.5,
              corr_max: float = 0.95, protected=PROTECTED) -> PrunePlan:
    dropped: dict[str, str] = {}
    X = train[features]
    miss = X.isna().mean()
    for c in features:
        if miss[c] > col_missing_max:
            dropped[c] = f"eksik oranı {miss[c]:.0%} > {col_missing_max:.0%}"
    rest = [c for c in features if c not in dropped]
    std = X[rest].std(skipna=True)
    for c in rest:
        if not np.isfinite(std[c]) or std[c] < 1e-12:
            dropped[c] = "sabit"
    rest = [c for c in rest if c not in dropped]
    # Korelasyon: |r| > corr_max olan çiftlerden birini at. Öncelik: korunanlar > az eksikli > kısa ad (temel değişken)
    order = sorted(rest, key=lambda c: (c not in protected, miss[c], len(c), c))
    corr = X[order].corr(min_periods=20).abs().to_numpy()
    keep_idx: list[int] = []
    for i, c in enumerate(order):
        hit = next((j for j in keep_idx if np.isfinite(corr[i, j]) and corr[i, j] > corr_max), None)
        if hit is not None and c not in protected:
            dropped[c] = f"|r|={corr[i, hit]:.3f} ile {order[hit]} tutuldu"
        else:
            keep_idx.append(i)
    kept = [c for c in features if c not in dropped]
    return PrunePlan(kept=kept, dropped=dropped)


def drop_sparse_rows(df: pd.DataFrame, features: list[str], row_missing_max: float = 0.5) -> pd.DataFrame:
    """Özelliklerinin yarısından fazlası boş satırları atar."""
    return df[df[features].isna().mean(axis=1) <= row_missing_max]


def drop_near_duplicates(df: pd.DataFrame, features: list[str], target: str, bins: int = 50) -> pd.DataFrame:
    """Neredeyse aynı satırları atar: her sütun kendi std'sinin 1/bins'i kadar adımlarla nicemlenir;
    nicemlenmiş özellikler + hedef aynıysa tek satır tutulur. Yalnızca EĞİTİM kümesinde kullanılır."""
    cols = features + [target]
    X = df[cols].astype("float64")
    step = (X.std(skipna=True) / bins).replace(0, 1.0).fillna(1.0)
    q = (X / step).round()
    key = pd.util.hash_pandas_object(q.fillna(-1e18), index=False)
    return df[~key.duplicated()]
