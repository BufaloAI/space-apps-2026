Kod yazıldı ve uçtan uca çalışıyor, ancak şu ana kadar yalnızca sentetik örnekle denendi. Gerçek veriyi çekecek indirici ajanı hâlâ çalışıyor. İş bitince ajan POWER ve MODIS'ten 6 ABD tarlası için 2024–2025 verisini indirmeyi deneyecek. Veri gelirse aynı hattı bir de gerçek veriyle çalıştırıp tam raporu vereceğim.
Şimdiye kadar durum:
- Temizleme: ml/data_load/ yazıldı. Dolgu değerleri (-999, -3000, -9999.9) NaN'a çevriliyor, ölçekler ve QA uygulanıyor.  Alt-günlük ürünlerden günde 00/06/12/18 UTC'ye en yakın 4 örnek alınıyor.
- Budama: yarısından fazlası boş sütun ve satırlar, korelasyonu 0,95'in üstündeki çiftlerden biri ve neredeyse aynı  satırlar atılıyor. Bu kurallar yalnızca eğitim kümesinden öğreniliyor.
- Çıktı: her satır bir alan-hafta olan features.parquet. Bütün özellikler float64, eksikler NaN; XGBoost'a doğrudan  verilebiliyor.
- Model: ml/model/ yazıldı. XGBoost 2 hafta sonraki NDVI'yı p10/p50/p90 aralığıyla tahmin ediyor. Bunun üstüne risk  skoru (0–100), rotasyon önerileri ve yerel veriyle kalibrasyon eklendi.
- Sentetik test sonucu: XGBoost hatası (RMSE) 0.030; kalıcılık bazı 0.045, klimatoloji bazı 0.031. Sentetik veride bu  bir başarı ölçüsü sayılmaz, yalnızca hattın çalıştığını gösteriyor.
- Testler: uv run pytest ile 95 test geçiyor (backend'in 80'i + yeni 15 ML testi).
✻ Waiting for 1 background agent to finish
❯ Dosyaları bu bilgisayarın hafızasına mı indiriyor ve tamamını mı indiriyor. Kodun tamamını kolay çalışabilecek birşekilde açıklarsan Nvidia GPU olan diğer bilgisayarımda çalıştırmak istiyorum.

  Ran 3 shell commands

Kısa cevap: hayır. Veriyi RAM'de tutmuyor ve dosyaların tamamını da indirmiyor, sadece seçtiğin tarlaların noktalarını
çekip diske küçük parquet dosyaları yazıyor. İndirici ajanı hâlâ çalışıyor. Aşağıdakiler kodun ve şu ana kadarki çalışma
kaydının durumu.

İndirici dosyaları nereye ve ne kadar indiriyor?

┌───────────────────────┬─────────────────────────────────────────┬────────────────────────────────┬───────────────┐
│        Kaynak         │               Ne çekiyor                │         Nereye yazıyor         │     Boyut     │
├───────────────────────┼─────────────────────────────────────────┼────────────────────────────────┼───────────────┤
│ NASA POWER (sıcaklık, │ Her tarla için tek bir nokta, günlük    │ ml/cache/raw/POWER.parquet     │ 3 tarla × 2   │
│  yağış, nem…)         │ değerler                                │ (diske)                        │ yıl ≈ 88 KB   │
├───────────────────────┼─────────────────────────────────────────┼────────────────────────────────┼───────────────┤
│ MODIS NDVI (ORNL      │ Her tarla için tek bir 250 m piksel, 16 │ ml/cache/raw/MOD13Q1.parquet   │ birkaç yüz KB │
│ servisi)              │  günlük değerler                        │                                │               │
├───────────────────────┼─────────────────────────────────────────┼────────────────────────────────┼───────────────┤
│ SMAP / GPM            │ Tarla kutusu (bbox) içindeki pikseller; │ aynı klasör, parquet           │ küçük         │
│ (earthaccess)         │  SMAP'tan günde 4 saat (00/06/12/18)    │                                │               │
└───────────────────────┴─────────────────────────────────────────┴────────────────────────────────┴───────────────┘

- Bellekte yalnızca o an işlenen tarlanın verisi duruyor, sonuç diske yazılıyor. Kod tekrar çalıştırılırsa yeni veriyimevcut dosyaya ekliyor ve tekrarları atıyor.
- Zeynel'in kodu bütün dünyayı indiriyordu (yüzlerce GB). Bu indirici dünya dosyası indirmiyor.
- Bir uyarı (SMAP/GPM): dosyalar diske kaydedilmiyor ama her granül internetten açılıp kutu içi kesiliyor. Diske yazılan küçük olsa da internet trafiği tarla verisinden fazla olabilir. Bu kısım Earthdata hesabı ister ve bu makinede hesap olmadığı için gerçek veriyle hiç denenmedi.
- Şu anki durum: POWER verisi 3 tarla için geldi. NASA'nın MODIS servisi (ORNL) yavaş cevap veriyor; zaman aşımları ve 503 hataları var, kod bunları otomatik olarak tekrar deniyor. Bu yüzden indirme uzun sürüyor.
U'lu bilgisayarda çalıştırma
lgisayarlar arası taşıma. Kod henüz commit edilmedi. Değişenler yeni ml/ klasörü ile pyproject.toml, uv.lock ve
.gitignore. Commit'i sen atıyorsun; ya commit edip push et, ya da klasörü kopyala. Sentetik dosyalar, modeller veindirilen veri .gitignore'da olduğu için git'e girmez.

1. Kurulum (bir kez):
nvidia-smi                      # sağ üstte "CUDA Version: 13.x" görünmeli (sürücü yeterince yeni olmalı)
git pull                        # ya da klasörü kopyala
uv sync                         # pandas, xgboost (CUDA destekli), pyarrow
uv sync --group nasa            # sadece SMAP/GPM indirilecekse (earthaccess, xarray, h5py)
uv run pytest ml/tests -q       # 15 test geçmeli
Linux'taki xgboost paketi CUDA 13 ile derlenmiş, ayrıca CUDA kurmaya gerek yok; sadece güncel bir NVIDIA sürücüsü lazım.
Eğitim GPU'yu kendisi bulur. Bulamazsa CPU'ya geçer ve nedenini loga yazar, sessizce devam etmez.

2. Veri (iki seçenek):

a) O bilgisayarda Zeynel'in pipeline'ının ürettiği CSV zaten varsa doğrudan onu ver:
uv run python -m ml.data_load.build --raw /yol/field_shift_optimized_features.csv \
    --areas ml/data_load/areas.example.json --out ml/cache/features.parquet
Temizleyici o CSV'nin şeklini tanıyor: sütunları alt alta eklenmiş ürünler, nv tekrarları, ham dolgu değerleri ve
yil/ay/gun/saat sütunları.

b) Veriyi sıfırdan indir. POWER ve MODIS için hesap gerekmiyor:
uv run python -m ml.data_load.download --areas ml/data_load/areas.example.json \
    --start 2024-01-01 --end 2025-12-31 --out ml/cache/raw --sources power,modis
uv run python -m ml.data_load.build --raw ml/cache/raw \
    --areas ml/data_load/areas.example.json --out ml/cache/features.parquet
areas.example.json, içinde area_id, lat, lon ve isteğe bağlı bbox olan tarlaların listesi. Kendi tarlalarını bu dosyaya ekleyebilirsin; tarla ne kadar çoksa model o kadar iyi öğrenir.

3. Eğitim:
uv run python -m ml.model.train --features ml/cache/features.parquet
Ekran çıktısının sonunda şu satırlar olmalı:
- Logda cihaz: cuda (NVIDIA GPU bulundu).
- Bir test RMSE satırı: model bazları (kalıcılık ve klimatoloji) geçemezse bunu açıkça yazar.

Model ve raporlar ml/runs/<tarih-saat>/ klasörüne kaydediliyor.

4. Bir tarla için sonuç:
uv run python -m ml.model.analyze --features ml/cache/features.parquet \
    --area iowa_ames --existing "corn>soybean" --priorities water=2,soil_health=1.5
Çıktıda üç şey var:
- 2 hafta sonrası için NDVI tahmini (p10/p50/p90)
- 0–100 risk skoru ve bileşenleri
- Mevcut rotasyonla karşılaştırmalı en iyi 3 rotasyon, gerekçeleri ve bilgi boşluklarıyla

--layers eklersen çıktı backend'in beklediği biçimde gelir.

5. (İsteğe bağlı) Yerel veriyle doğruluğu artırma: ml/local_obs.example.csv biçiminde bir CSV doldur. İçine saha/dron NDVI ölçümleri, sulama, toprak organik maddesi ve eğim girilebilir. Sonra:
uv run python -m ml.model.train --features ml/cache/features.parquet --local ml/local_obs.csv
uv run python -m ml.model.analyze --calibrate --features ml/cache/features.parquet --local ml/local_obs.csv

Kodun ne yaptığı, sırasıyla

1. İndirme (download.py): tarla başına küçük veri çeker ve ham değerleri olduğu gibi saklar.
2. Temizleme (clean.py + registry.py): dolgu değerlerini NaN yapar, NDVI ×0,0001 gibi ölçekleri uygular, bulut ve kar piksellerini atar ve günde 4 saati seçer. Her noktayı bir tarlaya bağlar.
3. Özellikler (features.py): veriyi tarla × hafta tablosuna çevirir. Son 8 haftanın NDVI'si, 4 ve 12 haftalık yağış, büyüme derece günü, sıcak/don/kuru günler, toprak nemi anomalisi ve mevsim bilgisi eklenir. Hedef 2 hafta sonraki NDVI'dır.
4. Budama (prune.py): yarısından fazlası boş sütunlar, sabit sütunlar, birbirine çok benzeyen (r > 0,95) sütun çiftlerinden biri ve neredeyse aynı satırlar atılır.
5. Model (train.py): XGBoost bugünden 2 hafta sonraya NDVI değişimini öğrenir ve %80 belirsizlik aralığı verir. Kontrol iki şekilde yapılır: gelecek aylar gizlenerek ve bazı tarlalar tamamen dışarıda tutularak. Sonuç her zaman iki basit tahminle karşılaştırılır.
6. Risk ve rotasyon (risk.py, rotation.py, crops.toml): risk skoru bitki, su, sıcak ve don stresinden oluşur. Rotasyonlar iklim uygunluğu ve senin önceliklerine göre puanlanır. Ürün profilleri genel tahmini değerler, bu yüzden güven skoru bilinçli olarak sınırlı tutuluyor.