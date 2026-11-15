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
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from ml.data_load import clean, download, features as F
from ml.data_load import static as ST
from ml.data_load.build import load_config
from ml.model import outlook as OL
from ml.model.i18n import Bi, en, en_list
from ml.model import risk as R
from ml.model import rotation as ROT
from ml.model.predict import Model

log = logging.getLogger("ml.service")
ML_DIR = Path(__file__).resolve().parent
AREA = "parcel"

Progress = Callable[[str, dict], None]


def _noop(_stage: str, _info: dict) -> None:
    pass


CONUS = (24.0, 50.0, -125.0, -66.0)  # CDL ve SSURGO yalnızca ABD (kıta) için
_BG = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ml-static")  # beklenmeden sürebilir (sonuç önbelleğe)


def in_conus(lat: float, lon: float) -> bool:
    return CONUS[0] <= lat <= CONUS[1] and CONUS[2] <= lon <= CONUS[3]


class DataUnavailable(RuntimeError):
    """Hiç iklim verisi alınamadı (analiz yapılamaz)."""


class ParcelService:
    def __init__(self, model_dir: str | Path | None = None, cache_dir: str | Path | None = None,
                 history_years: int = 3, ndvi_weeks: int = 22, ndvi_latency_days: int = 18, ndvi_timeout: float = 60, ndvi_retries: int = 3,
                 power_timeout: float = 30, power_retries: int = 2, cache_days: int = 1):
        self.model = Model.load(model_dir)
        cov = self.model.schema.get("interval_coverage_test")
        if cov is None and (self.model.path / "metrics.json").exists():  # eski modeller: metrics.json'dan oku
            m = json.loads((self.model.path / "metrics.json").read_text())
            cov = m.get("test_temporal", {}).get("interval_conformal", {}).get("coverage")
        _INTERVAL_COV[self.model.schema["model_version"]] = cov
        self.cfg = load_config()
        self.cache_dir = Path(cache_dir) if cache_dir else ML_DIR / "cache" / "parcels"
        self.history_years, self.ndvi_weeks = history_years, ndvi_weeks
        self.ndvi_latency_days = ndvi_latency_days  # bundan yeni kompozit henüz yayınlanmamıştır (boş istek atma)
        self.ndvi_timeout, self.ndvi_retries = ndvi_timeout, ndvi_retries
        self.power_timeout, self.power_retries = power_timeout, power_retries
        self.cache_days = cache_days
        self.static_wait_s = 12.0  # NDVI bittikten sonra toprak/CDL için en fazla bu kadar daha beklenir (CDL sık çöker)
        self._lock = threading.Lock()

    # ------------------------------------------------------------ veri
    def _cache_paths(self, lat: float, lon: float, today: dt.date) -> tuple[Path, Path]:
        # Dikkat: Path.with_suffix kullanılmaz; koordinattaki nokta uzantı sanılıp boylam ve tarih silinirdi.
        key = f"{lat:.3f}_{lon:.3f}_{today.isoformat()}"
        return self.cache_dir / f"{key}.parquet", self.cache_dir / f"{key}.json"

    def fetch(self, lat: float, lon: float, today: dt.date, progress: Progress = _noop) -> tuple[pd.DataFrame, dict]:
        """Ham veri + kaynak durumu. Aynı gün aynı nokta için diskteki önbellekten okur."""
        pq, meta = self._cache_paths(lat, lon, today)
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
            # 1) Planetary Computer (hızlı, Terra + Aqua); 2) olmazsa ORNL (yavaş, yalnızca Terra)
            try:
                from ml.data_load import planetary  # noqa: PLC0415

                df = planetary.fetch_area(AREA, lat, lon, n_start, today, workers=12)
                if not df.empty:
                    progress("data", {"source": "ndvi", "status": "done", "rows": len(df), "provider": "planetary"})
                    return df
                log.warning("Planetary Computer NDVI boş döndü; ORNL deneniyor")
            except Exception as e:  # noqa: BLE001
                log.warning("Planetary Computer NDVI alınamadı (%s); ORNL deneniyor", str(e)[:150])
            n_end = today - dt.timedelta(days=self.ndvi_latency_days)
            df = download.fetch_modis_ornl(AREA, lat, lon, n_start, n_end, "MOD13Q1", timeout=self.ndvi_timeout,
                                           retries=self.ndvi_retries, parallel=True, patient=False)
            progress("data", {"source": "ndvi", "status": "done", "rows": len(df), "provider": "ornl"})
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
        tmp = pq.parent / (pq.name + ".tmp")
        raw.to_parquet(tmp, index=False)
        tmp.replace(pq)
        meta.write_text(json.dumps(status))
        self._prune_cache(today)
        return raw, status

    def _prune_cache(self, today: dt.date) -> None:
        for p in self.cache_dir.iterdir():
            name = p.name.removesuffix(".tmp")
            stem = name.rsplit(".", 1)[0] if name.endswith((".parquet", ".json")) else ""
            try:
                d = dt.date.fromisoformat(stem.rsplit("_", 1)[1])
            except (ValueError, IndexError):
                p.unlink(missing_ok=True)  # eski/bozuk adlı dosya (tarihsiz): güvenli değil, sil
                continue
            if (today - d).days > self.cache_days:
                p.unlink(missing_ok=True)

    def _cached(self, kind: str, lat: float, lon: float, today: dt.date) -> Path:
        return self.cache_dir / f"{kind}_{lat:.3f}_{lon:.3f}_{today.isoformat()}.json"

    def fetch_soil(self, lat: float, lon: float, today: dt.date) -> dict:
        """SSURGO toprak (ABD, ~1 sn). Yoksa (ABD dışı ya da servis hatası) SoilGrids + yükseklik modelinden
        HESAPLANAN tahmin (`soil_global`, ~10-15 sn, kalıcı önbellek). Günlük önbellekli."""
        path = self._cached("soil", lat, lon, today)
        if path.exists():
            return json.loads(path.read_text())
        from ml.data_load import soil_global as SG  # noqa: PLC0415

        soil, errors = None, []
        if in_conus(lat, lon):
            from ml.data_load import fields as FL  # noqa: PLC0415

            cache = FL.default_cache()
            try:
                soil = FL.soil_at(lat, lon, cache)
            except Exception as e:  # noqa: BLE001
                errors.append(f"SSURGO toprak alınamadı ({str(e)[:80]}); küresel tahmin kullanıldı")
            finally:
                cache.flush()
        gcache = ML_DIR / "cache" / "static_global"
        if soil is None:
            est = SG.estimate(lat, lon, gcache)
            soil, errors = est["soil"], errors + est["errors"]
        elif soil.get("slope_pct") is None:  # SSURGO var ama eğim yok: yalnızca eğimi hesapla
            est = SG.estimate(lat, lon, gcache, need_soil=False)
            if est["soil"] and est["soil"].get("slope_pct") is not None:
                soil = {**soil, "slope_pct": est["soil"]["slope_pct"], "estimated_keys": ["slope_pct"],
                        "source": "SSURGO + Copernicus GLO-90 eğim"}
        out = {"soil": soil, "errors": errors}
        if soil is not None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(out, default=str))
        return out

    def fetch_cdl(self, lat: float, lon: float, today: dt.date) -> dict:
        """USDA CDL ürün geçmişi (ABD, son 6 yıl) + çıkarılan mevcut rotasyon. Servis sık sık yavaş/çökük.
        Günlük önbellekli; zamanında gelmezse arka planda sürer ve sonraki analizde önbellekten gelir."""
        path = self._cached("cdl", lat, lon, today)
        if path.exists():
            return json.loads(path.read_text())
        if not in_conus(lat, lon):
            return {"cdl": {}, "rotation": None, "errors": []}
        from ml.data_load import fields as FL  # noqa: PLC0415

        cache = FL.default_cache()
        try:
            hist = FL.cdl_history(lat, lon, range(today.year - 6, today.year), cache, workers=3)
        except Exception as e:  # noqa: BLE001
            return {"cdl": {}, "rotation": None, "errors": [f"ürün geçmişi alınamadı: {str(e)[:120]}"]}
        finally:
            cache.flush()
        cdl = {str(int(y)): (None if pd.isna(k) else str(k)) for y, k in zip(hist["year"], hist["crop_key"])}
        out = {"cdl": cdl, "rotation": FL.infer_rotation(hist), "errors": []}
        if any(cdl.values()):
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(out, default=str))
        return out

    def start_static(self, lat: float, lon: float, today: dt.date) -> dict:
        """Toprak ve CDL'yi ayrı ayrı arka planda başlatır (biri yavaşsa diğeri beklemez)."""
        return {"soil": _BG.submit(self.fetch_soil, lat, lon, today),
                "cdl": _BG.submit(self.fetch_cdl, lat, lon, today)}

    def fetch_static(self, lat: float, lon: float, today: dt.date, started: dict | None = None) -> dict:
        """Toprak + CDL sonucunu toplar; her biri için en fazla `static_wait_s` bekler. Gelmeyen boş kalır."""
        futs = started or self.start_static(lat, lon, today)
        out: dict = {"soil": None, "cdl": {}, "rotation": None, "errors": []}
        if not in_conus(lat, lon):
            out["errors"].append("ABD dışı: toprak (SSURGO) ve ürün geçmişi (CDL) yok")
        deadline = time.monotonic() + self.static_wait_s
        for kind, label, label_en in (("soil", "toprak (SSURGO)", "soil (SSURGO)"),
                                      ("cdl", "ürün geçmişi (CDL)", "crop history (CDL)")):
            try:
                res = futs[kind].result(timeout=max(0.1, deadline - time.monotonic()))
            except Exception:  # noqa: BLE001  (zaman aşımı: arka planda sürer, sonuç önbelleğe yazılır)
                out["errors"].append(Bi(f"{label} zamanında gelmedi", f"{label_en} did not arrive in time"))
                continue
            out["errors"] += res.get("errors", [])
            if kind == "soil":
                out["soil"] = res.get("soil")
            else:
                out["cdl"], out["rotation"] = res.get("cdl", {}), res.get("rotation")
        return out

    def fetch_outlook(self, lat: float, lon: float, today: dt.date) -> dict | None:
        """Hava görünümü (15 yıllık POWER geçmişinden). Günlük önbellek: geçmiş her gün bir gün uzar."""
        path = self._cached("outlook", lat, lon, today)
        if path.exists():
            hit = json.loads(path.read_text())
            return None if hit.get("insufficient_history") else hit
        daily = OL.fetch_daily(lat, lon, today, timeout=self.power_timeout, retries=self.power_retries)
        res = OL.outlook(daily)
        self.cache_dir.mkdir(parents=True, exist_ok=True)  # yetersiz geçmiş de önbelleğe (her analizde yeniden çekme)
        path.write_text(json.dumps(res if res is not None else {"insufficient_history": True}))
        return res

    def build_features(self, raw: pd.DataFrame) -> pd.DataFrame:
        dc = self.cfg.get("data", {})
        long, _rep = clean.clean_frame(raw, hours=tuple(dc.get("hours", (0, 6, 12, 18))))
        if long.empty:
            raise DataUnavailable("Veri temizlendikten sonra kullanılabilir değer kalmadı.")
        daily, centers = F.to_daily(long, dc.get("composite_anchor", "end"))
        weekly = F.to_weekly(daily)
        return F.build_features(weekly, centers, horizon_weeks=int(self.model.schema["horizon_weeks"]),
                                ndvi_lags=dc.get("ndvi_lags", 8), max_gap_weeks=dc.get("max_gap_weeks", 2),
                                feature_fill=dc.get("feature_fill", "ffill"))

    # ------------------------------------------------------------ analiz
    def analyze(self, lon: float, lat: float, today: dt.date | None = None, existing=None, chosen=None,
                priorities: dict | None = None, profile: dict | None = None,
                progress: Progress = _noop, custom_crops: list[dict] | None = None,
                custom_rotations: list[dict] | None = None) -> dict:
        today = today or dt.date.today()
        started = self.start_static(lat, lon, today)  # NDVI beklenirken paralel
        outlook_fut = _BG.submit(self.fetch_outlook, lat, lon, today)
        raw, status = self.fetch(lat, lon, today, progress)
        stat = self.fetch_static(lat, lon, today, started=started)
        try:
            wx = outlook_fut.result(timeout=20)
        except Exception as e:  # noqa: BLE001  (görünüm yoksa analiz yine biter)
            log.warning("hava görünümü hesaplanamadı: %s", e)
            wx = None
        progress("data", {"source": "outlook", "status": "done" if wx else "failed"})
        progress("data", {"source": "soil", "status": "done" if stat.get("soil") else "failed"})
        progress("data", {"source": "crop_history", "status": "done" if any(stat.get("cdl", {}).values()) else "failed"})
        progress("features", {"status": "started"})
        feats = self.build_features(raw)
        cdl_hist = {int(y): k for y, k in stat.get("cdl", {}).items()}
        feats, _srep = ST.add_static_features(feats, soil={AREA: stat["soil"]} if stat.get("soil") else None,
                                              cdl={AREA: cdl_hist} if cdl_hist else None)
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
                warnings.append(Bi(
                    f"Uydu NDVI verisi gecikmeli (son ölçüm {pred['ndvi_last_observed_week']} haftası); "
                    f"tahmin {pred['target_week']} haftası için, yani bugüne yakın durumun tahminidir.",
                    f"Satellite NDVI is delayed (last measurement week of {pred['ndvi_last_observed_week']}); the "
                    f"forecast is for the week of {pred['target_week']}, i.e. an estimate of the current state."))
        else:
            warnings.append(Bi("NDVI (bitki örtüsü) verisi alınamadı; bitki tahmini yapılamadı, risk ve rotasyon "
                               "yalnızca iklim verisinden hesaplandı.",
                               "NDVI (vegetation) data could not be fetched; no vegetation forecast was made, and risk "
                               "and rotations were computed from climate data only."))
        warnings += [Bi(f"Not: {e}", f"Note: {en(e)}") for e in stat.get("errors", []) if not e.startswith("ABD dışı")]
        profile = dict(profile or {})
        soil = stat.get("soil") or {}
        est_keys = set(soil.get("estimated_keys", []))
        estimated: list[str] = []
        for k_src, k_dst in (("om_pct", "soil_om_pct"), ("slope_pct", "slope_pct"), ("soil_texture", "soil_texture")):
            if soil.get(k_src) is not None and k_dst not in profile:
                profile[k_dst] = soil[k_src]
                if k_src in est_keys:
                    estimated.append(k_dst)
        if estimated:
            profile["_estimated"] = estimated
            vals = ", ".join(f"{k} {profile[k]}" for k in estimated)
            src = soil.get("source", "küresel kaynak")
            warnings.append(Bi(f"Toprak/eğim ölçülmedi, hesaplandı ({src}): {vals}. Tahmindir; bildiğiniz değerleri "
                               "çiftçi bilgisine girin.",
                               f"Soil/slope were not measured but calculated ({src.replace('eğim', 'slope')}): "
                               f"{vals}. These are estimates; enter values you know in the farmer's data."))
        rot_info = stat.get("rotation") or {}
        existing_source = "çiftçi" if existing else None
        if not existing and rot_info.get("rotation") and rot_info.get("confidence", 0) >= 0.5:
            existing, existing_source = rot_info["rotation"], "USDA CDL"
            names = ROT.catalog()["crops"]
            seq_tr = " → ".join(names.get(c, {}).get("name", c) for c in ROT.parse_rotation(existing))
            if "→" not in seq_tr:
                seq_tr = f"sürekli {seq_tr.lower()} (monokültür)"
            seq_en = " → ".join(names.get(c, {}).get("name_en", c) for c in ROT.parse_rotation(existing))
            if "→" not in seq_en:
                seq_en = f"continuous {seq_en.lower()} (monoculture)"
            warnings.append(Bi(f"Mevcut rotasyon USDA CDL ürün haritasından otomatik çıkarıldı: {seq_tr} "
                               f"(eminlik {rot_info['confidence']:.2f}); yanlışsa çiftçi düzeltmeli.",
                               f"Current rotation was inferred from the USDA CDL crop map: {seq_en} (confidence "
                               f"{rot_info['confidence']:.2f}); the farmer should correct it if wrong."))
        if missing:
            warnings.append(Bi(f"Modelin kullandığı {len(missing)} özellik bu parsel için yok (örn. {missing[0]}); "
                               "eminlik buna göre düşük olabilir.",
                               f"{len(missing)} model feature(s) are missing for this parcel (e.g. {missing[0]}); "
                               "confidence may be lower."))

        # Risk: iklim bileşenleri en güncel haftadan, bitki bileşeni son NDVI haftasından
        row = latest.copy()
        if ndvi_row is not None:
            row["ndvi"], row["week"] = ndvi_row["ndvi"], ndvi_row["week"]
        ref = R.seasonal_reference(feats, latest["week"])
        cov = float(latest.get("_coverage", 0.5)) if pd.notna(latest.get("_coverage", np.nan)) else 0.5
        srisk = R.short_term_risk(row, pred, None, ref, cov, int(self.model.schema["horizon_weeks"]), outlook=wx)
        if missing:
            srisk["confidence"] = round(srisk["confidence"] * (1 - 0.5 * len(missing) / len(self.model.features)), 3)
            srisk["confidence_level"] = R.confidence_level(srisk["confidence"])
        progress("rotations", {"status": "started"})
        cond = R.area_climate(feats)
        state = {"cond": cond, "risk": srisk, "profile": profile, "model_confidence": srisk["confidence"]}
        rot = ROT.recommend(cond, existing, priorities, profile, srisk,
                            model_confidence=srisk["confidence"], chosen=chosen,
                            extra_crops=custom_crops, extra_rotations=custom_rotations)
        return {
            "as_of": today.isoformat(),
            "data_sources": status,
            "data_as_of": {"weather": str(pd.Timestamp(latest["week"]).date()),
                           "ndvi": pred["ndvi_last_observed_week"] if pred else None},
            "forecast": pred, "risk": srisk, "area_conditions": cond, "rotations": rot, "outlook": wx,
            "warnings": warnings, "warnings_en": en_list(warnings), "missing_features": missing,
            "field_info": {"soil": soil or None, "crop_history": stat.get("cdl", {}),
                           "inferred_rotation": rot_info or None, "existing_rotation_source": existing_source},
            "model_version": self.model.schema["model_version"],
            "synthetic_model": bool(self.model.schema.get("synthetic_input")),
            "rescore_state": state,
        }

    @staticmethod
    def rescore(state: dict, existing=None, chosen=None, priorities: dict | None = None,
                profile: dict | None = None, custom_crops: list[dict] | None = None,
                custom_rotations: list[dict] | None = None) -> dict:
        """Veri çekmeden rotasyonları yeniden puanlar (çiftçi kendi rotasyonunu/önceliklerini/ürünlerini seçince)."""
        prof = {**state.get("profile", {}), **(profile or {})}
        if profile and prof.get("_estimated"):  # çiftçinin girdiği değer artık tahmin değil
            prof["_estimated"] = [k for k in prof["_estimated"] if k not in profile]
        return ROT.recommend(state["cond"], existing, priorities, prof, state["risk"],
                             model_confidence=state["model_confidence"], chosen=chosen,
                             extra_crops=custom_crops, extra_rotations=custom_rotations)


# ---------------------------------------------------------------- backend Layer biçimi
_INTERVAL_COV: dict[str, float | None] = {}


def interval_text(f: dict) -> str:
    """Aralığı dürüst anlat: testte gerçekten ölçülen kapsamayı yaz (hedef %80, yaz aylarında daha düşük olabilir)."""
    cov = _INTERVAL_COV.get(f.get("model_version"))
    if cov:
        return f"Aralık, testte gerçek değerleri yaklaşık %{100 * cov:.0f} oranında kapsadı (hedef %80)."
    return "Aralık yaklaşık %80 tahmin aralığıdır."


def ndvi_label(v: float) -> str:
    """NDVI sayısını yorum etiketine çevirir (yorum backend'de; model sayıyı yanlış okumasın)."""
    if v < 0.15:
        return "çıplak toprak / bitki yok"
    if v < 0.30:
        return "çok seyrek bitki örtüsü (hasat sonrası, ekim öncesi ya da kış olabilir)"
    if v < 0.50:
        return "seyrek-orta bitki örtüsü (gelişme başı ya da stresli bitki olabilir)"
    if v < 0.70:
        return "orta-yoğun bitki örtüsü"
    return "yoğun, sağlıklı bitki örtüsü"


def ndvi_label_en(v: float) -> str:
    if v < 0.15:
        return "bare soil / no vegetation"
    if v < 0.30:
        return "very sparse vegetation (post-harvest, pre-sowing or winter)"
    if v < 0.50:
        return "sparse to moderate vegetation (early growth or stressed plants)"
    if v < 0.70:
        return "moderate to dense vegetation"
    return "dense, healthy vegetation"


def _damage_text(rec: dict, n: int = 3) -> str:
    parts = []
    for d in rec["damages"][:n]:
        loss = f" (olası verim kaybı %{d['yield_loss_pct'][0]:.0f}–{d['yield_loss_pct'][1]:.0f})" \
            if d.get("yield_loss_pct") else ""
        parts.append(f"{d['risk']} [{d['severity']}] {d['affects']}: {d['impact']}{loss}")
    return "; ".join(parts) if parts else "belirgin zarar beklenmiyor"


def rotation_layer(name: str, label: str, rec: dict, confidence: float, synthetic: bool,
                   label_en: str | None = None) -> dict:
    seq = " → ".join(rec["sequence_names"])
    note = (f"Sıra: {seq}. Neden: {'; '.join(rec['why'])}. "
            f"Faydalar: {'; '.join(rec['benefits']) or 'belirgin değil'}. "
            f"Olası zararlar: {_damage_text(rec)}. Risk puanı {100 * rec['risk']:.0f}/100.")
    if "delta_vs_existing" in rec:
        d = rec["delta_vs_existing"]
        ds, dr = round(100 * d["score"]), round(100 * d["risk"])
        note += (f" Mevcut rotasyona göre puan farkı {ds:+d}; "
                 + (f"risk farkı {dr:+d}." if dr != 0 else "risk mevcut rotasyonla aynı düzeyde."))
    if rec.get("economics"):
        e = rec["economics"]
        note += (f" Çiftçinin kendi verim/fiyat/maliyet hesabıyla sezon başına ortalama brüt kâr "
                 f"{e['mean_gross_margin_per_ha']:.0f} (para birimi/ha, tahmini verim kaybı düşüldü).")
    if rec.get("source") == "otomatik":
        note += " (Sabit listede yoktu; ürün kurallarından otomatik üretilmiş aday.)"
    if synthetic:
        note += " (SENTETİK veriyle eğitilmiş model)"
    seq_en = " → ".join(rec.get("sequence_names_en") or rec["sequence_names"])
    dmg_en = "; ".join(
        f"{d.get('risk_en', d['risk'])} [{SEV_EN.get(d['severity'], d['severity'])}] {d.get('affects_en', d['affects'])}: "
        f"{d.get('impact_en', d['impact'])}"
        + (f" (possible yield loss {d['yield_loss_pct'][0]:.0f}–{d['yield_loss_pct'][1]:.0f}%)" if d.get("yield_loss_pct") else "")
        for d in rec["damages"][:3]) or "no notable damage expected"
    note_en = (f"Sequence: {seq_en}. Why: {'; '.join(rec.get('why_en', rec['why']))}. "
               f"Benefits: {'; '.join(rec.get('benefits_en', rec['benefits'])) or 'none notable'}. "
               f"Possible damages: {dmg_en}. Risk score {100 * rec['risk']:.0f}/100.")
    if "delta_vs_existing" in rec:
        d = rec["delta_vs_existing"]
        ds, dr = round(100 * d["score"]), round(100 * d["risk"])
        note_en += f" Versus the current rotation: score {ds:+d}, " + (f"risk {dr:+d}." if dr else "same risk.")
    if rec.get("economics"):
        note_en += (f" With the farmer's own yield/price/cost: mean gross margin per season "
                    f"{rec['economics']['mean_gross_margin_per_ha']:.0f} per ha (estimated losses subtracted).")
    if rec.get("source") == "otomatik":
        note_en += " (Not in the fixed list; auto-generated from crop rules.)"
    score = round(100 * rec["score"], 1)  # 0-100 puan; aralık yok (min = max)
    return {"name": name, "label": label + " (0-100 puan)", "unit": "puan", "mean": score, "min": score,
            "max": score, "confidence": confidence, "note": note,
            "details": {k: rec[k] for k in ("key", "name", "sequence", "sequence_names", "score", "risk", "rank",
                                            "climate_fit", "components", "why", "benefits", "damages",
                                            "expected_yield_loss_pct", "risks_for_local_review", "evidence",
                                            "source", "economics")
                        if k in rec}
                       | {k: rec[k] for k in ("name_en", "sequence_names_en", "why_en", "benefits_en",
                                              "risks_for_local_review_en") if k in rec}
                       | {"label_en": (label_en or en(rec["name"])) + " (0-100 points)", "note_en": note_en}
                       | ({"delta_vs_existing": rec["delta_vs_existing"]} if "delta_vs_existing" in rec else {})}


MAX_CUSTOM_LAYERS = 5


def rotation_layers(rot: dict, synthetic: bool = False) -> list[dict]:
    conf = float(rot["confidence"])
    out = [rotation_layer(f"rotasyon_{i}", f"Rotasyon önerisi #{i}: {r['name']}", r, conf, synthetic,
                          f"Rotation suggestion #{i}: {en(r['name'])}")
           for i, r in enumerate(rot["recommended"], 1)]
    for i, r in enumerate(rot.get("custom", [])[:MAX_CUSTOM_LAYERS], 1):
        n = rot.get("n_candidates", "?")
        out.append(rotation_layer(f"rotasyon_ozel_{i}", f"Çiftçinin oluşturduğu rotasyon: {r['name']} "
                                  f"(sıra {r['rank']}/{n})", r, conf, synthetic,
                                  f"Farmer's rotation: {en(r['name'])} (rank {r['rank']}/{n})"))
    if rot.get("existing"):
        out.append(rotation_layer("rotasyon_mevcut", f"Mevcut rotasyon: {rot['existing']['name']}",
                                  rot["existing"], conf, synthetic, f"Current rotation: {en(rot['existing']['name'])}"))
    if rot.get("chosen"):
        out.append(rotation_layer("rotasyon_secilen", f"Çiftçinin seçtiği rotasyon: {rot['chosen']['name']}",
                                  rot["chosen"], conf, synthetic, f"Farmer's choice: {en(rot['chosen']['name'])}"))
    return out


LEVEL_EN = {"yüksek": "high", "orta": "medium", "düşük": "low", "bilinmiyor": "unknown"}
SEV_EN = LEVEL_EN


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
            "note": (f"Tahmin edilen durum: {ndvi_label(f['ndvi_p50'])}. Son uydu ölçümü "
                     f"{f['ndvi_last_observed']:.2f} = {ndvi_label(f['ndvi_last_observed'])} "
                     f"({f['ndvi_last_observed_week']} haftası). NDVI ölçeği: <0.3 çok seyrek/çıplak, "
                     "0.3-0.5 seyrek-orta, 0.5-0.7 orta-yoğun, >0.7 yoğun. "
                     f"{interval_text(f)} En etkili etkenler: {top}."
                     + (" Uydu gecikmesi nedeniyle bu, bugüne yakın durumun tahminidir." if f["is_nowcast"] else "")
                     + tag),
            "details": {"forecast": f,
                        "label_en": f"Vegetation forecast (NDVI), week of {f['target_week']}",
                        "note_en": (f"Expected state: {ndvi_label_en(f['ndvi_p50'])}. Last satellite reading "
                                    f"{f['ndvi_last_observed']:.2f} = {ndvi_label_en(f['ndvi_last_observed'])} (week of "
                                    f"{f['ndvi_last_observed_week']}). NDVI scale: <0.3 very sparse/bare, 0.3-0.5 "
                                    "sparse-moderate, 0.5-0.7 moderate-dense, >0.7 dense. Main drivers: "
                                    f"{top}." + (" Because of the satellite delay this estimates the current state."
                                                 if f["is_nowcast"] else ""))},
        })
    if r.get("overall_0_100") is not None:
        comp = ", ".join(f"{k}: {v['level']}" for k, v in r["components"].items() if v["score"] is not None)
        layers.append({"name": "risk", "label": "Genel risk skoru (önümüzdeki haftalar, 0-100)", "unit": "puan",
                       "mean": r["overall_0_100"], "min": r["overall_0_100"], "max": r["overall_0_100"],
                       "confidence": conf,
                       "note": f"Bileşenler: {comp}." + (" Eksik: " + ", ".join(r["missing_components"]) + "."
                                                        if r["missing_components"] else "") + tag,
                       "details": {"risk": r, "label_en": "Overall risk score (coming weeks, 0-100)",
                                   "note_en": "Components: " + ", ".join(
                                       f"{k.replace('_', ' ')}: {LEVEL_EN.get(v['level'], v['level'])}"
                                       for k, v in r["components"].items() if v["score"] is not None) + "."}})
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
        bits_en = []
        for k, lab, fmt in (("annual_gdd", "annual growing degree days", "{:.0f}"),
                            ("summer_heat_days", "summer days above 35 °C", "{:.0f}"),
                            ("spring_frost_days", "May-June frost days", "{:.0f}"),
                            ("p95_daily_precip_mm", "heavy rain (daily, 95th pct)", "{:.0f} mm")):
            v = c.get(k)
            if v is not None and np.isfinite(v):
                bits_en.append(f"{lab} {fmt.format(v)}")
        note_en = "; ".join(bits_en) + (f". Long-term water stress {LEVEL_EN.get(R.level(ws), R.level(ws))}"
                                        if ws is not None and np.isfinite(ws) else "") + "."
        layers.append({"name": "iklim", "label": f"Yaz yağışı (mayıs-eylül, {len(c['years'])} yıl ort.)",
                       "unit": "mm", "mean": round(float(c["summer_precip_mm"]), 1),
                       "min": round(float(c.get("summer_precip_min_mm", c["summer_precip_mm"])), 1),
                       "max": round(float(c.get("summer_precip_max_mm", c["summer_precip_mm"])), 1),
                       "confidence": min(0.9, 0.5 + 0.1 * len(c["years"])),
                       "note": "; ".join(bits) + (f". Uzun vadeli su stresi {R.level(ws)}" if ws is not None and
                                                  np.isfinite(ws) else "") + ".",
                       "details": {"area_conditions": c, "note_en": note_en,
                                   "label_en": f"Summer rainfall (May-Sep, {len(c['years'])}-year mean)"}})
    layers += outlook_layers(res.get("outlook"))
    layers += rotation_layers(res["rotations"], syn)
    return layers


def outlook_layers(o: dict | None) -> list[dict]:
    """Hava görünümü -> iki katman (sıcaklık, yağış; 1-2. hafta). Ayrıntıda 4 haftanın tamamı."""
    if not o or not o.get("weeks"):
        return []
    w1, w2 = o["weeks"][0], o["weeks"][min(1, len(o["weeks"]) - 1)]
    span = f"{w1['start']} – {w2['end']}"
    t, p = OL.two_week(o, "t2m"), OL.two_week(o, "precip")
    fr, ht, gd = OL.two_week(o, "frost_days"), OL.two_week(o, "heat_days"), OL.two_week(o, "gdd")
    tmin, tmax = OL.two_week(o, "tmin"), OL.two_week(o, "tmax")
    sk = o["skill"]["t2m"][0] or 0
    skill_txt = ("tahmin mevsim normalinden belirgin şekilde iyi değil; değerleri 'bu dönemin normali' gibi okuyun"
                 if sk < 0.1 else f"1. hafta tahmini mevsim normalinden %{100 * sk:.0f} daha isabetli")
    later = o["weeks"][2:]
    later_txt = "; ".join(f"{w['start']} haftası ort. {w['vars']['t2m']['p50']:.1f} °C, yağış "
                          f"{w['vars']['precip']['p50']:.0f} mm, donlu gün {w['vars']['frost_days']['p50']:.1f}"
                          for w in later)
    diff = t["p50"] - t["normal"]
    skill_en = ("the forecast is not clearly better than the seasonal normal; read the values as 'normal for this "
                "time of year'" if sk < 0.1 else f"week 1 is {100 * sk:.0f}% more accurate than the seasonal normal")
    later_en = "; ".join(f"week of {w['start']} mean {w['vars']['t2m']['p50']:.1f} °C, rain "
                         f"{w['vars']['precip']['p50']:.0f} mm, frost days {w['vars']['frost_days']['p50']:.1f}"
                         for w in later)
    details = {"outlook": o, "label_en": f"Mean temperature, next 2 weeks ({span})",
               "note_en": (f"Seasonal normal {t['normal']:.1f} °C (difference {diff:+.1f}). Mean daily low "
                           f"{tmin['p50']:.1f} °C, mean daily high {tmax['p50']:.1f} °C. Expected frost days "
                           f"{fr['p50']:.1f} (range {fr['p10']:.0f}–{fr['p90']:.0f}), days above 35 °C {ht['p50']:.1f}, "
                           f"growing degree days {gd['p50']:.0f}. {skill_en}. Following weeks: {later_en}. Source: "
                           f"{o['history_years']} years of NASA POWER history for the field's coordinates, latest data "
                           f"{o['issued_from']}.")}
    temp = {
        "name": "hava_tahmini", "label": f"Önümüzdeki 2 hafta ortalama sıcaklık ({span})", "unit": "°C",
        "mean": t["p50"], "min": t["p10"], "max": t["p90"], "confidence": OL.confidence(o, "t2m"),
        "note": (f"Mevsim normali {t['normal']:.1f} °C (fark {diff:+.1f}). Günlük en düşük ort. {tmin['p50']:.1f} °C, "
                 f"en yüksek ort. {tmax['p50']:.1f} °C. Beklenen donlu gün {fr['p50']:.1f} "
                 f"(aralık {fr['p10']:.0f}–{fr['p90']:.0f}), 35 °C üstü gün {ht['p50']:.1f}, büyüme derece günü "
                 f"{gd['p50']:.0f}. {skill_txt}. Sonraki haftalar: {later_txt}. Kaynak: tarlanın koordinatı için "
                 f"{o['history_years']} yıllık NASA POWER geçmişi, son veri {o['issued_from']}."),
        "details": details,
    }
    rain = {
        "name": "yagis_tahmini", "label": f"Önümüzdeki 2 hafta toplam yağış ({span})", "unit": "mm",
        "mean": p["p50"], "min": p["p10"], "max": p["p90"], "confidence": OL.confidence(o, "precip"),
        "note": (f"Mevsim normali {p['normal']:.0f} mm. Yağış birkaç gün öncesinden bile zor tahmin edilir; "
                 "aralık geniştir ve değer büyük ölçüde mevsim normalidir."),
        "details": {"outlook_ref": "hava_tahmini", "label_en": f"Total rainfall, next 2 weeks ({span})",
                    "note_en": (f"Seasonal normal {p['normal']:.0f} mm. Rain is hard to forecast even a few days "
                                "ahead; the range is wide and the value is mostly the seasonal normal.")},
    }
    return [temp, rain]
