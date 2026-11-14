"""ML adaptör katmanı.

Backend'in geri kalanı yalnızca `Layer` listesini bilir; ML'in biçimi değişirse sadece buradaki
sağlayıcılar değişir. Sağlayıcılar: `FakeMLProvider` (deterministik sahte) ve `XGBoostMLProvider`
(gerçek: repo kökündeki `ml/` paketi; NASA POWER + MODIS NDVI -> XGBoost -> risk + rotasyon).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import inspect
import logging
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

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
    details: dict | None = field(default=None, compare=False)  # yapısal ayrıntı (frontend için; modele gitmez)

    @property
    def confidence_level(self) -> str:
        """Eminliği modelin yanlış yorumlayamayacağı bir etikete çevirir (hesap backend'de)."""
        if self.confidence >= 0.8:
            return "yüksek"
        if self.confidence >= 0.6:
            return "orta"
        return "düşük"

    def summary(self) -> str:
        rng = f"(aralık {self.min:.1f}–{self.max:.1f}), " if self.min != self.max else ""
        s = (
            f"{self.label}: ortalama {self.mean:.1f} {self.unit} {rng}"
            f"eminlik {self.confidence_level} (%{self.confidence * 100:.0f})"
        )
        return f"{s}. {self.note}" if self.note else s

    def to_dict(self) -> dict:
        d = asdict(self)
        d["confidence_level"] = self.confidence_level  # frontend etiketi hesaplamasın, backend verir
        return d


Progress = Callable[[dict], None]


class MLProvider(Protocol):
    name: str

    async def analyze(self, parcel: Parcel, date: dt.date, *, options: dict | None = None,
                      progress: Progress | None = None) -> list[Layer]: ...


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

    async def analyze(self, parcel: Parcel, date: dt.date, *, options: dict | None = None,
                      progress: Progress | None = None) -> list[Layer]:
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
    providers: list[MLProvider], parcel: Parcel, date: dt.date, *, options: dict | None = None,
    progress: Progress | None = None,
) -> tuple[list[Layer], list[str]]:
    """Sağlayıcıları paralel çalıştırır. Biri patlarsa diğerlerinin sonucu korunur."""
    def call(p):
        # eski imzalı sağlayıcılar (analyze(parcel, date)) da çalışsın
        params = inspect.signature(p.analyze).parameters
        kw = {k: v for k, v in (("options", options), ("progress", progress)) if k in params}
        return p.analyze(parcel, date, **kw)

    results = await asyncio.gather(*(call(p) for p in providers), return_exceptions=True)
    layers: list[Layer] = []
    errors: list[str] = []
    for provider, res in zip(providers, results):
        if isinstance(res, asyncio.CancelledError):
            raise res
        if isinstance(res, MLUserError):  # kullanıcıya gösterilebilir neden (örn. veri alınamadı)
            log.warning("ML sağlayıcısı sonuç üretemedi (%s): %s", provider.name, res)
            errors.append(UserMessage(str(res)))
        elif isinstance(res, BaseException):
            log.error("ML sağlayıcısı başarısız: %s", provider.name, exc_info=res)
            errors.append(f"{provider.name} sonuç üretemedi")
        else:
            layers.extend(res)
    return layers, errors


# ---------------------------------------------------------------------------
# Gerçek ML: ml/ paketi (XGBoost)
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]


class UserMessage(str):
    """Kullanıcıya olduğu gibi gösterilebilecek hata metni (iç ayrıntı içermez)."""


class MLUserError(RuntimeError):
    """Kullanıcıya olduğu gibi gösterilebilecek ML hatası (iç ayrıntı içermez)."""


def json_safe(x: Any) -> Any:
    """NaN/inf -> None, numpy sayıları -> Python (JSON.parse NaN kabul etmez)."""
    if isinstance(x, dict):
        return {str(k): json_safe(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [json_safe(v) for v in x]
    if hasattr(x, "item") and not isinstance(x, (str, bytes)):  # numpy skaler
        x = x.item()
    if isinstance(x, float) and (x != x or x in (float("inf"), float("-inf"))):
        return None
    return x


def _layers_from_dicts(items: list[dict]) -> list[Layer]:
    keys = ("name", "label", "unit", "mean", "min", "max", "confidence", "note")
    out = []
    for d in items:
        kw = {k: d[k] for k in keys if k in d}
        for k in ("mean", "min", "max", "confidence"):
            kw[k] = float(kw[k])
        out.append(Layer(**kw, details=json_safe(d.get("details"))))
    return out


class XGBoostMLProvider:
    """`ml.service.ParcelService` sarmalayıcısı. Ağ ve hesap işi ayrı thread'de çalışır (sunucu kilitlenmez).

    Son analizin yeniden puanlama durumu (`last_state`) parsel başına tutulur; çiftçi kendi rotasyonunu
    veya önceliklerini seçince veri yeniden çekilmeden `rescore()` ile rotasyon katmanları güncellenir.
    """

    name = "xgboost"

    def __init__(self, model_dir: str | None = None, cache_dir: str | None = None, ndvi_timeout: float = 90,
                 ndvi_retries: int = 3) -> None:
        if str(REPO_ROOT) not in sys.path:  # uvicorn --app-dir backend: repo kökü yolda değil
            sys.path.insert(0, str(REPO_ROOT))
        from ml.service import ParcelService  # noqa: PLC0415 (ağır içe aktarma yalnızca gerçek ML'de)

        self._svc = ParcelService(model_dir=model_dir, cache_dir=cache_dir, ndvi_timeout=ndvi_timeout,
                                  ndvi_retries=ndvi_retries)
        self.model_version: str = self._svc.model.schema["model_version"]
        self.synthetic: bool = bool(self._svc.model.schema.get("synthetic_input"))

    async def analyze(self, parcel: Parcel, date: dt.date, *, options: dict | None = None,
                      progress: Progress | None = None) -> list[Layer]:
        from ml import service as S  # noqa: PLC0415

        opts = options or {}
        loop = asyncio.get_running_loop()

        def report(stage: str, info: dict) -> None:  # thread'den çağrılır -> olay döngüsüne güvenli aktar
            if progress is not None:
                loop.call_soon_threadsafe(progress, {"stage": stage, **info})

        lon, lat = parcel.centroid

        def work() -> dict:
            return self._svc.analyze(
                lon, lat, today=dt.date.today(),  # hep güncel veri; istekteki tarih yalnızca kayıt için
                existing=opts.get("existing_rotation"), chosen=opts.get("chosen_rotation"),
                priorities=opts.get("priorities"), profile=opts.get("profile"), progress=report,
            )

        try:
            res = await asyncio.to_thread(work)
        except S.DataUnavailable as e:
            raise MLUserError(str(e)) from e
        except ValueError as e:  # örn. bilinmeyen ürün adı
            raise MLUserError(f"Geçersiz rotasyon girdisi: {e}") from e
        state = res.pop("rescore_state")
        layers = _layers_from_dicts(S.to_layers(res))
        if layers:
            layers[0] = Layer(**{**asdict(layers[0]), "details": {**(layers[0].details or {}), "_meta": json_safe({
                "warnings": res["warnings"], "data_as_of": res["data_as_of"], "data_sources": res["data_sources"],
                "model_version": res["model_version"], "information_gaps": res["rotations"]["information_gaps"],
                "disclaimer": res["rotations"]["disclaimer"], "field_info": res.get("field_info"),
            })}})
        self.last_state = {"parcel": parcel.centroid, "state": state}
        return layers

    def rescore(self, parcel: Parcel, existing=None, chosen=None, priorities=None, profile=None) -> list[Layer]:
        """Son analizin verisiyle rotasyonları yeniden puanlar (ağ yok, milisaniyeler)."""
        from ml import service as S  # noqa: PLC0415

        last = getattr(self, "last_state", None)
        if not last or last["parcel"] != parcel.centroid:
            raise MLUserError("Bu parsel için önce analiz yapılmalı.")
        try:
            rot = S.ParcelService.rescore(last["state"], existing, chosen, priorities, profile)
        except ValueError as e:
            raise MLUserError(f"Geçersiz rotasyon girdisi: {e}") from e
        return _layers_from_dicts(S.rotation_layers(rot, self.synthetic))

    @staticmethod
    def catalog() -> dict:
        """Frontend formu için ürün/rotasyon/öncelik listesi."""
        from ml.model import rotation as ROT  # noqa: PLC0415

        cat = ROT.catalog()
        return {
            "crops": [{"id": k, "name": v["name"], "family": v["family"], "season": v["season"],
                       "cover_crop": bool(v.get("cover_crop", False))} for k, v in cat["crops"].items()],
            "rotations": [{"id": k, "name": v["name"], "sequence": v["seq"]} for k, v in cat["rotations"].items()],
            "priorities": [{"id": k, "label": v, "default": ROT.DEFAULT_PRIORITIES[k]}
                           for k, v in ROT.PRIORITY_LABELS.items()],
            "rotation_syntax": "ürünler '>' ile sıralanır, örtü bitkisi '+' ile eklenir: corn+cereal_rye>soybean",
        }


def build_providers(kind: str, *, fake_delay: float = 0.0, model_dir: str | None = None,
                    cache_dir: str | None = None, ndvi_timeout: float = 90, ndvi_retries: int = 3) -> list[Any]:
    if kind == "fake":
        return [FakeMLProvider(fake_delay)]
    if kind == "xgboost":
        return [XGBoostMLProvider(model_dir, cache_dir, ndvi_timeout, ndvi_retries)]
    raise RuntimeError(f"Bilinmeyen ML_PROVIDER: {kind!r} (fake | xgboost)")
