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

from .i18n import Bi, en, en_list

CROPS_FILE = Path(__file__).with_name("crops.toml")
DEFAULT_PRIORITIES = {"water": 1.0, "soil_health": 1.0, "income": 1.0, "erosion": 1.0, "forage": 0.3}
PRIORITY_LABELS = {"water": "su tasarrufu", "soil_health": "toprak sağlığı", "income": "gelir/pazar",
                   "erosion": "erozyon azaltma", "forage": "yem değeri"}
PRIORITY_LABELS_EN = {"water": "water saving", "soil_health": "soil health", "income": "income/market",
                      "erosion": "erosion control", "forage": "forage value"}


def cname_en(c: dict) -> str:
    return c.get("name_en") or c["name"]
PROFILE_CONFIDENCE = 0.7  # ürün profilleri ölçüm değil -> güven tavanı


LIBRARY_FILE = Path(__file__).with_name("crop_library.toml")


@lru_cache(maxsize=1)
def catalog() -> dict:
    return tomllib.loads(CROPS_FILE.read_text())


@lru_cache(maxsize=1)
def library() -> dict:
    """Ürün kütüphanesi: varsayılan adaylara girmez; çiftçi kendi listesine ekleyince kullanılır."""
    return tomllib.loads(LIBRARY_FILE.read_text())["crops"]


def suggest_crops(cond: dict, profile: dict | None = None, exclude: set[str] | None = None,
                  top: int = 8, min_fit: float = 0.5) -> list[dict]:
    """Alanın iklimine (ve sulama bilgisine) en uygun, çiftçinin listesinde olmayan kütüphane + katalog ürünleri.
    Uygunluk `crop_fit` ile aynı sezgisel kurallardan; ölçüm değildir."""
    profile = profile or {}
    irrigated = bool(_nz(profile.get("irrigation"), 0))
    exclude = exclude or set()
    pool = {**{k: {**v, "_from": "library"} for k, v in library().items()},
            **{k: {**v, "_from": "catalog"} for k, v in catalog()["crops"].items()}}
    out = []
    for k, c in pool.items():
        if k in exclude or c["family"] == "none" or c.get("cover_crop"):
            continue
        fit, notes = crop_fit(c, cond, irrigated)
        if fit < min_fit:
            continue
        losses = crop_losses(c, cond, irrigated)
        total = 1 - float(np.prod([1 - v for v in losses.values()])) if losses else 0.0
        main_risk = max(losses, key=losses.get) if losses else None
        out.append({
            "id": k, "name": c["name"], "name_en": c.get("name_en", c["name"]), "from": c["_from"],
            "fit": round(fit, 3), "family": c["family"], "season": c["season"],
            "expected_yield_loss_pct": [round(100 * total * 0.5, 1), round(100 * min(0.9, total * 1.5), 1)],
            "main_risk": RISK_LABELS.get(main_risk) if main_risk and losses[main_risk] >= 0.03 else None,
            "main_risk_en": RISK_LABELS_EN.get(main_risk) if main_risk and losses[main_risk] >= 0.03 else None,
            "concerns": list(notes), "concerns_en": en_list(notes),
            "note": c.get("note"), "note_en": c.get("note_en"),
        })
    out.sort(key=lambda r: -r["fit"])
    return out[:top]


# Çiftçinin eklediği ürünlerde zorunlu/isteğe bağlı alanlar (crops.toml ile aynı anlam, 0-1 göreli ölçek)
CROP_FLOATS = ("water_need", "drought_tol", "heat_tol", "frost_tol", "residue_cover", "om", "market", "forage",
               "n_need", "wet_tol")
SEASONS = ("warm", "cool", "perennial", "none")
ECON_KEYS = ("yield_t_ha", "price_per_t", "cost_per_ha")


def normalize_crop(c: dict) -> dict:
    """Çiftçi ürününü doğrular ve katalog biçimine çevirir (eksik sayısal alan = 0.5, ölçülmemiş sezgi)."""
    cid = str(c.get("id", "")).strip()
    if not cid or not cid.replace("_", "").isalnum() or len(cid) > 40:
        raise ValueError(f"Geçersiz ürün kimliği: {cid!r}")
    out = {"name": str(c.get("name") or cid)[:60], "family": str(c.get("family") or "other")[:30],
           "season": c.get("season") if c.get("season") in SEASONS else "warm",
           "n_fix": bool(c.get("n_fix", False)), "cover_crop": bool(c.get("cover_crop", False)),
           "gdd_need": float(np.clip(float(c.get("gdd_need") or 0), 0, 5000)), "custom": True}
    for k in CROP_FLOATS:
        v = c.get(k)
        out[k] = float(np.clip(float(v), 0, 1)) if v is not None else 0.5
    econ = {k: float(c[k]) for k in ECON_KEYS if c.get(k) is not None and np.isfinite(float(c[k]))}
    if econ:
        out["economics"] = econ
    return out


def build_catalog(extra_crops: list[dict] | None = None) -> dict:
    """Taban katalog + çiftçinin ürünleri (aynı kimlik verilirse çiftçinin değerleri geçerli olur)."""
    base = catalog()
    if not extra_crops:
        return base
    crops = dict(base["crops"])
    for c in extra_crops:
        n = normalize_crop(c)
        crops[str(c["id"]).strip()] = n
    return {**base, "crops": crops}


def parse_rotation(spec: str | list[str], cat: dict | None = None) -> list[str]:
    """'corn>soybean' / 'corn,soybean' / ['corn','soybean'] / tanımlı rotasyon anahtarı ('corn_soy')."""
    cat = cat or catalog()
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
        notes.append(Bi(f"{crop['name']}: yağışlı/nemli yazda hastalık riski yüksek",
                        f"{cname_en(crop)}: high disease risk in wet/humid summers"))
    gdd = cond.get("annual_gdd")
    pen_g = 0.0
    if crop.get("gdd_need") and gdd is not None and np.isfinite(gdd) and crop["season"] == "warm":
        ratio = gdd / crop["gdd_need"]
        if ratio < 1.0:
            pen_g = min(1.0, (1 - ratio) * 2)
            notes.append(Bi(f"{crop['name']}: yıllık GDD ({gdd:.0f}) ihtiyacın ({crop['gdd_need']}) altında",
                            f"{cname_en(crop)}: annual GDD ({gdd:.0f}) below its need ({crop['gdd_need']})"))
    if pen_w > 0.2:
        notes.append(Bi(f"{crop['name']}: su stresi altında su ihtiyacı yüksek",
                        f"{cname_en(crop)}: high water need under water stress"))
    if pen_h > 0.2:
        notes.append(Bi(f"{crop['name']}: sıcak stresine duyarlı", f"{cname_en(crop)}: sensitive to heat stress"))
    if pen_f > 0.15:
        notes.append(Bi(f"{crop['name']}: ilkbahar donuna duyarlı", f"{cname_en(crop)}: sensitive to spring frost"))
    if crop["family"] == "none":  # nadas yalnızca kurak yerde su biriktirmek için anlamlı
        pen_fallow = 0.6 * (1 - _nz(cond.get("water_stress"), 0.3))
        if pen_fallow > 0.3:
            notes.append(Bi("Nadas: bu alan kurak değil; nadas su kazandırmaz, gelir ve toprak kaybettirir",
                            "Fallow: this area is not dry; fallow saves no water and costs income and soil"))
        return float(np.clip(1 - pen_fallow, 0, 1)), notes
    return float(np.clip(1 - pen_w - pen_h - pen_f - pen_g - pen_wet, 0, 1)), notes


# Şiddetli stres altında en fazla verim kaybı (kesir). SEZGİSEL üst sınırlar, ölçüm değil.
MAX_LOSS = {"drought": 0.60, "heat": 0.40, "frost": 0.50, "gdd": 0.50, "wet": 0.50}
RISK_LABELS = {"drought": "kuraklık / su stresi", "heat": "sıcak stresi", "frost": "ilkbahar donu",
               "gdd": "yetersiz sıcaklık birikimi (GDD)", "wet": "aşırı nem / hastalık"}
RISK_LABELS_EN = {"drought": "drought / water stress", "heat": "heat stress", "frost": "spring frost",
                  "gdd": "insufficient heat accumulation (GDD)", "wet": "excess moisture / disease"}
IMPACT = {
    "drought": ("çiçeklenme/dane dolumu döneminde su eksikliği verimi ve kaliteyi düşürür",
                "water shortage at flowering/grain fill cuts yield and quality"),
    "heat": ("yüksek sıcaklık tozlanmayı ve dane dolumunu bozar", "high temperatures disrupt pollination and grain fill"),
    "frost": ("erken ekimde fide kaybı, yeniden ekim maliyeti", "seedling loss after early sowing, replanting cost"),
    "gdd": ("ürün mevsim sonunda olgunlaşmayabilir; hasat nemi ve kurutma maliyeti artar",
            "the crop may not mature by season end; harvest moisture and drying costs rise"),
    "wet": ("yağışlı ve nemli yazda mantar hastalıkları (örn. antraknoz) yayılır, ilaç maliyeti artar",
            "fungal diseases (e.g. anthracnose) spread in wet, humid summers; spraying costs rise"),
}


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


def rotation_damages(seq: list[str], cond: dict, profile: dict, repeated: bool,
                     catalog_: dict | None = None) -> tuple[list[dict], dict]:
    """Risklerin olası zararları: ürün bazlı verim kaybı aralığı + toprak/zararlı/azot zararları."""
    cat = (catalog_ or catalog())["crops"]
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
                "kind": k, "risk": RISK_LABELS[k], "risk_en": RISK_LABELS_EN[k],
                "affects": c["name"], "affects_en": cname_en(c), "severity": _sev(v),
                "yield_loss_pct": [round(100 * v * 0.5, 1), round(100 * min(0.9, v * 1.5), 1)],
                "impact": IMPACT[k][0], "impact_en": IMPACT[k][1],
            })
    ep = _nz(cond.get("erosion_pressure"), 0.3) * (1 + min(1.0, _nz(profile.get("slope_pct"), 0) / 8))
    bare = [i + 1 for i, x in enumerate(seq) if max(cat[c]["residue_cover"] for c in x.split("+")) < 0.35]
    if bare and ep > 0.2:
        dmg.append({"kind": "erosion", "risk": "erozyon", "risk_en": "erosion", "affects": "toprak",
                    "affects_en": "soil", "severity": _sev(0.25 * ep), "yield_loss_pct": None,
                    "impact": f"{', '.join(map(str, bare))}. sezonda toprak örtüsü zayıf; yoğun yağışta üst toprak "
                              "ve besin kaybı, uzun vadede verim düşüşü",
                    "impact_en": f"weak soil cover in season {', '.join(map(str, bare))}; heavy rain washes away "
                                 "topsoil and nutrients, lowering yields over time"})
    if repeated:
        dmg.append({"kind": "pests", "risk": "zararlı/hastalık birikimi", "risk_en": "pest/disease build-up",
                    "affects": "tüm rotasyon", "affects_en": "whole rotation", "severity": "orta",
                    "yield_loss_pct": [5.0, 15.0],
                    "impact": "aynı ürün ailesi art arda ekilince zararlı ve hastalık baskısı artar; ilaç maliyeti "
                              "yükselir (aralık genel bir sezgidir)",
                    "impact_en": "planting the same crop family back to back raises pest and disease pressure and "
                                 "spraying costs (the range is a general rule of thumb)"})
    mains = [cat[x.split("+")[0]] for x in seq]
    covers = [cat[c] for x in seq for c in x.split("+")[1:]]
    if not any(c["n_fix"] for c in mains + covers) and np.mean([c.get("n_need", 0.5) for c in mains]) >= 0.6:
        dmg.append({"kind": "nitrogen", "risk": "azot açığı", "risk_en": "nitrogen deficit", "affects": "toprak",
                    "affects_en": "soil", "severity": "orta", "yield_loss_pct": None,
                    "impact": "baklagil yok, azot ihtiyacı yüksek: gübre maliyeti ve azot yıkanması (su kirliliği) riski",
                    "impact_en": "no legume and high nitrogen demand: fertiliser cost and nitrate leaching "
                                 "(water pollution) risk"})
    if any(c["family"] == "none" for c in mains):
        dmg.append({"kind": "organic_matter", "risk": "organik madde kaybı", "risk_en": "organic matter loss",
                    "affects": "toprak", "affects_en": "soil", "severity": "orta", "yield_loss_pct": None,
                    "impact": "nadas yılında toprak çıplak kalır; organik madde ve toprak yapısı zayıflar, gelir olmaz",
                    "impact_en": "soil lies bare in the fallow year; organic matter and structure decline and there "
                                 "is no income"})
    order = {"yüksek": 0, "orta": 1, "düşük": 2}
    dmg.sort(key=lambda d: order[d["severity"]])
    return dmg, per_crop


def economics(seq: list[str], cat: dict, crop_loss: dict) -> dict | None:
    """Çiftçinin kendi verim/fiyat/maliyet hesabından sezon başına brüt kâr (yalnızca tüm ana ürünlerde varsa).
    Beklenen verim kaybı (zarar aralığının ortası) düşülür."""
    rows = []
    for s in seq:
        c = cat[s.split("+")[0]]
        if c["family"] == "none":
            rows.append({"crop": c["name"], "gross_margin": 0.0})
            continue
        e = c.get("economics") or {}
        if not all(k in e for k in ECON_KEYS):
            return None
        loss = crop_loss.get(c["name"])
        mid = (loss[0] + loss[1]) / 200 if loss else 0.0
        rows.append({"crop": c["name"], "gross_margin": round(e["yield_t_ha"] * (1 - mid) * e["price_per_t"]
                                                              - e["cost_per_ha"], 1),
                     "expected_yield_t_ha": round(e["yield_t_ha"] * (1 - mid), 2)})
    return {"per_season": rows, "mean_gross_margin_per_ha": round(float(np.mean([r["gross_margin"] for r in rows])), 1),
            "note": "çiftçinin girdiği verim, fiyat ve maliyetten; tahmini verim kaybı düşüldü",
            "note_en": "from the farmer's yield, price and cost; estimated yield loss subtracted"}


def score_rotation(key: str, name: str, seq: list[str], cond: dict, priorities: dict, profile: dict,
                   short_risk: dict | None, catalog_: dict | None = None, source: str = "katalog") -> dict:
    full = catalog_ or catalog()
    cat = full["crops"]
    irrigated = bool(_nz(profile.get("irrigation"), 0))
    mains = [cat[s.split("+")[0]] for s in seq]
    covers = [cat[c] for s in seq for c in s.split("+")[1:]]
    fits, notes = zip(*(crop_fit(c, cond, irrigated) for c in mains))
    cfit, cnotes = (zip(*(crop_fit(c, cond, irrigated) for c in covers)) if covers else ((), ()))
    climate_fit = float(np.mean(fits) * 0.85 + (np.mean(cfit) * 0.15 if cfit else 0.15 * np.mean(fits)))

    fams = [c["family"] for c in mains]
    real = [f for f in fams if f != "none"]
    diversity = min(1.0, len(set(real)) / 3)  # 3 farklı aile = tam puan; monokültür 1/3 (eskiden 1.0'dı)
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
    why = [Bi(f"{PRIORITY_LABELS[k]} puanı {comps[k]:.2f}", f"{PRIORITY_LABELS_EN[k]} score {comps[k]:.2f}")
           for k in top]
    if climate_fit >= 0.75:
        why.append(Bi(f"alan iklimine uygunluk yüksek ({climate_fit:.2f})",
                      f"good fit to the local climate ({climate_fit:.2f})"))
    benefits = []
    if n_fix:
        benefits.append(Bi("baklagil azot bağlar, sonraki ürünün gübre ihtiyacını azaltabilir",
                           "a legume fixes nitrogen and can cut the next crop's fertiliser need"))
    if has_cover:
        benefits.append(Bi("örtü bitkisi kış boyunca toprağı korur, sızmayı ve organik maddeyi artırabilir",
                           "a cover crop protects the soil over winter and can raise infiltration and organic matter"))
    if diversity >= 0.66 and not repeated:
        benefits.append(Bi("ürün ailesi değişiyor, zararlı/hastalık döngüsü kırılabilir",
                           "crop families alternate, which can break pest/disease cycles"))
    if water >= 0.5:
        benefits.append(Bi("ortalama su ihtiyacı düşük", "low average water need"))
    if forage >= 0.6:
        benefits.append(Bi("yem olarak ek gelir imkânı", "extra income possible as forage"))
    risks = list(dict.fromkeys([*[n for ns in notes for n in ns], *[n for ns in cnotes for n in ns]]))
    if repeated:
        risks.append(Bi("aynı ürün ailesi art arda: zararlı ve hastalık birikimi riski",
                        "same crop family back to back: pest and disease build-up risk"))
    if any(c["family"] == "none" for c in mains):
        risks.append(Bi("nadas döneminde toprak çıplak kalır: erozyon ve organik madde kaybı",
                        "soil is bare during fallow: erosion and organic matter loss"))
    if has_cover:
        risks.append(Bi("örtü bitkisi kuru yıllarda ana ürünün suyunu azaltabilir; sonlandırma zamanı yerel karar",
                        "in dry years a cover crop can take water from the main crop; termination timing is a local "
                        "decision"))
    damages, crop_loss = rotation_damages(seq, cond, profile, repeated, full)
    custom = sorted({cat[c]["name"] for s_ in seq for c in s_.split("+") if cat[c].get("custom")})
    if custom:
        risks.append(Bi(f"çiftçi ürünü profili ({', '.join(custom)}) çiftçinin kendi değerleridir; sonuç onlara bağlı",
                        f"farmer crop profile ({', '.join(custom)}) uses the farmer's own values; results depend "
                        "on them"))
    evidence = {k: (round(v, 3) if isinstance(v, float) and np.isfinite(v) else v)
                for k, v in cond.items() if k in ("summer_precip_mm", "annual_gdd", "summer_heat_days",
                                                    "spring_frost_days", "summer_soil_moisture",
                                                    "p95_daily_precip_mm", "water_stress", "heat_stress",
                                                    "frost_stress", "erosion_pressure")}
    return {
        "key": key, "name": name, "sequence": seq,
        "sequence_names": [" + ".join(cat[c]["name"] for c in s.split("+")) for s in seq],
        "sequence_names_en": [" + ".join(cname_en(cat[c]) for c in s.split("+")) for s in seq],
        "why_en": en_list(why), "benefits_en": en_list(benefits), "risks_for_local_review_en": en_list(risks),
        "name_en": en(name),
        "score": round(float(total), 3), "risk": round(risk, 3), "climate_fit": round(climate_fit, 3),
        "components": {k: round(v, 3) for k, v in comps.items()},
        "why": why, "benefits": benefits, "risks_for_local_review": risks, "evidence": evidence,
        "damages": damages, "expected_yield_loss_pct": crop_loss, "source": source,
        **({"economics": econ} if (econ := economics(seq, cat, crop_loss)) else {}),
    }


def seq_name(seq: list[str], cat: dict) -> Bi:
    return Bi(" → ".join(" + ".join(cat["crops"][c]["name"] for c in s.split("+")) for s in seq),
              " → ".join(" + ".join(cname_en(cat["crops"][c]) for c in s.split("+")) for s in seq))


def generate_candidates(cat: dict, cond: dict, profile: dict, max_len: int = 3, limit: int = 400) -> dict:
    """Katalogdaki sabit rotasyonların ötesinde aday üretir: 2-3 sezonluk ana ürün dizileri.

    Kurallar (agronomik sezgi): art arda (döngüsel) aynı ürün ailesi yok, en fazla bir nadas ve yalnızca kurak
    alanda, çok yıllık ve örtü bitkileri ana ürün olarak dizilmez (yonca katalogdaki kalıpla kalır), iklime
    uymayan (uygunluk < 0.35) katalog ürünleri elenir. Kurak + sulamasız değilse her dizinin bir de örtü bitkili
    sürümü üretilir: yazlık ürünün ardından yine yazlık geliyorsa araya, sonraki ürün azot isterse fiğ, değilse
    çavdar konur."""
    from itertools import product  # noqa: PLC0415

    crops = cat["crops"]
    irrigated = bool(_nz(profile.get("irrigation"), 0))
    dry = _nz(cond.get("water_stress"), 0.3) >= 0.5
    mains = []
    for k, c in crops.items():
        if c.get("cover_crop") or c["season"] == "perennial":
            continue
        if c["family"] == "none" and not dry:
            continue
        fit, _ = crop_fit(c, cond, irrigated)
        if fit >= (0.2 if c.get("custom") else 0.35):
            mains.append(k)
    # örtü bitkisi ana ürünün suyunu kullanır: kurak ve sulamasız alanda örtülü sürüm üretilmez
    covers = {} if (dry and not irrigated) else {
        f: k for k, c in crops.items() if c.get("cover_crop") for f in (["fabaceae"] if c.get("n_fix") else ["poaceae"])}
    seen = {canonical(list(v["seq"])) for v in cat["rotations"].values()}
    out: dict = {}
    for n in range(2, max_len + 1):
        for seq in product(mains, repeat=n):
            fams = [crops[c]["family"] for c in seq]
            if any(fams[i] == fams[(i + 1) % n] for i in range(n)):
                continue
            if sum(f == "none" for f in fams) > 1 or len(set(seq)) < n:
                continue
            seq = list(seq)
            variants = [seq]
            if covers:
                cv, changed = [], False
                for i, c in enumerate(seq):
                    nxt = crops[seq[(i + 1) % n]]
                    if crops[c]["season"] == "warm" and nxt["season"] == "warm":
                        cover = covers.get("fabaceae" if nxt.get("n_need", 0.5) >= 0.6 else "poaceae")
                        if cover:
                            cv.append(f"{c}+{cover}")
                            changed = True
                            continue
                    cv.append(c)
                if changed:
                    variants.append(cv)
            for v in variants:
                key = canonical(v)
                if key in seen:
                    continue
                seen.add(key)
                out["gen:" + ">".join(key)] = (seq_name(list(key), cat), list(key))
                if len(out) >= limit:
                    return out
    return out


def _mains_key(seq: list[str]) -> tuple[str, ...]:
    return canonical([s.split("+")[0] for s in seq])


def recommend(cond: dict, existing: str | list[str] | None = None, priorities: dict | None = None,
              profile: dict | None = None, short_risk: dict | None = None, top_n: int = 3,
              model_confidence: float = 0.5, chosen: str | list[str] | None = None,
              extra_crops: list[dict] | None = None, extra_rotations: list[dict] | None = None,
              generate: bool = True) -> dict:
    """Önerir. `existing` = çiftçinin şu anki rotasyonu, `chosen` = çiftçinin değerlendirmek istediği kendi seçimi,
    `extra_crops` = çiftçinin eklediği ürünler, `extra_rotations` = çiftçinin oluşturduğu rotasyonlar
    ([{"name": ..., "seq": "corn>soybean" | [...]}]), `generate` = sabit listeye ek otomatik adaylar."""
    cat = build_catalog(extra_crops)
    profile = profile or {}
    if existing is None and isinstance(profile.get("existing_rotation"), str):
        existing = profile["existing_rotation"]
    cands = {k: (Bi(v["name"], v.get("name_en", v["name"])), list(v["seq"])) for k, v in cat["rotations"].items()}
    source = dict.fromkeys(cands, "katalog")
    if generate:
        gen = generate_candidates(cat, cond, profile)
        cands.update(gen)
        source.update(dict.fromkeys(gen, "otomatik"))
    user_keys: list[str] = []
    for i, r in enumerate(extra_rotations or [], 1):
        seq = parse_rotation(r["seq"], cat)
        key = next((k for k, (_, s) in cands.items() if canonical(s) == canonical(seq)), None)
        if key is None or source.get(key) == "otomatik":  # çiftçinin adı otomatik adın önüne geçer
            cands.pop(key, None)
            key = f"user:{i}"
            nm = str(r["name"])[:80] if r.get("name") else seq_name(seq, cat)
            cands[key] = (Bi(nm, nm) if not isinstance(nm, Bi) else nm, seq)
        source[key] = "çiftçi"
        user_keys.append(key)
    existing_key = None
    if existing:
        seq = parse_rotation(existing, cat)
        existing_key = next((k for k, (_, s) in cands.items() if canonical(s) == canonical(seq)), None)
        if existing_key is None:
            existing_key = "existing"
            cands[existing_key] = (Bi("Mevcut rotasyon", "Current rotation"), seq)
            source[existing_key] = "çiftçi"
    chosen_key = None
    if chosen:
        cseq = parse_rotation(chosen, cat)
        chosen_key = next((k for k, (_, s) in cands.items() if canonical(s) == canonical(cseq)), None)
        if chosen_key is None:
            chosen_key = "chosen"
            cands[chosen_key] = (Bi("Çiftçinin seçtiği rotasyon", "Farmer's chosen rotation"), cseq)
            source[chosen_key] = "çiftçi"
    scored = [score_rotation(k, n, s, cond, priorities or {}, profile, short_risk, cat, source.get(k, "katalog"))
              for k, (n, s) in cands.items()]
    scored.sort(key=lambda r: -r["score"])
    for i, r in enumerate(scored, 1):
        r["rank"] = i
        r["is_existing"] = r["key"] == existing_key
        r["is_chosen"] = r["key"] == chosen_key
    # Öneriler birbirinin kopyası olmasın: ana ürün KÜMESİ farklı olanlar (sıra ya da örtü bitkisi farkı sayılmaz).
    # Otomatik adaylar sezgisel ürün profillerine dayanır ve sahada denenmemiş olabilir; bu yüzden en iyi
    # katalog rotasyonu (yaygın, bilinen uygulama) her zaman önerilerde kalır.
    def crop_set(r):
        return frozenset(x.split("+")[0] for x in r["sequence"])

    picks, used = [], set()
    pool = [r for r in scored if not r["is_existing"]]
    best_known = next((r for r in pool if r.get("source") == "katalog"), None)
    for r in pool:
        if crop_set(r) in used:
            continue
        if len(picks) == top_n - 1 and best_known and best_known not in picks and r is not best_known:
            break  # son yer bilinen rotasyona
        picks.append(r)
        used.add(crop_set(r))
        if len(picks) >= top_n:
            break
    if best_known and best_known not in picks and len(picks) < top_n:
        picks.append(best_known)
    custom = [r for r in scored if r["key"] in user_keys]
    ex = next((r for r in scored if r["is_existing"]), None)
    ch = next((r for r in scored if r["is_chosen"]), None)
    if ex:
        for r in [*picks, *([ch] if ch else []), *custom]:
            r["delta_vs_existing"] = {"score": round(r["score"] - ex["score"], 3),
                                      "risk": round(r["risk"] - ex["risk"], 3)}
    gaps = []
    for k, label, label_en in (("water_stress", "su stresi", "water stress"), ("heat_stress", "sıcak stresi", "heat stress"),
                               ("frost_stress", "don", "frost"), ("annual_gdd", "büyüme derece günü", "growing degree days"),
                               ("erosion_pressure", "yoğun yağış/erozyon", "heavy rain/erosion")):
        if not np.isfinite(_nz(cond.get(k), np.nan)):
            gaps.append(Bi(f"{label} verisi yok, varsayılan değer kullanıldı", f"no {label_en} data; a default was used"))
    if len(cond.get("years", [])) < 3:
        n = len(cond.get("years", []))
        gaps.append(Bi(f"yalnızca {n} yıllık veri var; uzun vadeli iklim için az",
                       f"only {n} year(s) of data; too little for long-term climate"))
    estimated = set(profile.get("_estimated", []))
    for k, label, label_en in (("soil_om_pct", "toprak organik maddesi", "soil organic matter"),
                               ("soil_texture", "toprak dokusu", "soil texture"),
                               ("irrigation", "sulama durumu", "irrigation"), ("slope_pct", "eğim", "slope")):
        if k not in profile:
            gaps.append(Bi("sulama durumu bilinmiyor; yağışa bağlı (sulamasız) varsayıldı",
                           "irrigation unknown; rainfed (not irrigated) was assumed") if k == "irrigation"
                        else Bi(f"{label} bilinmiyor (yerel veriyle eklenebilir)",
                                f"{label_en} unknown (can be added as farmer's data)"))
        elif k in estimated:
            gaps.append(Bi(f"{label} ölçülmedi, küresel veriden hesaplandı ({profile[k]}); yerel ölçümle doğrulanmalı",
                           f"{label_en} not measured, calculated from global data ({profile[k]}); verify locally"))
    gaps.append(Bi("ürün profilleri genel sezgisel değerler; yerel verim ve pazar fiyatıyla doğrulanmalı",
                   "crop profiles are general rules of thumb; check against local yields and market prices"))
    # ölçülen/çiftçinin girdiği değer tam, hesaplanan tahmin yarım katkı verir
    local_bonus = 0.1 * min(1.0, sum((0.5 if k in estimated else 1.0) for k in ("soil_om_pct", "soil_texture",
                                                                               "irrigation", "slope_pct")
                                     if k in profile) / 4)
    conf = float(np.clip(model_confidence * PROFILE_CONFIDENCE + local_bonus, 0, 1))
    return {
        "existing": ex, "chosen": ch, "recommended": picks, "custom": custom,
        "n_candidates": len(scored), "n_generated": sum(1 for v in source.values() if v == "otomatik"),
        "all_ranked": [(r["key"], r["score"]) for r in scored][:50],
        "confidence": round(conf, 3), "information_gaps": gaps, "information_gaps_en": en_list(gaps),
        "disclaimer": "Bu sıralama karar desteğidir; nihai karar çiftçi ve yerel tarım uzmanına aittir.",
        "disclaimer_en": "This ranking is decision support; the final decision belongs to the farmer and a local "
                         "agronomist.",
    }
