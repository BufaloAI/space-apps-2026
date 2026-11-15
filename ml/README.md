# ml/: veri hazırlama + XGBoost tahmini + risk + rotasyon

Kararlar ve gerekçeler: [`DECISIONS.md`](DECISIONS.md). Ayarlar: [`config.toml`](config.toml).

```
ml/
  config.toml
  data_load/            # Zeynel'in data-load/ klasöründen ayrı, ML'e hazır veri
    registry.py         # değişken kaydı: ad eşleme, ölçek, dolgu (-999, -3000...), QA
    download.py         # alan bazlı indirici (POWER, ORNL MODIS, earthaccess bbox + 00/06/12/18)
    clean.py            # ham tablo (CSV/parquet, şekli değişebilir) -> temiz uzun tablo
    features.py         # alan x gün -> alan x hafta -> gecikme/pencere/mevsim özellikleri + hedef
    prune.py            # boş/sabit/korelasyonlu sütun, boş satır, neredeyse aynı satır budama
    build.py            # CLI: ham -> features.parquet (+ .report.json)
    sample.py           # SENTETİK nasa_pipeline çıktısı (yalnızca test)
  model/
    train.py            # XGBoost nokta + kantil (p10/p50/p90), bazlar, zamansal + alan bazlı doğrulama
    predict.py          # predict(features_df) -> list[dict] (kararlı şema)
    risk.py             # kısa vadeli risk skoru + alanın uzun vadeli iklimi
    rotation.py         # rotasyon puanlama/sıralama (crops.toml)
    local.py            # yerel veri: eğitime ekleme, kalibrasyon, tarla profili
    analyze.py          # alan için uçtan uca sonuç + backend Layer biçimi
  service.py            # backend'in çağırdığı parsel servisi: güncel veri -> tahmin -> risk -> rotasyon
  local_obs.example.csv # yerel veri biçimi (ÖRNEK değerler)
  tests/
```

## Çalıştırma: tek komut (GPU'lu bilgisayarda)

```bash
uv sync                                   # bağımlılıklar
uv run python -m ml.run_all --check-only  # 1) yalnızca kontrol: GPU, Earthdata, API anahtarları, servisler, eldeki veri
uv run python -m ml.run_all --max-fields 5 # 2) önce küçük deneme (5 tarla)
uv run python -m ml.run_all               # 3) tam çalıştırma (saatler sürebilir; yarıda kalırsa tekrar çalıştırın)
```
Sonunda `ml/cache/run_all_report.md`: her adımın sonucu, eksik veriler ve tamamlamak için komutlar.
Adım günlükleri `ml/cache/logs/<adım>.log`. Tek adım: `--only appeears,build,train --force`.

Gerekenler: `~/.netrc` içinde Earthdata girişi, `ml/.env` içinde `NASS_API_KEY` (zorunlu), `OPENET_API_KEY`
(isteğe bağlı). `ml/.env` git'e girmez; diğer bilgisayara elle kopyalayın.

| Adım | Kaynak | Çıktı |
|---|---|---|
| fields | USDA CDL (ürün geçmişi) + SSURGO (toprak); 132 aday noktadan gerçek tarlalar | `data_load/areas.us.json`, `cache/static/{cdl_history,soil}.parquet` |
| power | NASA POWER günlük hava | `cache/raw/POWER.parquet` |
| ndvi | MODIS NDVI Terra + Aqua (MOD13Q1/MYD13Q1): Microsoft Planetary Computer; olmazsa ORNL | `cache/raw/{MOD13Q1,MYD13Q1}.parquet` |
| appeears | AppEEARS: MODIS LST, ET, SMAP toprak nemi, SRTM eğim (ek veri; çalışmazsa akış durmaz) | `cache/raw/*.parquet`, `cache/static/elevation.parquet` |
| usda | NASS ilçe verimleri + fiyatlar, OpenET (batı eyaletleri) | `cache/static/nass_*.parquet`, `cache/raw/OPENET.parquet` |
| build / train / verify | özellik tablosu, XGBoost, testler | `cache/features.parquet`, `runs/<zaman>/` |

Adımlar tek tek de çalışır:

```bash
uv run python -m ml.data_load.fields select --out ml/data_load/areas.us.json --years 2015-2025
uv run python -m ml.data_load.download --areas ml/data_load/areas.us.json --sources power --out ml/cache/raw \
    --start 2018-01-01 --end 2025-12-31
uv run python -m ml.data_load.planetary --areas ml/data_load/areas.us.json --start 2018-01-01 --end 2025-12-31 \
    --out ml/cache/raw
uv run python -m ml.data_load.appeears --areas ml/data_load/areas.us.json --start 2024-01-01 --end 2025-12-31 \
    --out ml/cache/raw --static-out ml/cache/static --products ndvi,ndvi_aqua,lst,et,smap,elev
uv run python -m ml.data_load.usda --areas ml/data_load/areas.us.json --start 2024-01-01 --end 2025-12-31 \
    --years 2015-2025 --static-out ml/cache/static --raw-out ml/cache/raw --what counties,yields,prices,openet
uv run python -m ml.data_load.build --raw ml/cache/raw --areas ml/data_load/areas.us.json \
    --out ml/cache/features.parquet --static ml/cache/static
uv run python -m ml.model.train --features ml/cache/features.parquet
uv run python -m ml.model.analyze --features ml/cache/features.parquet --area <area_id>
uv run pytest -q
```

Sentetik deneme: `uv run python -m ml.data_load.sample` (yalnızca test).

**Başka bilgisayardan gelen veri** (WhatsApp/USB ile gelen `.parquet` dosyaları): mevcut veriye ekler, tekrarları atar:
```bash
uv run python -m ml.data_load.merge ~/İndirilenler/MOD13Q1.parquet ~/İndirilenler/MYD13Q1.parquet
```

## Çıktı tipi (XGBoost girdisi)
`features.parquet`: bir satır bir alan-hafta (`area_id`, `week` = pazartesi). Bütün özellikler `float64`, eksik
değerler `NaN` (dolgu değerleri temizlendi). `target_ndvi` 2 hafta sonraki NDVI, `target_observed` hedefin gerçekten
gözlenip gözlenmediği. `_` ile başlayan sütunlar (kapsam) ve `target*` özellik değildir (`features.feature_columns`).

## Backend'de kullanma

`backend/.env` içinde `ML_PROVIDER=xgboost`. Backend `ml/runs/LATEST`'in gösterdiği modeli yükler
(ya da `ML_MODEL_DIR=/yol/ml/runs/<zaman>`). Diğer bilgisayarda eğitilen modeli taşımak için:

```bash
# GPU'lu bilgisayarda: eğitimden sonra
scp -r ml/runs/<zaman> <bu-pc>:~/Projeler/space-apps-2026/ml/runs/
# bu bilgisayarda
realpath ml/runs/<zaman> > ml/runs/LATEST
systemctl --user restart space-backend
```
Tahmin CPU'da tarla başına milisaniyeler sürer; GPU gerekmez. Asıl süre veri çekmektir (NDVI 30 sn–2 dk).
