"""Baştan sona tek komut: ön kontrol -> veri indirme -> özellik tablosu -> eğitim -> doğrulama -> rapor.

    uv run python -m ml.run_all                 # her şeyi çalıştır (biten adımlar atlanır)
    uv run python -m ml.run_all --check-only    # hiçbir şey indirmeden: ön kontrol + eldeki veri envanteri
    uv run python -m ml.run_all --only fields,power --force   # yalnızca bu adımları baştan çalıştır
    uv run python -m ml.run_all --max-fields 10 # deneme: yalnızca 10 tarla

Adımlar: preflight, fields (CDL + SSURGO tarla seçimi), power (NASA POWER), ndvi (MODIS NDVI: Planetary
Computer, olmazsa ORNL), appeears (MODIS LST/ET, SMAP, SRTM; çalışmazsa akış durmaz), usda (NASS verim/fiyat, OpenET), build (özellik tablosu), train (XGBoost), verify (testler + metrik
kontrolü). Her adımın çıktısı `ml/cache/logs/<adım>.log`'a yazılır. Yarıda kalırsa tekrar çalıştırın: tamamlanmış
adımlar atlanır, indiriciler kendi önbelleklerinden devam eder. Sonunda `ml/cache/run_all_report.md` yazılır:
neyin tamam, neyin eksik olduğu ve eksikleri tamamlamak için komutlar.

Gizli bilgiler (Earthdata şifresi, API anahtarları) hiçbir zaman yazdırılmaz.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import netrc
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ML = ROOT / "ml"
CACHE = ML / "cache"
RAW, STATIC, LOGS = CACHE / "raw", CACHE / "static", CACHE / "logs"
STATE = CACHE / "run_all_state.json"
REPORT = CACHE / "run_all_report.md"
AREAS_US = ML / "data_load" / "areas.us.json"
AREAS_EXAMPLE = ML / "data_load" / "areas.example.json"
FEATURES = CACHE / "features.parquet"
STEPS = ["preflight", "fields", "power", "ndvi", "appeears", "usda", "build", "train", "verify"]
APPEEARS_PRODUCTS = "lst,et,smap,elev"  # NDVI ayrı adımda (Planetary Computer)
UA = {"User-Agent": "field-shift-research/0.1"}

OK, WARN, FAIL, SKIP = "✅", "⚠️", "❌", "⏭️"


# ---------------------------------------------------------------- yardımcılar
class Ctx:
    def __init__(self, a):
        self.a = a
        self.results: list[tuple[str, str, str]] = []  # (adım, durum, ayrıntı)
        self.todo: list[str] = []  # eksikleri tamamlamak için öneriler
        self.state = json.loads(STATE.read_text()) if STATE.exists() else {}

    def note(self, step, status, detail):
        self.results.append((step, status, detail))
        print(f"  {status} {detail}")

    def save(self):
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(self.state, indent=1))


def env_keys() -> dict:
    try:
        from dotenv import dotenv_values

        return {k: v for k, v in dotenv_values(ML / ".env").items() if v}
    except Exception:  # noqa: BLE001
        return {}


def http_ok(url: str, timeout: float = 20, data: bytes | None = None, headers: dict | None = None) -> tuple[bool, str]:
    t = time.time()
    try:
        req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return True, f"{r.status} ({time.time() - t:.1f} sn)"
    except urllib.error.HTTPError as e:
        return e.code < 500, f"HTTP {e.code} ({time.time() - t:.1f} sn)"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__} ({time.time() - t:.1f} sn)"


def run_cmd(ctx: Ctx, step: str, args: list[str], timeout_h: float = 8) -> bool:
    """`uv run python -m ...` komutunu çalıştırır, çıktıyı log'a yazar, son satırları gösterir."""
    LOGS.mkdir(parents=True, exist_ok=True)
    log = LOGS / f"{step}.log"
    cmd = ["uv", "run", "python", "-m", *args]
    print(f"  $ {' '.join(cmd)}\n    (log: {log.relative_to(ROOT)})")
    t = time.time()
    with open(log, "a") as f:
        f.write(f"\n===== {dt.datetime.now().isoformat(timespec='seconds')} {' '.join(cmd)}\n")
        f.flush()
        try:
            p = subprocess.run(cmd, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT, timeout=timeout_h * 3600)
            code = p.returncode
        except subprocess.TimeoutExpired:
            code = -1
    tail = log.read_text(errors="replace").strip().splitlines()[-6:]
    for line in tail:
        print(f"    | {line[:160]}")
    print(f"    süre {(time.time() - t) / 60:.1f} dk, çıkış kodu {code}")
    return code == 0


def areas_file(ctx: Ctx) -> Path:
    return Path(ctx.a.areas) if ctx.a.areas else (AREAS_US if AREAS_US.exists() else AREAS_EXAMPLE)


def area_ids(path: Path) -> list[str]:
    return [a["area_id"] for a in json.loads(path.read_text())] if path.exists() else []


def limited_areas(ctx: Ctx) -> Path:
    """--max-fields verildiyse ilk N tarlayı içeren geçici alan dosyası."""
    src = areas_file(ctx)
    if not ctx.a.max_fields:
        return src
    out = CACHE / f"areas_first{ctx.a.max_fields}.json"
    out.write_text(json.dumps(json.loads(src.read_text())[: ctx.a.max_fields], indent=1))
    return out


def raw_coverage(source: str, column_hint: str | None = None) -> dict[str, int]:
    """Ham dosyada alan başına satır sayısı (değer sütunu dolu olanlar)."""
    import pandas as pd

    p = RAW / f"{source}.parquet"
    if not p.exists():
        return {}
    d = pd.read_parquet(p)
    if column_hint:
        cols = [c for c in d.columns if column_hint.lower() in c.lower()]
        if cols:
            d = d[d[cols].notna().any(axis=1)]
    return d.groupby("area_id").size().to_dict()


# ---------------------------------------------------------------- adımlar
def preflight(ctx: Ctx) -> bool:
    ok = True
    v = sys.version_info
    ctx.note("preflight", OK if v >= (3, 12) else FAIL, f"Python {v.major}.{v.minor}")
    ctx.note("preflight", OK if shutil.which("uv") else FAIL, "uv " + ("var" if shutil.which("uv") else "YOK"))
    try:
        import xgboost as xgb

        cuda_build = bool(xgb.build_info().get("USE_CUDA"))
        smi = shutil.which("nvidia-smi")
        gpu = subprocess.run([smi, "-L"], capture_output=True, text=True, timeout=10).stdout.strip() if smi else ""
        if cuda_build and gpu:
            ctx.note("preflight", OK, f"GPU: {gpu.splitlines()[0][:80]} (xgboost {xgb.__version__}, CUDA)")
        else:
            ctx.note("preflight", WARN, "GPU kullanılamıyor -> eğitim CPU'da (daha yavaş ama çalışır)")
    except Exception as e:  # noqa: BLE001
        ok = False
        ctx.note("preflight", FAIL, f"xgboost yüklenemedi: {e}. Çözüm: uv sync")
    free = shutil.disk_usage(ROOT).free / 1e9
    ctx.note("preflight", OK if free > 5 else WARN, f"boş disk {free:.0f} GB")
    # Earthdata
    try:
        has = netrc.netrc().authenticators("urs.earthdata.nasa.gov") is not None
    except (FileNotFoundError, netrc.NetrcParseError):
        has = False
    if not has:
        ok = False
        ctx.note("preflight", FAIL, "~/.netrc içinde Earthdata girişi yok (AppEEARS çalışmaz)")
        ctx.todo.append("~/.netrc'ye Earthdata girişini ekleyin (ml/README.md), sonra tekrar çalıştırın")
    else:
        import base64

        u, _, p = netrc.netrc().authenticators("urs.earthdata.nasa.gov")
        good, info = http_ok("https://urs.earthdata.nasa.gov/api/users/tokens",
                             headers={"Authorization": "Basic " + base64.b64encode(f"{u}:{p}".encode()).decode()})
        ctx.note("preflight", OK if good else FAIL, f"Earthdata girişi {'geçerli' if good else 'BAŞARISIZ'} ({info})")
        ok &= good
    keys = env_keys()
    for k, need in (("NASS_API_KEY", True), ("OPENET_API_KEY", False), ("AMS_API_KEY", False)):
        st = OK if k in keys else (FAIL if need else WARN)
        ctx.note("preflight", st, f"ml/.env {k}: {'var' if k in keys else 'YOK'}")
        if k not in keys and need:
            ctx.todo.append(f"ml/.env içine {k}=... ekleyin")
    # gerçek veri uçlarına çok küçük sorgular (web sayfaları değil: CDL sayfasının SSL zinciri bozuk, servisi sağlam)
    for name, url in (("NASA POWER", "https://power.larc.nasa.gov/api/temporal/daily/point?parameters=T2M"
                                     "&community=AG&longitude=-93.7&latitude=42.0&start=20240101&end=20240101"
                                     "&format=JSON"),
                      ("AppEEARS", "https://appeears.earthdatacloud.nasa.gov/api/product"),
                      ("USDA CDL", "https://nassgeodata.gmu.edu/axis2/services/CDLService/GetCDLValue"
                                   "?year=2024&x=185800&y=2116500"),
                      ("USDA SSURGO (SDA)", "https://sdmdataaccess.sc.egov.usda.gov/"),
                      ("USDA NASS", "https://quickstats.nass.usda.gov/"),
                      ("Census geocoder", "https://geocoding.geo.census.gov/geocoder/"),
                      ("OpenET", "https://openet-api.org/")):
        good, info = http_ok(url, timeout=60)
        good = good and not info.startswith("HTTP 4")
        ctx.note("preflight", OK if good else WARN, f"{name} erişimi: {info}")
    return ok


def step_fields(ctx: Ctx) -> bool:
    if ctx.a.areas:
        ctx.note("fields", SKIP, f"--areas verildi ({ctx.a.areas}); tarla seçimi atlandı")
        return True
    if AREAS_US.exists() and not ctx.force("fields"):
        n = len(area_ids(AREAS_US))
        ctx.note("fields", OK, f"{AREAS_US.relative_to(ROOT)} var: {n} tarla (yeniden seçmek için --only fields --force)")
        return n > 0
    args = ["ml.data_load.fields", "select", "--out", str(AREAS_US), "--years", "2015-2025"]
    if ctx.a.max_fields:
        args += ["--max", str(ctx.a.max_fields * 2)]  # bir kısmı CDL'de elenir
    good = run_cmd(ctx, "fields", args)
    n = len(area_ids(AREAS_US))
    ctx.note("fields", OK if good and n else FAIL, f"seçilen tarla: {n}")
    if n and n < 20:
        ctx.todo.append(f"Yalnızca {n} tarla seçildi; model için 20+ önerilir (fields.py candidate_points'e aday ekleyin)")
    return n > 0


def raw_span(source: str, column_hint: str | None = None) -> dict[str, tuple]:
    """Ham dosyada alan başına (ilk tarih, son tarih, satır sayısı)."""
    import pandas as pd

    p = RAW / f"{source}.parquet"
    if not p.exists():
        return {}
    d = pd.read_parquet(p)
    if column_hint:
        cols = [c for c in d.columns if column_hint.lower() in c.lower()]
        if cols:
            d = d[d[cols].notna().any(axis=1)]
    g = d.groupby("area_id")["time"].agg(["min", "max", "size"])
    return {a: (r["min"], r["max"], int(r["size"])) for a, r in g.iterrows()}


def covers(span: tuple | None, start: str, end: str, slack_days: int = 20) -> bool:
    import pandas as pd

    if not span:
        return False
    return span[0] <= pd.Timestamp(start) + pd.Timedelta(days=slack_days) and \
        span[1] >= pd.Timestamp(end) - pd.Timedelta(days=slack_days)


def step_power(ctx: Ctx) -> bool:
    areas = limited_areas(ctx)
    want = set(area_ids(areas))
    have = {a for a, sp in raw_span("POWER").items() if covers(sp, ctx.a.start, ctx.a.end, 5)}
    if want <= have and not ctx.force("power"):
        ctx.note("power", OK, f"POWER tüm {len(want)} tarlada {ctx.a.start}..{ctx.a.end} aralığını kapsıyor")
        return True
    run_cmd(ctx, "power", ["ml.data_load.download", "--areas", str(areas), "--start", ctx.a.start,
                           "--end", ctx.a.end, "--out", str(RAW), "--sources", "power", "--workers", "3"])
    have = {a for a, sp in raw_span("POWER").items() if covers(sp, ctx.a.start, ctx.a.end, 5)}
    miss = sorted(want - have)
    ctx.note("power", OK if not miss else WARN, f"POWER: {len(want & have)}/{len(want)} tarla tam aralıkta" +
             (f", eksik: {', '.join(miss[:8])}" if miss else ""))
    if miss:
        ctx.todo.append("POWER eksik tarlalar için: uv run python -m ml.run_all --only power")
    return bool(have)


NDVI_PER_YEAR = 46  # Terra 23 + Aqua 23 kompozit
NDVI_MIN_FRACTION = 0.6  # beklenenin %60'ı altı "eksik" (2025 yazındaki katalog boşluğu ~%5-10 götürür)


def ndvi_counts() -> dict[str, int]:
    out: dict[str, int] = {}
    for src in ("MOD13Q1", "MYD13Q1"):
        for a, n in raw_coverage(src, "NDVI").items():
            out[a] = out.get(a, 0) + n
    return out


def ndvi_complete(ctx: Ctx) -> set[str]:
    import pandas as pd

    years = (pd.Timestamp(ctx.a.end) - pd.Timestamp(ctx.a.start)).days / 365.25
    need = NDVI_MIN_FRACTION * NDVI_PER_YEAR * years
    return {a for a, n in ndvi_counts().items() if n >= need}


def step_ndvi(ctx: Ctx) -> bool:
    """NDVI: önce Microsoft Planetary Computer (hızlı, girişsiz; MOD13Q1 + MYD13Q1), olmazsa ORNL (yavaş)."""
    areas = limited_areas(ctx)
    want = set(area_ids(areas))
    if want <= ndvi_complete(ctx) and not ctx.force("ndvi"):
        ctx.note("ndvi", OK, f"NDVI tüm {len(want)} tarlada yeterli")
        return True
    before = ndvi_counts()
    run_cmd(ctx, "ndvi", ["ml.data_load.planetary", "--areas", str(areas), "--start", ctx.a.start, "--end",
                          ctx.a.end, "--out", str(RAW), "--workers", "12", "--area-workers", "3"])
    after = ndvi_counts()
    done = want & ndvi_complete(ctx)
    some = want & set(after)
    ctx.note("ndvi", OK if done == want else (WARN if some else FAIL),
             f"NDVI (Planetary Computer): {len(done)}/{len(want)} tarla yeterli, {len(some)} tarlada veri var")
    if after == before and done != want:  # Planetary hiç yeni veri getirmedi -> ORNL yedeği (son 2 yıl; yavaş)
        return ornl_ndvi_fallback(ctx, areas, want)
    if done != want:
        ctx.todo.append("Bazı tarlalarda NDVI eksik; tekrar: uv run python -m ml.run_all --only ndvi,build,train,verify")
    ctx.todo.append("Bilgi: Planetary Computer kataloğunda 2025-06-26..2025-11-17 MODIS boşluğu var (2025 yazı). "
                    "İsterseniz ORNL ile doldurulabilir (yavaş): uv run python -m ml.data_load.download --areas "
                    "ml/data_load/areas.us.json --start 2025-06-01 --end 2025-11-30 --out ml/cache/raw --sources modis "
                    "--workers 1")
    return bool(some)


ORNL_MIN_COMPOSITES = 35  # ORNL yedeği: 2 yılda ~46 Terra kompoziti beklenir


def ornl_ndvi_fallback(ctx: Ctx, areas: Path, want: set[str]) -> bool:
    """Planetary yoksa NDVI'yı ORNL MODIS servisinden çeker (yavaş, girişsiz). Yalnızca son 2 yıl.
    ORNL aşırı yüklenince 503 verdiği için tek işçiyle ve uzun beklemelerle çalışır; yarım kalan tarlalar
    (az kompozit) sonraki turlarda tekrar denenir."""
    import pandas as pd

    start = max(pd.Timestamp(ctx.a.start), pd.Timestamp(ctx.a.end) - pd.Timedelta(days=730)).date().isoformat()

    def complete() -> set[str]:
        return {a for a, n in raw_coverage("MOD13Q1", "NDVI").items() if n >= ORNL_MIN_COMPOSITES}

    ctx.note("ndvi", WARN, f"yedek: NDVI ORNL'den çekiliyor ({len(want - complete())} tarla, {start}..; yavaş)")
    for _ in (1, 2, 3):  # ORNL bazı istekleri düşürür; sonraki turlar yalnızca eksik/yarım tarlaları dener
        miss = sorted(want - complete())
        if not miss:
            break
        sub = CACHE / "areas_ornl_missing.json"
        sub.write_text(json.dumps([a for a in json.loads(areas.read_text()) if a["area_id"] in miss]))
        run_cmd(ctx, "ornl_ndvi", ["ml.data_load.download", "--areas", str(sub), "--start", start, "--end",
                                   ctx.a.end, "--out", str(RAW), "--sources", "modis", "--workers", "1"])
    got = want & set(raw_coverage("MOD13Q1", "NDVI"))
    ctx.note("ndvi", OK if got == want else (WARN if got else FAIL), f"yedek NDVI (ORNL): {len(got)}/{len(want)} tarla")
    if got != want:
        ctx.todo.append("Bazı tarlalarda NDVI eksik; tekrar: uv run python -m ml.run_all --only ndvi,build,train,verify")
    return bool(got)


def appeears_smoke(ctx: Ctx, areas: Path) -> bool:
    """Tam indirmeden önce 1 tarla x 2 ay deneme + biçim kontrolü (gerçek CSV biçimi burada doğrulanır)."""
    if ctx.state.get("appeears_smoke_ok") and not ctx.force("appeears"):
        return True
    import pandas as pd

    from ml.data_load import clean

    first = json.loads(areas.read_text())[:1]
    smoke_dir = CACHE / "appeears_smoke"
    one = CACHE / "areas_smoke1.json"
    one.write_text(json.dumps(first))
    run_cmd(ctx, "appeears_smoke", ["ml.data_load.appeears", "--areas", str(one), "--start", "2024-06-01",
                                    "--end", "2024-07-31", "--out", str(smoke_dir), "--static-out",
                                    str(smoke_dir / "static"), "--products", "lst,smap", "--check",
                                    "--timeout-min", "60"], timeout_h=1.5)
    problems = []
    if not any((smoke_dir / f"{s}.parquet").exists() for s in ("MOD11A2", "SPL4SMGP")):
        problems.append("deneme dosyası oluşmadı")
    for src, var, lo, hi in (("MOD11A2", "lst_day_c", -40, 70), ("SPL4SMGP", "sm_surface", 0, 1)):
        g = smoke_dir / f"{src}.parquet"
        if g.exists():
            long, _ = clean.clean_frame(pd.read_parquet(g), hours=None)
            v = long.loc[long["var"].astype(str) == var, "value"]
            if len(v) and not v.between(lo, hi).all():
                problems.append(f"{var} aralık dışı ({v.min():.2f}..{v.max():.2f})")
            elif len(v):
                ctx.note("appeears", OK, f"biçim kontrolü: {var} {v.min():.2f}..{v.max():.2f} doğru")
    if problems:
        log = (LOGS / "appeears_smoke.log").read_text(errors="replace") if (LOGS / "appeears_smoke.log").exists() else ""
        outage = "504" in log or "502" in log or "503" in log or "login" in log.lower() and "giving up" in log
        ctx.note("appeears", WARN if outage else FAIL, "AppEEARS kullanılamadı: " + "; ".join(problems)
                 + (" (sunucu/giriş kesintisi)" if outage else ""))
        if outage:
            ctx.todo.append("AppEEARS kesintisi bitince SMAP/LST/ET/eğim için tekrar: "
                            "uv run python -m ml.run_all --only appeears,build,train,verify")
        else:
            ctx.todo.append("AppEEARS biçimi beklenenden farklı; ml/cache/logs/appeears_smoke.log dosyasını "
                            "(içinde şifre yok) Claude'a gönderin")
        return False
    ctx.state["appeears_smoke_ok"] = True
    ctx.save()
    return True


def step_appeears(ctx: Ctx) -> bool:
    """AppEEARS: ek uydu verisi (SMAP toprak nemi, yüzey sıcaklığı, ET, SRTM eğim). Çalışmazsa akış DURMAZ
    (NDVI ayrı adımda Planetary'den gelir); model bu verilerle daha iyi olur ama onlarsız da eğitilir."""
    areas = limited_areas(ctx)
    want = set(area_ids(areas))
    checks = (("MOD11A2", "LST", "yüzey sıcaklığı"), ("MOD16A2GF", "et", "MODIS ET"),
              ("SPL4SMGP", "sm_", "SMAP toprak nemi"))
    have_all = all(want <= set(raw_coverage(src, hint)) for src, hint, _ in checks)
    if have_all and (STATIC / "elevation.parquet").exists() and not ctx.force("appeears"):
        ctx.note("appeears", OK, "SMAP/LST/ET/eğim tüm tarlalarda var")
        return True
    if not appeears_smoke(ctx, areas):
        ctx.note("appeears", WARN, "AppEEARS atlandı; model SMAP/LST/ET olmadan eğitilecek")
        return True
    run_cmd(ctx, "appeears", ["ml.data_load.appeears", "--areas", str(areas), "--start", ctx.a.start,
                              "--end", ctx.a.end, "--out", str(RAW), "--static-out", str(STATIC),
                              "--products", ctx.a.appeears_products, "--timeout-min", str(ctx.a.appeears_timeout)])
    for src, hint, label in checks:
        got = want & set(raw_coverage(src, hint))
        ctx.note("appeears", OK if got == want else WARN, f"{label}: {len(got)}/{len(want)} tarla")
    if not all(want <= set(raw_coverage(src, hint)) for src, hint, _ in checks):
        ctx.todo.append("AppEEARS işleri bitmemiş olabilir (sunucuda saatler sürebilir). Sonra: "
                        "uv run python -m ml.run_all --only appeears,build,train,verify (kaldığı yerden devam eder)")
    return True


def step_usda(ctx: Ctx) -> bool:
    areas = limited_areas(ctx)
    done = all((STATIC / f).exists() for f in ("counties.parquet", "nass_yields.parquet", "nass_prices.parquet"))
    if done and not ctx.force("usda"):
        ctx.note("usda", OK, "NASS verim/fiyat ve ilçe bilgisi var")
        return True
    what = "counties,yields,prices" + (",openet" if "OPENET_API_KEY" in env_keys() else "")
    good = run_cmd(ctx, "usda", ["ml.data_load.usda", "--areas", str(areas), "--start", ctx.a.start, "--end",
                                 ctx.a.end, "--years", "2015-2025", "--static-out", str(STATIC), "--raw-out", str(RAW),
                                 "--what", what])
    for f, label in (("nass_yields.parquet", "NASS ilçe verimleri"), ("nass_prices.parquet", "NASS fiyatları"),
                     ("counties.parquet", "ilçe eşleşmesi")):
        ctx.note("usda", OK if (STATIC / f).exists() else WARN, f"{label}: {'var' if (STATIC / f).exists() else 'YOK'}")
    if (STATIC / "nass_yields.parquet").exists():
        import pandas as pd

        y = pd.read_parquet(STATIC / "nass_yields.parquet")
        crops = sorted(y["crop_key"].dropna().unique()) if "crop_key" in y else []
        ctx.note("usda", OK if crops else WARN, f"verimi bulunan ürünler: {', '.join(crops) or 'yok'}")
        missing = {"corn", "soybean", "winter_wheat", "sorghum", "cotton"} - set(crops)
        if missing:
            ctx.todo.append(f"NASS'ta şu ürünlerin verimi gelmedi: {', '.join(sorted(missing))} "
                            "(usda.py'deki NASS short_desc metinleri kontrol edilmeli)")
    return good


def step_build(ctx: Ctx) -> bool:
    areas = limited_areas(ctx)
    good = run_cmd(ctx, "build", ["ml.data_load.build", "--raw", str(RAW), "--areas", str(areas),
                                  "--out", str(FEATURES), "--static", str(STATIC)])
    rp = FEATURES.with_suffix(".report.json")
    if not (good and rp.exists()):
        ctx.note("build", FAIL, "özellik tablosu üretilemedi (log'a bakın)")
        return False
    r = json.loads(rp.read_text())
    ctx.note("build", OK, f"{r['rows']} alan-hafta satırı, {r['n_features']} özellik, "
                          f"gözlenmiş hedefli {r['rows_with_observed_target']} satır, {len(r['areas'])} tarla")
    no_ndvi = sorted(set(r["areas"]) - set(r.get("areas_with_ndvi", r["areas"])))
    if no_ndvi:
        ctx.note("build", WARN, f"NDVI'siz tarla: {len(no_ndvi)} ({', '.join(no_ndvi[:6])}...)")
    st = r.get("static") or {}
    if st:
        ctx.note("build", OK if not st.get("missing_soil") else WARN,
                 f"toprak: {st.get('soil_areas')} tarla, ürün geçmişi: {st.get('cdl_areas')} tarla")
    if r["rows_with_observed_target"] < 500:
        ctx.todo.append(f"Gözlenmiş NDVI hedefi az ({r['rows_with_observed_target']}); daha çok tarla/NDVI gerekli")
    return r["rows_with_observed_target"] > 0


def step_train(ctx: Ctx) -> bool:
    good = run_cmd(ctx, "train", ["ml.model.train", "--features", str(FEATURES)])
    latest = ML / "runs" / "LATEST"
    if not (good and latest.exists()):
        ctx.note("train", FAIL, "eğitim başarısız (log: ml/cache/logs/train.log)")
        return False
    run_dir = Path(latest.read_text().strip())
    m = json.loads((run_dir / "metrics.json").read_text())
    t = m["test_temporal"]
    ctx.state["last_model"] = str(run_dir)
    ctx.note("train", OK, f"model: {run_dir.name} (cihaz {m['device']})")
    if t.get("n"):
        beat = t["beats_baselines"]
        ctx.note("train", OK if beat else WARN,
                 f"test RMSE xgb={t['xgb']['rmse']:.4f} kalıcılık={t['persistence']['rmse']:.4f} "
                 f"klimatoloji={t['climatology']['rmse']:.4f} -> bazları {'GEÇTİ' if beat else 'GEÇEMEDİ'}")
        cov = t["interval_conformal"]["coverage"]
        ctx.note("train", OK if 0.7 <= cov <= 0.9 else WARN, f"%80 aralık kapsaması {cov:.2f} (hedef ~0.80)")
        if not beat:
            ctx.todo.append("Model bazları geçemedi: daha çok tarla (fields), NDVI kapsamı ve 2015+ yıllar deneyin")
    ah = m.get("area_holdout", {})
    if ah.get("folds"):
        ctx.note("train", OK, f"alan dışarıda tutma: xgb {ah['xgb_rmse_mean']:.4f} / kalıcılık "
                              f"{ah['persistence_rmse_mean']:.4f}")
    return True


def step_verify(ctx: Ctx) -> bool:
    p = subprocess.run(["uv", "run", "pytest", "-q"], cwd=ROOT, capture_output=True, text=True, timeout=1800)
    last = (p.stdout.strip().splitlines() or ["?"])[-1]
    ctx.note("verify", OK if p.returncode == 0 else FAIL, f"testler: {last}")
    if p.returncode != 0:
        (LOGS / "verify.log").write_text(p.stdout + p.stderr)
        ctx.todo.append("Testler başarısız: ml/cache/logs/verify.log")
    model = ctx.state.get("last_model")
    if model:
        ctx.todo.append(f"Modeli backend makinesine kopyalayın: scp -r {model} <backend-pc>:~/Projeler/space-apps-2026/"
                        "ml/runs/ ; orada: realpath ml/runs/<klasör> > ml/runs/LATEST ; "
                        "systemctl --user restart space-backend")
    return p.returncode == 0


def inventory(ctx: Ctx) -> None:
    """İndirmeden: eldeki ham/statik verinin özeti."""
    import pandas as pd

    print("\n== Veri envanteri")
    for f in sorted(RAW.glob("*.parquet")):
        d = pd.read_parquet(f)
        ctx.note("inventory", OK, f"raw/{f.name}: {d['area_id'].nunique()} tarla, {len(d)} satır, "
                                  f"{d['time'].min().date()}..{d['time'].max().date()}")
    for f in sorted(STATIC.glob("*.parquet")):
        d = pd.read_parquet(f)
        ctx.note("inventory", OK, f"static/{f.name}: {len(d)} satır")
    if not any(RAW.glob("*.parquet")):
        ctx.note("inventory", WARN, "henüz ham veri yok")


# ---------------------------------------------------------------- ana akış
def write_report(ctx: Ctx, t0: float) -> None:
    lines = [f"# run_all raporu — {dt.datetime.now().isoformat(timespec='minutes')}",
             f"Süre: {(time.time() - t0) / 60:.1f} dk · Alan dosyası: {areas_file(ctx).relative_to(ROOT)}"
             + (f" (ilk {ctx.a.max_fields})" if ctx.a.max_fields else ""), ""]
    cur = None
    for step, st, detail in ctx.results:
        if step != cur:
            lines += ["", f"## {step}"]
            cur = step
        lines.append(f"- {st} {detail}")
    lines += ["", "## Yapılması gerekenler" if ctx.todo else "## Eksik yok"]
    lines += [f"- {t}" for t in dict.fromkeys(ctx.todo)]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help=f"virgülle adımlar: {','.join(STEPS)}")
    ap.add_argument("--skip", default="", help="atlanacak adımlar")
    ap.add_argument("--force", action="store_true", help="--only ile verilen adımları tamam olsa da yeniden çalıştır")
    ap.add_argument("--check-only", action="store_true", help="indirme/eğitim yok: ön kontrol + envanter")
    ap.add_argument("--areas", help="alan dosyası (varsayılan: areas.us.json, yoksa areas.example.json)")
    ap.add_argument("--max-fields", type=int, help="deneme için ilk N tarla")
    ap.add_argument("--start", default="2018-01-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--appeears-products", default=APPEEARS_PRODUCTS)
    ap.add_argument("--appeears-timeout", type=int, default=240, help="AppEEARS bekleme süresi (dk)")
    ap.add_argument("--continue-on-error", action="store_true")
    a = ap.parse_args(argv)
    ctx = Ctx(a)
    forced = set((a.only or "").split(",")) if a.force else set()
    ctx.force = lambda step: step in forced  # type: ignore[attr-defined]
    t0 = time.time()
    os.chdir(ROOT)

    if a.check_only:
        print("== preflight")
        preflight(ctx)
        inventory(ctx)
        write_report(ctx, t0)
        print(f"\nRapor: {REPORT.relative_to(ROOT)}")
        return 0

    steps = [s for s in (a.only.split(",") if a.only else STEPS) if s and s not in a.skip.split(",")]
    funcs = {"preflight": preflight, "fields": step_fields, "power": step_power, "ndvi": step_ndvi,
             "appeears": step_appeears,
             "usda": step_usda, "build": step_build, "train": step_train, "verify": step_verify}
    status = 0
    for s in steps:
        print(f"\n== {s}")
        try:
            good = funcs[s](ctx)
        except Exception as e:  # noqa: BLE001
            good = False
            ctx.note(s, FAIL, f"beklenmeyen hata: {type(e).__name__}: {str(e)[:200]}")
        ctx.state[s] = {"ok": bool(good), "at": dt.datetime.now().isoformat(timespec="seconds")}
        ctx.save()
        if not good:
            status = 1
            if s in ("preflight", "build", "train") and not a.continue_on_error:
                ctx.todo.append(f"'{s}' başarısız olduğu için sonraki adımlar çalıştırılmadı "
                                "(yine de devam etmek için --continue-on-error)")
                break
    write_report(ctx, t0)
    print(f"\n== Özet ({(time.time() - t0) / 60:.1f} dk) — rapor: {REPORT.relative_to(ROOT)}")
    for t in dict.fromkeys(ctx.todo):
        print(f"  • {t}")
    if not ctx.todo:
        print("  Eksik yok.")
    return status


if __name__ == "__main__":
    sys.exit(main())
