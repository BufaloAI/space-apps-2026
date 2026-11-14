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
    heat_w = 0.5 if crop["season"] == "cool" else 1.0  # kışlıklar yaz sıcağından önce hasat edilir
    pen_h = hs * (1 - crop["heat_tol"]) * heat_w
    frost_weight = 1.0 if crop["season"] in ("warm",) else 0.4
    pen_f = fs * (1 - crop["frost_tol"]) * frost_weight
    wet = _nz(cond.get("wetness"), 0.0)
    pen_wet = wet * (1 - crop.get("wet_tol", 0.8)) * 0.6
    if pen_wet > 0.15:
        notes.append(f"{crop['name']}: yağışlı/nemli yazda hastalık riski yüksek")
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
    if crop["family"] == "none":  # nadas yalnızca kurak yerde su biriktirmek için anlamlı
        pen_fallow = 0.6 * (1 - _nz(cond.get("water_stress"), 0.3))
        if pen_fallow > 0.3:
            notes.append("Nadas: bu alan kurak değil; nadas su kazandırmaz, gelir ve toprak kaybettirir")
        return float(np.clip(1 - pen_fallow, 0, 1)), notes
    return float(np.clip(1 - pen_w - pen_h - pen_f - pen_g - pen_wet, 0, 1)), notes


# Şiddetli stres altında en fazla verim kaybı (kesir). SEZGİSEL üst sınırlar, ölçüm değil.
MAX_LOSS = {"drought": 0.60, "heat": 0.40, "frost": 0.50, "gdd": 0.50, "wet": 0.50}
RISK_LABELS = {"drought": "kuraklık / su stresi", "heat": "sıcak stresi", "frost": "ilkbahar donu",
               "gdd": "yetersiz sıcaklık birikimi (GDD)", "wet": "aşırı nem / hastalık"}


def _sev(x: float) -> str:
    return "yüksek" if x >= 0.20 else "orta" if x >= 0.08 else "düşük"


def crop_losses(crop: dict, cond: dict, irrigated: bool) -> dict[str, float]:
    """Ürün için risk başına beklenen verim kaybı (0..1, merkez tahmin)."""
    if crop["family"] == "none":
        return {}
    ws = _nz(cond.get("water_stress"), 0.3) * (0.4 if irrigated else 1.0)
    hs, fs = _nz(cond.get("heat_stress"), 0.2), _nz(cond.get("frost_stress"), 0.2)
    out = {
        "drought": MAX_LOSS["drought"] * ws * crop["water_need"] * (1 - crop["drought_tol"]),
        "heat": MAX_LOSS["heat"] * hs * (1 - crop["heat_tol"]) * (0.5 if crop["season"] == "cool" else 1.0),
        "frost": MAX_LOSS["frost"] * fs * (1 - crop["frost_tol"]) * (1.0 if crop["season"] == "warm" else 0.4),
        "wet": MAX_LOSS["wet"] * _nz(cond.get("wetness"), 0.0) * (1 - crop.get("wet_tol", 0.8)),
    }
    gdd = cond.get("annual_gdd")
    if crop.get("gdd_need") and gdd is not None and np.isfinite(gdd) and crop["season"] == "warm":
        out["gdd"] = MAX_LOSS["gdd"] * min(1.0, max(0.0, (1 - gdd / crop["gdd_need"]) * 2))
    return {k: float(v) for k, v in out.items()}


def rotation_damages(seq: list[str], cond: dict, profile: dict, repeated: bool) -> tuple[list[dict], dict]:
    """Risklerin olası zararları: ürün bazlı verim kaybı aralığı + toprak/zararlı/azot zararları."""
    cat = catalog()["crops"]
    irrigated = bool(_nz(profile.get("irrigation"), 0))
    dmg: list[dict] = []
    per_crop = {}
    for s in dict.fromkeys(x.split("+")[0] for x in seq):
        c = cat[s]
        losses = crop_losses(c, cond, irrigated)
        if not losses:
            continue
        total = 1 - float(np.prod([1 - v for v in losses.values()]))
        per_crop[c["name"]] = [round(100 * total * 0.5, 1), round(100 * min(0.9, total * 1.5), 1)]
        for k, v in sorted(losses.items(), key=lambda kv: -kv[1]):
            if v < 0.03:
                continue
            dmg.append({
                "risk": RISK_LABELS[k], "affects": c["name"], "severity": _sev(v),
                "yield_loss_pct": [round(100 * v * 0.5, 1), round(100 * min(0.9, v * 1.5), 1)],
                "impact": {
                    "drought": "çiçeklenme/dane dolumu döneminde su eksikliği verimi ve kaliteyi düşürür",
                    "heat": "yüksek sıcaklık tozlanmayı ve dane dolumunu bozar",
                    "frost": "erken ekimde fide kaybı, yeniden ekim maliyeti",
                    "gdd": "ürün mevsim sonunda olgunlaşmayabilir; hasat nemi ve kurutma maliyeti artar",
                    "wet": "yağışlı ve nemli yazda mantar hastalıkları (örn. antraknoz) yayılır, ilaç maliyeti artar",
                }[k],
            })
    ep = _nz(cond.get("erosion_pressure"), 0.3) * (1 + min(1.0, _nz(profile.get("slope_pct"), 0) / 8))
    bare = [i + 1 for i, x in enumerate(seq) if max(cat[c]["residue_cover"] for c in x.split("+")) < 0.35]
    if bare and ep > 0.2:
        dmg.append({"risk": "erozyon", "affects": "toprak", "severity": _sev(0.25 * ep),
                    "yield_loss_pct": None,
                    "impact": f"{', '.join(map(str, bare))}. sezonda toprak örtüsü zayıf; yoğun yağışta üst toprak "
                              "ve besin kaybı, uzun vadede verim düşüşü"})
    if repeated:
        dmg.append({"risk": "zararlı/hastalık birikimi", "affects": "tüm rotasyon", "severity": "orta",
                    "yield_loss_pct": [5.0, 15.0],
                    "impact": "aynı ürün ailesi art arda ekilince zararlı ve hastalık baskısı artar; ilaç maliyeti "
                              "yükselir (aralık genel bir sezgidir)"})
    mains = [cat[x.split("+")[0]] for x in seq]
    covers = [cat[c] for x in seq for c in x.split("+")[1:]]
    if not any(c["n_fix"] for c in mains + covers) and np.mean([c.get("n_need", 0.5) for c in mains]) >= 0.6:
        dmg.append({"risk": "azot açığı", "affects": "toprak", "severity": "orta", "yield_loss_pct": None,
                    "impact": "baklagil yok, azot ihtiyacı yüksek: gübre maliyeti ve azot yıkanması (su kirliliği) riski"})
    if any(c["family"] == "none" for c in mains):
        dmg.append({"risk": "organik madde kaybı", "affects": "toprak", "severity": "orta", "yield_loss_pct": None,
                    "impact": "nadas yılında toprak çıplak kalır; organik madde ve toprak yapısı zayıflar, gelir olmaz"})
    order = {"yüksek": 0, "orta": 1, "düşük": 2}
    dmg.sort(key=lambda d: order[d["severity"]])
    return dmg, per_crop


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
    damages, crop_loss = rotation_damages(seq, cond, profile, repeated)
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
        "damages": damages, "expected_yield_loss_pct": crop_loss,
    }


def recommend(cond: dict, existing: str | list[str] | None = None, priorities: dict | None = None,
              profile: dict | None = None, short_risk: dict | None = None, top_n: int = 3,
              model_confidence: float = 0.5, chosen: str | list[str] | None = None) -> dict:
    """Önerir. `existing` = çiftçinin şu anki rotasyonu, `chosen` = çiftçinin değerlendirmek istediği kendi seçimi."""
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
    chosen_key = None
    if chosen:
        cseq = parse_rotation(chosen)
        chosen_key = next((k for k, (_, s) in cands.items() if canonical(s) == canonical(cseq)), None)
        if chosen_key is None:
            chosen_key = "chosen"
            cands[chosen_key] = ("Çiftçinin seçtiği rotasyon", cseq)
    scored = [score_rotation(k, n, s, cond, priorities or {}, profile, short_risk) for k, (n, s) in cands.items()]
    scored.sort(key=lambda r: -r["score"])
    for i, r in enumerate(scored, 1):
        r["rank"] = i
        r["is_existing"] = r["key"] == existing_key
        r["is_chosen"] = r["key"] == chosen_key
    picks = [r for r in scored if not r["is_existing"]][:top_n]
    ex = next((r for r in scored if r["is_existing"]), None)
    ch = next((r for r in scored if r["is_chosen"]), None)
    if ex:
        for r in [*picks, *([ch] if ch else [])]:
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
        "existing": ex, "chosen": ch, "recommended": picks, "all_ranked": [(r["key"], r["score"]) for r in scored],
        "confidence": round(conf, 3), "information_gaps": gaps,
        "disclaimer": "Bu sıralama karar desteğidir; nihai karar çiftçi ve yerel tarım uzmanına aittir.",
    }
