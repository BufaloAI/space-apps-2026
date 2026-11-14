"""ML adaptör katmanı.

ML'in çıktı biçimi henüz belli değil. Backend'in geri kalanı yalnızca `Layer` listesini bilir;
format değişirse sadece buradaki sağlayıcılar değişir. Şimdilik sahte (deterministik) sağlayıcı var.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import logging
from dataclasses import asdict, dataclass
from typing import Protocol

from .geo import Parcel

log = logging.getLogger("backend.ml")


@dataclass(frozen=True)
class Layer:
    """Bir ML çıktısının parsel üzerindeki özeti (ham harita değil, sayısal özet)."""

    name: str  # makine adı: "yagis", "toprak", "nem", ...
    label: str  # kullanıcıya/modele gösterilen ad
    unit: str
    mean: float
    min: float
    max: float
    confidence: float  # 0..1
    note: str = ""

    @property
    def confidence_level(self) -> str:
        """Eminliği modelin yanlış yorumlayamayacağı bir etikete çevirir (hesap backend'de)."""
        if self.confidence >= 0.8:
            return "yüksek"
        if self.confidence >= 0.6:
            return "orta"
        return "düşük"

    def summary(self) -> str:
        s = (
            f"{self.label}: ortalama {self.mean:.1f} {self.unit} "
            f"(aralık {self.min:.1f}–{self.max:.1f}), "
            f"eminlik {self.confidence_level} (%{self.confidence * 100:.0f})"
        )
        return f"{s}. {self.note}" if self.note else s

    def to_dict(self) -> dict:
        d = asdict(self)
        d["confidence_level"] = self.confidence_level  # frontend etiketi hesaplamasın, backend verir
        return d


class MLProvider(Protocol):
    name: str

    async def analyze(self, parcel: Parcel, date: dt.date) -> list[Layer]: ...


def _unit(seed: str) -> float:
    """seed'den 0..1 arası kararlı sahte değer."""
    return int.from_bytes(hashlib.sha256(seed.encode()).digest()[:8], "big") / 2**64


class FakeMLProvider:
    """Gerçek ML hazır olana kadar tutarlı sahte katmanlar üretir (aynı parsel+tarih = aynı sonuç)."""

    name = "fake"

    # ad: (etiket, birim, alt, üst)
    _SPECS = {
        "yagis": ("Yağış (30 günlük toplam)", "mm", 0.0, 200.0),
        "toprak": ("Toprak verimlilik skoru", "puan", 0.0, 100.0),
        "nem": ("Toprak nemi", "%", 5.0, 60.0),
    }

    def __init__(self, delay_seconds: float = 0.0) -> None:
        self._delay = delay_seconds

    async def analyze(self, parcel: Parcel, date: dt.date) -> list[Layer]:
        if self._delay:
            await asyncio.sleep(self._delay)
        base = f"{parcel.centroid[0]:.3f},{parcel.centroid[1]:.3f},{date.isoformat()}"
        layers = []
        for name, (label, unit, lo, hi) in self._SPECS.items():
            span = hi - lo
            mean = lo + span * (0.15 + 0.7 * _unit(f"{base}|{name}|mean"))
            spread = span * (0.03 + 0.12 * _unit(f"{base}|{name}|spread"))
            conf = 0.55 + 0.4 * _unit(f"{base}|{name}|conf")
            layers.append(
                Layer(
                    name=name, label=label, unit=unit, mean=mean,
                    min=max(lo, mean - spread), max=min(hi, mean + spread),
                    confidence=conf, note="(sahte veri)",
                )
            )
        return layers


async def run_providers(
    providers: list[MLProvider], parcel: Parcel, date: dt.date
) -> tuple[list[Layer], list[str]]:
    """Sağlayıcıları paralel çalıştırır. Biri patlarsa diğerlerinin sonucu korunur."""
    results = await asyncio.gather(*(p.analyze(parcel, date) for p in providers), return_exceptions=True)
    layers: list[Layer] = []
    errors: list[str] = []
    for provider, res in zip(providers, results):
        if isinstance(res, asyncio.CancelledError):
            raise res
        if isinstance(res, BaseException):
            log.error("ML sağlayıcısı başarısız: %s", provider.name, exc_info=res)
            errors.append(f"{provider.name} sonuç üretemedi")
        else:
            layers.extend(res)
    return layers, errors
