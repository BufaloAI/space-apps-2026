"""Rotasyon stratejilerini puanlar ve sıralar (mevcut rotasyon her zaman karşılaştırmada yer alır).

Girdi: alanın uzun vadeli koşulları (`risk.area_climate`), kısa vadeli risk, çiftçi öncelikleri ve yerel profil.
Çıktı: her strateji için puan, risk, güven, neden önerildiği, kanıt, faydalar, bilgi boşlukları, yerel
inceleme gerektiren riskler. Ürün profilleri genel sezgiler olduğu için güven bilinçli olarak sınırlıdır.
"""
from __future__ import annotations

import tomllib
from functools import lru_cache
from pathlib import Path

import numpy as np

CROPS_FILE = Path(__file__).with_name("crops.toml")
DEFAULT_PRIORITIES = {"water": 1.0, "soil_health": 1.0, "income": 1.0, "erosion": 1.0, "forage": 0.3}
PRIORITY_LABELS = {"water": "su tasarrufu", "soil_health": "toprak sağlığı", "income": "gelir/pazar",
                   "erosion": "erozyon azaltma", "forage": "yem değeri"}
PROFILE_CONFIDENCE = 0.7  # ürün profilleri ölçüm değil -> güven tavanı


@lru_cache(maxsize=1)
def catalog() -> dict:
    return tomllib.loads(CROPS_FILE.read_text())


def parse_rotation(spec: str | list[str]) -> list[str]:
    """'corn>soybean' / 'corn,soybean' / ['corn','soybean'] / tanımlı rotasyon anahtarı ('corn_soy')."""
    cat = catalog()
    if isinstance(spec, str):
        if spec in cat["rotations"]:
            return list(cat["rotations"][spec]["seq"])
        spec = [s for s in spec.replace(">", ",").replace("→", ",").split(",") if s.strip()]
    seq = [s.strip() for s in spec]
    for s in seq:
        for c in s.split("+"):
            if c not in cat["crops"]:
                raise ValueError(f"Bilinmeyen ürün: {c!r}. Bilinenler: {sorted(cat['crops'])}")
    return seq


def canonical(seq: list[str]) -> tuple[str, ...]:
    """Döngüsel eşdeğerlik: ['corn','corn'] == ['corn'], ['soybean','corn'] == ['corn','soybean']."""
    n = len(seq)
    for p in range(1, n + 1):  # en kısa periyot
        if n % p == 0 and seq == seq[:p] * (n // p):
            seq = seq[:p]
            break
    return min(tuple(seq[i:] + seq[:i]) for i in range(len(seq)))


def _nz(x, default=0.0):
    return x if x is not None and np.isfinite(x) else default


def crop_fit(crop: dict, cond: dict, irrigated: bool) -> tuple[float, list[str]]:
    """Ürünün alan koşullarına uygunluğu (0..1) ve olumsuz nedenler."""
    notes = []
    ws = _nz(cond.get("water_stress"), 0.3) * (0.4 if irrigated else 1.0)
    hs, fs = _nz(cond.get("heat_stress"), 0.2), _nz(cond.get("frost_stress"), 0.2)
    pen_w = ws * crop["water_need"] * (1 - crop["drought_tol"])
    pen_h = hs * (1 - crop["heat_tol"])
    frost_weight = 1.0 if crop["season"] in ("warm",) else 0.4
    pen_f = fs * (1 - crop["frost_tol"]) * frost_weight
    gdd = cond.get("annual_gdd")
    pen_g = 0.0
    if crop.get("gdd_need") and gdd is not None and np.isfinite(gdd) and crop["season"] == "warm":
        ratio = gdd / crop["gdd_need"]
        if ratio < 1.0:
            pen_g = min(1.0, (1 - ratio) * 2)
            notes.append(f"{crop['name']}: yıllık GDD ({gdd:.0f}) ihtiyacın ({crop['gdd_need']}) altında")
    if pen_w > 0.2:
        notes.append(f"{crop['name']}: su stresi altında su ihtiyacı yüksek")
    if pen_h > 0.2:
        notes.append(f"{crop['name']}: sıcak stresine duyarlı")
    if pen_f > 0.15:
        notes.append(f"{crop['name']}: ilkbahar donuna duyarlı")
    return float(np.clip(1 - pen_w - pen_h - pen_f - pen_g, 0, 1)), notes


def score_rotation(key: str, name: str, seq: list[str], cond: dict, priorities: dict, profile: dict,
                   short_risk: dict | None) -> dict:
    cat = catalog()["crops"]
    irrigated = bool(_nz(profile.get("irrigation"), 0))
    mains = [cat[s.split("+")[0]] for s in seq]
    covers = [cat[c] for s in seq for c in s.split("+")[1:]]
    fits, notes = zip(*(crop_fit(c, cond, irrigated) for c in mains))
    cfit, cnotes = (zip(*(crop_fit(c, cond, irrigated) for c in covers)) if covers else ((), ()))
    climate_fit = float(np.mean(fits) * 0.85 + (np.mean(cfit) * 0.15 if cfit else 0.15 * np.mean(fits)))

    fams = [c["family"] for c in mains]
    real = [f for f in fams if f != "none"]
    diversity = len(set(real)) / max(1, min(3, len(seq)))
    repeated = len(seq) == 1 or any(fams[i] == fams[(i + 1) % len(fams)] and fams[i] != "none"
                                    for i in range(len(fams)))
    perennial = any(c["season"] == "perennial" for c in mains)
    if perennial:  # çok yıllık yonca kendi kendini takip eder; zararlı döngüsünü yine de kırar
        repeated = len(set(fams)) == 1
    n_fix = any(c["n_fix"] for c in mains + covers)
    has_cover = bool(covers)
    om = float(np.mean([c["om"] for c in mains] + [c["om"] for c in covers]))
    soil = float(np.clip(0.30 * n_fix + 0.25 * diversity + 0.25 * om + 0.20 * has_cover - 0.25 * repeated, 0, 1))
    seasons_cover = [max([cat[x]["residue_cover"] for x in s.split("+")]) for s in seq]
    erosion = float(np.mean(seasons_cover))
    water = float(1 - np.mean([c["water_need"] for c in mains]))
    income = float(np.mean([c["market"] for c in mains]))
    forage = float(max(c["forage"] for c in mains + covers))
    comps = {"water": water, "soil_health": soil, "income": income, "erosion": erosion, "forage": forage}

    pr = {**DEFAULT_PRIORITIES, **(priorities or {})}
    # erozyon baskısı / eğim yüksekse erozyon önceliği kendiliğinden artar
    slope = _nz(profile.get("slope_pct"), 0)
    pr["erosion"] *= 1 + _nz(cond.get("erosion_pressure"), 0) + min(1.0, slope / 8)
    pr["water"] *= 1 + _nz(cond.get("water_stress"), 0) * (0.3 if irrigated else 1.0)
    wsum = sum(pr.values())
    prio_score = sum(pr[k] * comps[k] for k in comps) / wsum
    total = 0.40 * climate_fit + 0.60 * prio_score

    area_risk = _nz((short_risk or {}).get("overall"), 0.3)
    risk = float(np.clip((1 - climate_fit) * (0.7 + 0.3 * area_risk) + 0.15 * repeated, 0, 1))

    # açıklamalar
    top = sorted(comps, key=lambda k: -pr[k] * comps[k] / wsum)[:2]
    why = [f"{PRIORITY_LABELS[k]} puanı {comps[k]:.2f}" for k in top]
    if climate_fit >= 0.75:
        why.append(f"alan iklimine uygunluk yüksek ({climate_fit:.2f})")
    benefits = []
    if n_fix:
        benefits.append("baklagil azot bağlar, sonraki ürünün gübre ihtiyacını azaltabilir")
    if has_cover:
        benefits.append("örtü bitkisi kış boyunca toprağı korur, sızmayı ve organik maddeyi artırabilir")
    if diversity >= 0.66 and not repeated:
        benefits.append("ürün ailesi değişiyor, zararlı/hastalık döngüsü kırılabilir")
    if water >= 0.5:
        benefits.append("ortalama su ihtiyacı düşük")
    if forage >= 0.6:
        benefits.append("yem olarak ek gelir imkânı")
    risks = list(dict.fromkeys([*[n for ns in notes for n in ns], *[n for ns in cnotes for n in ns]]))
    if repeated:
        risks.append("aynı ürün ailesi art arda: zararlı ve hastalık birikimi riski")
    if any(c["family"] == "none" for c in mains):
        risks.append("nadas döneminde toprak çıplak kalır: erozyon ve organik madde kaybı")
    if has_cover:
        risks.append("örtü bitkisi kuru yıllarda ana ürünün suyunu azaltabilir; sonlandırma zamanı yerel karar")
    evidence = {k: (round(v, 3) if isinstance(v, float) and np.isfinite(v) else v)
                for k, v in cond.items() if k in ("summer_precip_mm", "annual_gdd", "summer_heat_days",
                                                    "spring_frost_days", "summer_soil_moisture",
                                                    "p95_daily_precip_mm", "water_stress", "heat_stress",
                                                    "frost_stress", "erosion_pressure")}
    return {
        "key": key, "name": name, "sequence": seq,
        "sequence_names": [" + ".join(cat[c]["name"] for c in s.split("+")) for s in seq],
        "score": round(float(total), 3), "risk": round(risk, 3), "climate_fit": round(climate_fit, 3),
        "components": {k: round(v, 3) for k, v in comps.items()},
        "why": why, "benefits": benefits, "risks_for_local_review": risks, "evidence": evidence,
    }


def recommend(cond: dict, existing: str | list[str] | None = None, priorities: dict | None = None,
              profile: dict | None = None, short_risk: dict | None = None, top_n: int = 3,
              model_confidence: float = 0.5) -> dict:
    cat = catalog()
    profile = profile or {}
    if existing is None and isinstance(profile.get("existing_rotation"), str):
        existing = profile["existing_rotation"]
    cands = {k: (v["name"], list(v["seq"])) for k, v in cat["rotations"].items()}
    existing_key = None
    if existing:
        seq = parse_rotation(existing)
        existing_key = next((k for k, (_, s) in cands.items() if canonical(s) == canonical(seq)), None)
        if existing_key is None:
            existing_key = "existing"
            cands[existing_key] = ("Mevcut rotasyon", seq)
    scored = [score_rotation(k, n, s, cond, priorities or {}, profile, short_risk) for k, (n, s) in cands.items()]
    scored.sort(key=lambda r: -r["score"])
    for i, r in enumerate(scored, 1):
        r["rank"] = i
        r["is_existing"] = r["key"] == existing_key
    picks = [r for r in scored if not r["is_existing"]][:top_n]
    ex = next((r for r in scored if r["is_existing"]), None)
    if ex:
        for r in picks:
            r["delta_vs_existing"] = {"score": round(r["score"] - ex["score"], 3),
                                      "risk": round(r["risk"] - ex["risk"], 3)}
    gaps = []
    for k, label in (("water_stress", "su stresi"), ("heat_stress", "sıcak stresi"),
                     ("frost_stress", "don"), ("annual_gdd", "büyüme derece günü"),
                     ("erosion_pressure", "yoğun yağış/erozyon")):
        if not np.isfinite(_nz(cond.get(k), np.nan)):
            gaps.append(f"{label} verisi yok, varsayılan değer kullanıldı")
    if len(cond.get("years", [])) < 3:
        gaps.append(f"yalnızca {len(cond.get('years', []))} yıllık veri var; uzun vadeli iklim için az")
    for k, label in (("soil_om_pct", "toprak organik maddesi"), ("soil_texture", "toprak dokusu"),
                     ("irrigation", "sulama durumu"), ("slope_pct", "eğim")):
        if k not in profile:
            gaps.append(f"{label} bilinmiyor (yerel veriyle eklenebilir)")
    gaps.append("ürün profilleri genel sezgisel değerler; yerel verim ve pazar fiyatıyla doğrulanmalı")
    local_bonus = 0.1 * min(1.0, sum(k in profile for k in ("soil_om_pct", "soil_texture", "irrigation",
                                                             "slope_pct")) / 4)
    conf = float(np.clip(model_confidence * PROFILE_CONFIDENCE + local_bonus, 0, 1))
    return {
        "existing": ex, "recommended": picks, "all_ranked": [(r["key"], r["score"]) for r in scored],
        "confidence": round(conf, 3), "information_gaps": gaps,
        "disclaimer": "Bu sıralama karar desteğidir; nihai karar çiftçi ve yerel tarım uzmanına aittir.",
    }
