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

## Çalıştırma

```bash
# 0) (deneme) sentetik örnek
uv run python -m ml.data_load.sample
# 1) gerçek veri indir (POWER + MODIS NDVI, auth gerekmez)
uv run python -m ml.data_load.download --areas ml/data_load/areas.example.json \
    --start 2024-01-01 --end 2025-12-31 --out ml/cache/raw --sources power,modis
# 2) ML'e hazır tablo (nasa_pipeline CSV'si de verilebilir: --raw /yol/field_shift_optimized_features.csv)
uv run python -m ml.data_load.build --raw ml/cache/raw --areas ml/data_load/areas.example.json \
    --out ml/cache/features.parquet
# 3) eğitim (GPU varsa otomatik CUDA)
uv run python -m ml.model.train --features ml/cache/features.parquet [--local ml/local_obs.csv]
# 4) alan analizi: tahmin + risk + rotasyon
uv run python -m ml.model.analyze --features ml/cache/features.parquet --area iowa_ames \
    --existing "corn>soybean" --priorities water=2,soil_health=1.5
# 5) yerel ölçümlerle kalibrasyon (doğruluk ve güveni artırır)
uv run python -m ml.model.analyze --calibrate --features ml/cache/features.parquet --local ml/local_obs.csv
# testler
uv run pytest ml/tests -q
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
