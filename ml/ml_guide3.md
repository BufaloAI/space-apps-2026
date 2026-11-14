Hatanın nedeni NDVI verisinin hiç inmemiş olması. NASA'nın MODIS servisi (ORNL) 6 tarlanın hepsinde 503 ya da zaman aşımı vermiş. NDVI olmayınca özellik tablosunda modelin öğreneceği hedef (target_ndvi) oluşmuyor, eğitim de KeyError ile çöküyor.

O bilgisayar eski indirici kodunu çalıştırıyor; logdaki her hata .../MOD13Q1/dates?... isteğinde. Bu istek sunucunun en yavaş ucu, tek başına 50 saniyeyi geçiyor. Yeni kod bu isteği hiç atmıyor. Ayrıca bir parça düşerse sadece o parçayı atlıyor, tarlanın bütün NDVI verisi kaybolmuyor.

Çözüm (diğer bilgisayarda)

1. Son kodu al. Değişiklikler henüz commit edilmedi: commit edip push et, o bilgisayarda git pull yap.
2. Sadece NDVI'yı tekrar indir. POWER zaten inmiş ve yeni veri mevcut dosyaya ekleneceği için tekrar indirmeye gerek yok:
uv run python -m ml.data_load.download --areas ml/data_load/areas.example.json \
    --start 2024-01-01 --end 2025-12-31 --out ml/cache/raw --sources modis --workers 1
   --workers 1 yeni: ORNL'ye aynı anda tek istek gider. Hatanın bir kısmı aşırı yüklenmeden geliyor olabilir. Daha yavaş ama daha güvenli. Sonunda failures: N yazarsa aynı komutu tekrar çalıştır; inen tarlalar korunur, sadece eksikler denenir. ORNL çok kötü durumdaysa birkaç saat sonra ya da gece denemek gerekebilir.
3. Özellik tablosunu yeniden kur ve eğit:
uv run python -m ml.data_load.build --raw ml/cache/raw \
    --areas ml/data_load/areas.example.json --out ml/cache/features.parquet
uv run python -m ml.model.train --features ml/cache/features.parquet

Kestirme yol: bu bilgisayarda Iowa, Illinois ve Nebraska için inmiş NDVI var (ml/cache/raw/MOD13Q1.parquet, 6 KB). Bu dosyayı diğer bilgisayarın ml/cache/raw/ klasörüne kopyalarsan hemen eğitebilirsin. Ancak 3 tarlayla model zayıf kalır; bu bilgisayardaki deneme modeli de klimatoloji bazını geçemedi.

Koda eklediklerim

- Eğitim: NDVI yoksa KeyError yerine nedeni ve çözüm komutunu yazan açık bir hata veriyor. NDVI'siz bir tabloyla aynı durumu tekrar üretip mesajın çıktığını kontrol ettim.
- Build: NDVI hiç yoksa "model EĞİTİLEMEZ" uyarısı veriyor. Bazı tarlalarda NDVI eksikse o tarlaları tek tek listeliyor.
- İndirici: --workers seçeneği eklendi.
- Testler: 104'ün hepsi geçiyor.

Kalıcı bir çözüm olarak, Earthdata hesabınla çalışan ikinci bir NDVI kaynağı (HLS, 30 m) ekleyebilirim. Böylece ORNL çöktüğünde sistem ona geçer; backend'deki 2 dakikalık NDVI bekleme süresi de kısalır. İstersen ilk iş onu yaparım.