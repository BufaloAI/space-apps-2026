"""Çizilen herhangi bir parsel için uçtan uca ML servisi (backend bunu çağırır).

Akış: parsel merkezi -> güncel veri (POWER son ~3 yıl + bu yıl, MODIS NDVI son ~26 hafta; paralel)
-> temizlik + haftalık özellikler -> XGBoost NDVI tahmini (p10/p50/p90) -> risk -> rotasyon + zararlar.

Veri her zaman BUGÜNE kadar çekilir ("hep güncel veri"). MODIS NDVI ~4 hafta gecikmeli yayınlandığı için
tahmin, son uydu ölçümünden `horizon` hafta sonrası içindir; bu tarih bugünden önceyse sonuç "uydu
gecikmesini kapatan şimdiki durum tahmini" olarak etiketlenir.

Sayısal kararların hepsi burada; LLM yalnızca sonucu yorumlar.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from ml.data_load import clean, download, features as F
from ml.data_load.build import load_config
from ml.model import risk as R
from ml.model import rotation as ROT
from ml.model.predict import Model

log = logging.getLogger("ml.service")
ML_DIR = Path(__file__).resolve().parent
AREA = "parcel"

Progress = Callable[[str, dict], None]


def _noop(_stage: str, _info: dict) -> None:
    pass


class DataUnavailable(RuntimeError):
    """Hiç iklim verisi alınamadı (analiz yapılamaz)."""


class ParcelService:
    def __init__(self, model_dir: str | Path | None = None, cache_dir: str | Path | None = None,
                 history_years: int = 3, ndvi_weeks: int = 22, ndvi_latency_days: int = 18, ndvi_timeout: float = 60, ndvi_retries: int = 3,
                 power_timeout: float = 30, power_retries: int = 2, cache_days: int = 1):
        self.model = Model.load(model_dir)
        self.cfg = load_config()
        self.cache_dir = Path(cache_dir) if cache_dir else ML_DIR / "cache" / "parcels"
        self.history_years, self.ndvi_weeks = history_years, ndvi_weeks
        self.ndvi_latency_days = ndvi_latency_days  # bundan yeni kompozit henüz yayınlanmamıştır (boş istek atma)
        self.ndvi_timeout, self.ndvi_retries = ndvi_timeout, ndvi_retries
        self.power_timeout, self.power_retries = power_timeout, power_retries
        self.cache_days = cache_days
        self._lock = threading.Lock()

    # ------------------------------------------------------------ veri
    def _cache_path(self, lat: float, lon: float, today: dt.date) -> Path:
        return self.cache_dir / f"{lat:.3f}_{lon:.3f}_{today.isoformat()}"

    def fetch(self, lat: float, lon: float, today: dt.date, progress: Progress = _noop) -> tuple[pd.DataFrame, dict]:
        """Ham veri + kaynak durumu. Aynı gün aynı nokta için diskteki önbellekten okur."""
        base = self._cache_path(lat, lon, today)
        pq, meta = base.with_suffix(".parquet"), base.with_suffix(".json")
        if pq.exists() and meta.exists():
            status = json.loads(meta.read_text())
            if status.get("ndvi", {}).get("ok") and status.get("weather", {}).get("ok"):
                progress("data", {"status": "cache"})
                return pd.read_parquet(pq), status

        p_start = dt.date(today.year - self.history_years, 1, 1)
        n_start = today - dt.timedelta(weeks=self.ndvi_weeks)
        status: dict = {}

        def power():
            progress("data", {"source": "weather", "status": "started"})
            df = download.fetch_power(AREA, lat, lon, p_start, today, timeout=self.power_timeout,
                                      retries=self.power_retries)
            progress("data", {"source": "weather", "status": "done", "rows": len(df)})
            return df

        def ndvi():
            progress("data", {"source": "ndvi", "status": "started"})
            n_end = today - dt.timedelta(days=self.ndvi_latency_days)
            df = download.fetch_modis_ornl(AREA, lat, lon, n_start, n_end, "MOD13Q1", timeout=self.ndvi_timeout,
                                           retries=self.ndvi_retries, parallel=True)
            progress("data", {"source": "ndvi", "status": "done", "rows": len(df)})
            return df

        frames = []
        with ThreadPoolExecutor(max_workers=2) as ex:
            futs = {"weather": ex.submit(power), "ndvi": ex.submit(ndvi)}
            for name, fut in futs.items():
                try:
                    df = fut.result()
                    if df.empty:
                        raise RuntimeError("veri dönmedi")
                    status[name] = {"ok": True, "rows": int(len(df)),
                                    "first": str(df["time"].min().date()), "last": str(df["time"].max().date())}
                    frames.append(df)
                except Exception as e:  # noqa: BLE001  (kaynak düşerse diğeriyle devam)
                    log.warning("%s alınamadı: %s", name, e)
                    status[name] = {"ok": False, "error": str(e)[:200]}
                    progress("data", {"source": name, "status": "failed"})
        if not status.get("weather", {}).get("ok"):
            raise DataUnavailable("İklim verisi (NASA POWER) alınamadı; analiz yapılamıyor.")
        raw = pd.concat(frames, ignore_index=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = pq.with_suffix(".tmp")
        raw.to_parquet(tmp, index=False)
        tmp.replace(pq)
        meta.write_text(json.dumps(status))
        self._prune_cache(today)
        return raw, status

    def _prune_cache(self, today: dt.date) -> None:
        for p in self.cache_dir.glob("*_*_*.*"):
            try:
                d = dt.date.fromisoformat(p.stem.rsplit("_", 1)[1])
            except ValueError:
                continue
            if (today - d).days > self.cache_days:
                p.unlink(missing_ok=True)

    def build_features(self, raw: pd.DataFrame) -> pd.DataFrame:
        dc = self.cfg.get("data", {})
        long, _rep = clean.clean_frame(raw, hours=tuple(dc.get("hours", (0, 6, 12, 18))))
        if long.empty:
            raise DataUnavailable("Veri temizlendikten sonra kullanılabilir değer kalmadı.")
        daily, centers = F.to_daily(long)
        weekly = F.to_weekly(daily)
        return F.build_features(weekly, centers, horizon_weeks=int(self.model.schema["horizon_weeks"]),
                                ndvi_lags=dc.get("ndvi_lags", 8), max_gap_weeks=dc.get("max_gap_weeks", 2))

    # ------------------------------------------------------------ analiz
    def analyze(self, lon: float, lat: float, today: dt.date | None = None, existing=None, chosen=None,
                priorities: dict | None = None, profile: dict | None = None,
                progress: Progress = _noop) -> dict:
        today = today or dt.date.today()
        raw, status = self.fetch(lat, lon, today, progress)
        progress("features", {"status": "started"})
        feats = self.build_features(raw)
        missing = [c for c in self.model.features if c not in feats.columns]
        for c in missing:  # modelin bildiği ama bu kaynaklardan gelmeyen özellik -> NaN (XGBoost yönetir)
            feats[c] = np.nan
        feats = feats.sort_values("week").reset_index(drop=True)
        weather_rows = feats[feats["precip_mm"].notna()] if "precip_mm" in feats else feats
        latest = weather_rows.iloc[-1]
        has_ndvi = "ndvi_obs" in feats and (feats["ndvi_obs"] > 0).any()
        warnings: list[str] = []
        pred = None
        ndvi_row = None
        if has_ndvi:
            ndvi_row = feats[feats["ndvi_obs"] > 0].iloc[-1]
            progress("model", {"status": "started"})
            pred = self.model.predict(ndvi_row.to_frame().T.astype({c: "float64" for c in self.model.features}))[0]
            target = pd.Timestamp(ndvi_row["week"]) + pd.Timedelta(weeks=int(pred["horizon_weeks"]))
            pred["target_week"] = target.date().isoformat()
            pred["ndvi_last_observed_week"] = pd.Timestamp(ndvi_row["week"]).date().isoformat()
            pred["ndvi_last_observed"] = float(ndvi_row["ndvi"])
            pred["is_nowcast"] = bool(target.date() <= today)
            if pred["is_nowcast"]:
                warnings.append(f"Uydu NDVI verisi gecikmeli (son ölçüm {pred['ndvi_last_observed_week']} haftası); "
                                f"tahmin {pred['target_week']} haftası için, yani bugüne yakın durumun tahminidir.")
        else:
            warnings.append("NDVI (bitki örtüsü) verisi alınamadı; bitki tahmini yapılamadı, risk ve rotasyon "
                            "yalnızca iklim verisinden hesaplandı.")
        if missing:
            warnings.append(f"Modelin kullandığı {len(missing)} özellik bu parsel için yok (örn. {missing[0]}); "
                            "eminlik buna göre düşük olabilir.")

        # Risk: iklim bileşenleri en güncel haftadan, bitki bileşeni son NDVI haftasından
        row = latest.copy()
        if ndvi_row is not None:
            row["ndvi"], row["week"] = ndvi_row["ndvi"], ndvi_row["week"]
        woy = pd.Timestamp(latest["week"]).isocalendar().week
        fw = feats["week"].dt.isocalendar().week.astype(int)
        same_season = feats[(np.minimum(abs(fw - woy), 52 - abs(fw - woy)) <= 3) & (feats["week"] < latest["week"])]
        ref = same_season.median(numeric_only=True) if len(same_season) >= 3 else feats.median(numeric_only=True)
        cov = float(latest.get("_coverage", 0.5)) if pd.notna(latest.get("_coverage", np.nan)) else 0.5
        srisk = R.short_term_risk(row, pred, None, ref, cov, int(self.model.schema["horizon_weeks"]))
        if missing:
            srisk["confidence"] = round(srisk["confidence"] * (1 - 0.5 * len(missing) / len(self.model.features)), 3)
            srisk["confidence_level"] = R.confidence_level(srisk["confidence"])
        progress("rotations", {"status": "started"})
        cond = R.area_climate(feats)
        state = {"cond": cond, "risk": srisk, "profile": profile or {}, "model_confidence": srisk["confidence"]}
        rot = ROT.recommend(cond, existing, priorities, profile or {}, srisk,
                            model_confidence=srisk["confidence"], chosen=chosen)
        return {
            "as_of": today.isoformat(),
            "data_sources": status,
            "data_as_of": {"weather": str(pd.Timestamp(latest["week"]).date()),
                           "ndvi": pred["ndvi_last_observed_week"] if pred else None},
            "forecast": pred, "risk": srisk, "area_conditions": cond, "rotations": rot,
            "warnings": warnings, "missing_features": missing,
            "model_version": self.model.schema["model_version"],
            "synthetic_model": bool(self.model.schema.get("synthetic_input")),
            "rescore_state": state,
        }

    @staticmethod
    def rescore(state: dict, existing=None, chosen=None, priorities: dict | None = None,
                profile: dict | None = None) -> dict:
        """Veri çekmeden rotasyonları yeniden puanlar (çiftçi kendi rotasyonunu/önceliklerini seçince)."""
        prof = {**state.get("profile", {}), **(profile or {})}
        return ROT.recommend(state["cond"], existing, priorities, prof, state["risk"],
                             model_confidence=state["model_confidence"], chosen=chosen)


# ---------------------------------------------------------------- backend Layer biçimi
def _damage_text(rec: dict, n: int = 3) -> str:
    parts = []
    for d in rec["damages"][:n]:
        loss = f" (olası verim kaybı %{d['yield_loss_pct'][0]:.0f}–{d['yield_loss_pct'][1]:.0f})" \
            if d.get("yield_loss_pct") else ""
        parts.append(f"{d['risk']} [{d['severity']}] {d['affects']}: {d['impact']}{loss}")
    return "; ".join(parts) if parts else "belirgin zarar beklenmiyor"


def rotation_layer(name: str, label: str, rec: dict, confidence: float, synthetic: bool) -> dict:
    seq = " → ".join(rec["sequence_names"])
    note = (f"Sıra: {seq}. Neden: {'; '.join(rec['why'])}. "
            f"Faydalar: {'; '.join(rec['benefits']) or 'belirgin değil'}. "
            f"Olası zararlar: {_damage_text(rec)}. Risk puanı {100 * rec['risk']:.0f}/100.")
    if "delta_vs_existing" in rec:
        d = rec["delta_vs_existing"]
        ds, dr = round(100 * d["score"]), round(100 * d["risk"])
        note += (f" Mevcut rotasyona göre puan farkı {ds:+d}; "
                 + (f"risk farkı {dr:+d}." if dr != 0 else "risk mevcut rotasyonla aynı düzeyde."))
    if synthetic:
        note += " (SENTETİK veriyle eğitilmiş model)"
    score = round(100 * rec["score"], 1)  # 0-100 puan; aralık yok (min = max)
    return {"name": name, "label": label + " (0-100 puan)", "unit": "puan", "mean": score, "min": score,
            "max": score, "confidence": confidence, "note": note,
            "details": {k: rec[k] for k in ("key", "name", "sequence", "sequence_names", "score", "risk", "rank",
                                            "climate_fit", "components", "why", "benefits", "damages",
                                            "expected_yield_loss_pct", "risks_for_local_review", "evidence")
                        if k in rec} | ({"delta_vs_existing": rec["delta_vs_existing"]}
                                        if "delta_vs_existing" in rec else {})}


def rotation_layers(rot: dict, synthetic: bool = False) -> list[dict]:
    conf = float(rot["confidence"])
    out = [rotation_layer(f"rotasyon_{i}", f"Rotasyon önerisi #{i}: {r['name']}", r, conf, synthetic)
           for i, r in enumerate(rot["recommended"], 1)]
    if rot.get("existing"):
        out.append(rotation_layer("rotasyon_mevcut", f"Mevcut rotasyon: {rot['existing']['name']}",
                                  rot["existing"], conf, synthetic))
    if rot.get("chosen"):
        out.append(rotation_layer("rotasyon_secilen", f"Çiftçinin seçtiği rotasyon: {rot['chosen']['name']}",
                                  rot["chosen"], conf, synthetic))
    return out


def to_layers(res: dict) -> list[dict]:
    syn = res.get("synthetic_model", False)
    tag = " (SENTETİK veriyle eğitilmiş model)" if syn else ""
    r = res["risk"]
    conf = float(r["confidence"])
    layers = []
    f = res.get("forecast")
    if f:
        top = ", ".join(t["name"] for t in f["top_features"][:3])
        layers.append({
            "name": "ndvi_tahmin", "label": f"{f['target_week']} haftası bitki örtüsü tahmini (NDVI)", "unit": "NDVI",
            "mean": f["ndvi_p50"], "min": f["ndvi_p10"], "max": f["ndvi_p90"], "confidence": conf,
            "note": (f"Son uydu ölçümü {f['ndvi_last_observed']:.2f} ({f['ndvi_last_observed_week']} haftası). "
                     f"Aralık %80 tahmin aralığıdır. En etkili etkenler: {top}."
                     + (" Uydu gecikmesi nedeniyle bu, bugüne yakın durumun tahminidir." if f["is_nowcast"] else "")
                     + tag),
            "details": {"forecast": f},
        })
    if r.get("overall_0_100") is not None:
        comp = ", ".join(f"{k}: {v['level']}" for k, v in r["components"].items() if v["score"] is not None)
        layers.append({"name": "risk", "label": "Genel risk skoru (önümüzdeki haftalar, 0-100)", "unit": "puan",
                       "mean": r["overall_0_100"], "min": r["overall_0_100"], "max": r["overall_0_100"],
                       "confidence": conf,
                       "note": f"Bileşenler: {comp}." + (" Eksik: " + ", ".join(r["missing_components"]) + "."
                                                        if r["missing_components"] else "") + tag,
                       "details": {"risk": r}})
    c = res["area_conditions"]
    if np.isfinite(c.get("summer_precip_mm", np.nan)):
        bits = []
        for k, lab, fmt in (("annual_gdd", "yıllık büyüme derece günü", "{:.0f}"),
                            ("summer_heat_days", "yazın 35 °C üstü gün", "{:.0f}"),
                            ("spring_frost_days", "mayıs-haziran donlu gün", "{:.0f}"),
                            ("p95_daily_precip_mm", "yoğun yağış (günlük, %95)", "{:.0f} mm")):
            v = c.get(k)
            if v is not None and np.isfinite(v):
                bits.append(f"{lab} {fmt.format(v)}")
        ws = c.get("water_stress")
        layers.append({"name": "iklim", "label": f"Yaz yağışı (mayıs-eylül, {len(c['years'])} yıl ort.)",
                       "unit": "mm", "mean": round(float(c["summer_precip_mm"]), 1),
                       "min": round(float(c.get("summer_precip_min_mm", c["summer_precip_mm"])), 1),
                       "max": round(float(c.get("summer_precip_max_mm", c["summer_precip_mm"])), 1),
                       "confidence": min(0.9, 0.5 + 0.1 * len(c["years"])),
                       "note": "; ".join(bits) + (f". Uzun vadeli su stresi {R.level(ws)}" if ws is not None and
                                                  np.isfinite(ws) else "") + ".",
                       "details": {"area_conditions": c}})
    layers += rotation_layers(res["rotations"], syn)
    return layers
