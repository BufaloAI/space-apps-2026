# ML katmanı: kararlar ve varsayımlar

Tarih: 2026-11-14. Kaynak talimat: `ml-bot-instructions.md`.

## Veri
- **`data-load/` (Zeynel) değiştirilmedi.** Yeni kod `ml/data_load/` altında. Klasör adında `-` yerine `_` var,
  çünkü Python `ml.data-load` adını içe aktaramaz.
- `nasa_pipeline.py` çıktısını okuyarak bulunan sorunlar ve yeni koddaki karşılıkları:
  1. Tüm dünya indiriliyordu (bounding box yok). Yeni indirici yalnızca alan noktası/kutusunu çekiyor
     (POWER ve ORNL REST, earthaccess'te `bounding_box`). Alt-günlük ürünlerde günde 4 örnek alınıyor (00/06/12/18 UTC).
  2. `.hdf` uzantısı filtrede yok, bu yüzden MOD13Q1 ve MOD11A1 atlanıyor. SMAP `.h5` dosyasında veri `Geophysical_Data`
     grubunda, lat/lon ise `cell_lat`/`cell_lon` adında olduğu için atlanıyor. Sonuçta pratikte yalnızca GPM işleniyor.
  3. `to_dataframe()` dünya ızgarasını satırlara açıyor (IMERG'in tek günü yaklaşık 6,5 milyon satır), bellek yetmez.
  4. Ürünler alt alta ekleniyor. Bu yüzden her sütun %57–97 boş görünüyor ve "yarısı boş sütunu sil" kuralı ham
     tabloda uygulanamıyor. **Bu kural alan × hafta tablosunda uygulanıyor.**
  5. GPM'deki `time_bnds` değişkeni `nv` boyutunu getiriyor, her satır 2 kez tekrar ediyor. Tekrarların ortalaması alınıyor.
  6. Ölçek ve dolgu uygulanmıyor (NDVI ×0,0001, -3000; LST ×0,02 K, 0; IMERG -9999,9; SMAP -9999; POWER -999)
     ve QA yok. Hepsi `registry.py` içinde tanımlandı.
  7. IMERG V07'de `calendar="julian"` olabilir. `pd.to_datetime(cftime)` başarısız olursa `errors="coerce"`
     tüm satırları siliyor. Doğrulanmadı; yeni kod metin olarak gelen cftime değerini de okuyabiliyor.
  8. `nasa_dataloader.py`: etiket hiç verilmiyor (hep 0), bölme rastgele (zamansal ya da alan bazlı değil),
     PyTorch DataLoader XGBoost için gereksiz.
- Gerçek veri başka makinede olduğu için şekil `sample.py` ile taklit edildi (**SENTETİK**, `synthetic=1`).
- Haftalık anahtar: pazartesi (`week`). MOD13Q1 16 günlük olduğu için değeri dönemin ortasına (+8 gün) taşındı,
  en fazla 2 haftalık iç boşluklar doğrusal dolduruldu. Hedef yalnızca **gerçekten gözlenmiş** haftalardan alınıyor.
- Ufuk: 2 hafta (`horizon_weeks`). 1 haftalık ufuk 16 günlük ürünle anlamsız olurdu.
- Dolgu değerleri doldurulmuyor, NaN yapılıyor; XGBoost NaN'ı kendisi yönetir (`missing=np.nan`).

## Budama (yalnızca eğitim kümesinde öğrenilir)
- Sütun: eksik oranı > %50, sabit, |r| > 0,95 olan çiftlerden biri. Korelasyonda öncelik: korunanlar
  (`ndvi`, mevsim, konum) > az eksikli > kısa adlı (temel değişken).
- Satır: özelliklerinin %50'den fazlası boşsa atılır. Neredeyse aynı satırlar (std/50 adımla nicemleme) tekilleştirilir.

## Model
- XGBoost `hist`. GPU varsa CUDA, yoksa CPU kullanılır ve neden loga yazılır. Bu makinede GPU yok.
- **Hedef modu `delta`:** model "2 hafta sonraki NDVI − bugünkü NDVI" farkını öğrenir. Sentetik testte mutlak hedef
  bazları geçemedi, delta geçti. `config.toml`'dan değiştirilebilir.
- Belirsizlik: tek çok-kantilli model (`reg:quantileerror`, 0,1/0,5/0,9) + doğrulama kümesinde konformal düzeltme (CQR).
- Doğrulama: zamansal (eğitim < 2025-04 < doğrulama < 2025-07 < test) ve alan bazlı (alanlar tamamen dışarıda).
  Bazlar: kalıcılık ve alan × hafta klimatolojisi. Bazlar geçilemezse rapor bunu açıkça yazar.

## Risk ve rotasyon
- Kısa vadeli risk (0–100): bitki stresi %40 (tahmini NDVI'nın "normal − 0,1" altına düşme olasılığı),
  su stresi %30, sıcak %20, don %10. Eksik bileşen olursa ağırlıklar yeniden dağıtılır.
- Güven: aralık genişliği, veri kapsamı, bileşen bulunurluğu ve yerel ölçüm sayısından hesaplanır. **Sentetik modelde
  en fazla 0,3.** Etiket eşikleri backend'deki `Layer` ile aynı.
- Rotasyon puanı = %40 iklim uygunluğu + %60 öncelik ağırlıklı (su, toprak sağlığı, gelir, erozyon, yem).
  Ürün profilleri (`crops.toml`) **sezgisel değerler**. Bu yüzden rotasyon güveninin tavanı 0,7 × model güveni + yerel bonus.
  Mevcut rotasyon her zaman karşılaştırmada yer alır (döngüsel eşleşme: corn>corn = corn_corn).

## Yerel veri
- `local_obs.csv`: (1) NDVI ölçümleri hedefi değiştirir ve ağırlık 3 ile eğitime girer; (2) kalibrasyonda alan bazlı
  sapma n/(n+5) ile küçültülerek uygulanır ve aralık genişliği yeniden hesaplanır; (3) sulama, toprak ve eğim bilgileri
  rotasyon puanına ve güvenine eklenir.

## Açık konular
- Backend `ml.py` henüz bağlanmadı (talimat gereği). `analyze.to_layers()` çıktısı backend `Layer` alanlarıyla uyumlu.
- SRTM eğim ve SSURGO toprak verisi henüz yok; yerel profilden ya da ileride bir kaynaktan alınacak.
- Talimattaki eşzamanlı gece hattı (orchestrator, manifest, heartbeat) bu turda yazılmadı.
- ORNL MODIS servisi çok yavaş (hata cevabı bile ~38 sn), arada 503 veriyor ve istek başına en fazla 10 tarih
  kabul ediyor. Varsayılan bantlar NDVI + pixel_reliability'ye indirildi. Düşen parça atlanıyor; komutu tekrar
  çalıştırmak güvenli (`write_raw` birleştirip tekrarları atıyor). Çok sayıda alan için AppEEARS ya da earthaccess
  (MOD13Q1 HDF4, `pyhdf`/`rioxarray` gerekir) düşünülmeli.
- POWER günleri yerel standart saatle (LST) veriyor; MODIS ve SMAP UTC. Haftalık birleştirmede bu fark önemsiz.

## Ek kaynaklar (2026-11-14)
- ORNL yerine/yanında **AppEEARS** (Earthdata girişi): sunucu tarafında nokta çıkarımı, yalnızca istenen tarla ve
  tarihler iner. Terra + Aqua NDVI birlikte ~8 günde bir gözlem verir.
- **CDL** ürün geçmişi ve **SSURGO** toprak: sabit özellik. Sızıntı kuralı: bir yılın CDL ürünü ertesi yıl
  yayınlandığı için yalnızca önceki yılların ürünleri ve onlardan çıkan "beklenen ürün" kullanılır.
  Backend'de ABD parselinde mevcut rotasyon girilmezse CDL'den çıkarılır (eminlik >= 0.5) ve uyarı gösterilir.
- **NASS** ilçe verimleri ve fiyatları, **OpenET** (yalnızca batı eyaletleri) eklendi.
- Atlananlar: GPM/PRISM/NLDAS (POWER ile örtüşüyor), NAIP (çok büyük), NLCD (CDL yeterli), GEDI, LANDFIRE,
  GAEZ, CoCoRaHS, NWIS (rotasyon kararına katkısı düşük), ERA5 (CDS anahtarı yok, POWER benzeri), AMS (NASS fiyatı
  yeterli; anahtar yok).
- CDL web sayfasının SSL zinciri bozuk; veri servisi (axis2) sağlam ama zaman zaman 60 sn+ yanıt vermiyor.

## NDVI kaynağı: Microsoft Planetary Computer (2026-11-15)
- ORNL'nin önündeki proxy her isteği 60. saniyede kesiyor (502); AppEEARS girişi 504. Bizim istek biçimimizden
  bağımsız (tek tarih / 10 tarih / bantsız / iki bant denendi).
- Planetary Computer aynı MOD13Q1 v061 ürününü (+ Aqua MYD13Q1) bulut-optimize GeoTIFF olarak sunuyor; yalnızca
  pikselin bloğu okunur. ORNL ile aynı tarihlerde değerler BİREBİR aynı (Iowa'da 35/36; 1 farkı muhtemelen yeniden
  işlenmiş dosya). Tarla başına 2 yıl ~20 sn, 8 yıl ~1 dk.
- Katalogda 2025-06-26..2025-11-17 arasında Terra ve Aqua sahnesi yok (2018-2024 ve 2026 tam). Bu yüzden veri 2018'e
  uzatıldı ve zamansal bölme değişti: eğitim 2018-2023, doğrulama 2024-01..06, test 2024-07+ (2025 yazı kısmen eksik).
  Veri sabit tarihlere uymazsa (örn. yalnızca 2024-2025) eğitim zamana göre %70/%15/%15 böler.
- Backend'de canlı parsel NDVI'si de önce Planetary'den (saniyeler), olmazsa ORNL'den.

## Bağımsız inceleme sonrası düzeltmeler (2026-11-15, ml/ml_feedback1.md)
- **Sızıntı:** kompozit değeri dönemin ortasına yazılıyordu ve özellik boşlukları doğrusal (gelecek gözlemi de
  kullanarak) dolduruluyordu. Artık değer dönemin SONUNA yazılıyor (`composite_anchor = "end"`), boşluklar
  yalnızca geçmişten dolduruluyor (`feature_fill = "ffill"`), hedef yalnızca gerçek gözlem. Ölçüm (72 tarla,
  yalnızca NDVI özellikleri, CPU): eski xgb 0.0851 / kalıcılık 0.1156; sızıntısız xgb 0.0866 / kalıcılık 0.1193.
  Kazanç %26 -> %27: sızıntının etkisi küçükmüş, modelin avantajı gerçek. `ndvi_lag5` sızıntısız sürümde de en
  önemli özellik (fenoloji / 16 günlük döngü), yani sızıntı belirtisi değil.
- **Doğrulama tam yıl:** eğitim 2018-2022, doğrulama 2023 (yaz dahil), test 2024-2025. Test aralık kapsaması
  0.76 -> 0.80 (hedef). Arayüz testte ölçülen kapsamayı yazar (`interval_coverage_test`).
- **İlçe bazlı dışarıda tutma:** `ia_cedar_01/02` gibi komşu tarlalar aynı katmanda (5 katman). Daha dürüst sayı:
  NDVI-only modelde xgb 0.094 / kalıcılık 0.119 (~%21 kazanç).
- Yerel saha NDVI'si artık eğitim hedefine karıştırılmıyor (`local_as_target = false`), yalnızca kalibrasyon.
- `analyze.py` su stresi referansı aynı mevsimin medyanı (service.py ile aynı, `risk.seasonal_reference`).
- `climatology()` gerçek ufku kullanıyor; monokültürün çeşitlilik puanı 1.0 -> 1/3.
- CDL özellikleri (`crop_prev1`, `crop_expected`) yalnızca önceki yılları kullanıyor: sızıntı yok (testli).

## Hava görünümü, küresel toprak, çiftçi ürünleri, aday üretimi (2026-11-15)
- **Hava görünümü (`model/outlook.py`):** tarla koordinatının 15 yıllık POWER geçmişi -> yılın günü klimatolojisi
  (±7 gün) + son 7/30 günün anomalisinden 1-4. hafta için doğrusal kalıcılık. Son 2 yıl geri test: beceri
  (1 - MAE_model/MAE_normal) ve aralık (ilk yarıda ayar, ikinci yarıda ölçülen kapsama). Polatlı'da sıcaklıkta
  beceri 1. haftada ~%2: haftalık anomali neredeyse kalıcı değil, yani görünüm pratikte **noktaya özgü mevsim
  normali + aralık**. Eminlik beceriden (0.45 + 0.5 x beceri), bu yüzden "düşük" çıkar; arayüz ve bot bunu söyler.
  Daha isabetli 1-2. hafta için gerçek sayısal tahmin (ör. Open-Meteo ECMWF/GFS) eklenebilir; yapılmadı.
- **Risk ileriye bakar:** don/sıcak bileşeni = max(son 4 hafta gözlemi, önümüzdeki 2 haftanın beklenen gün sayısı);
  beklenen yağış normalin altındaysa su stresine eklenir.
- **Yeni türetilmiş özellikler (sonraki eğitim için):** `vpd_kpa` (+4w, anomali), `dtr_c`, `water_balance_mm`
  (+4w, 12w). Mevcut model şemasında yok, canlı tahmini değiştirmez; budama eğitimde karar verir.
- **Küresel toprak/eğim (`data_load/soil_global.py`):** SSURGO yoksa SoilGrids 250 m COG (0-30 cm) + Saxton & Rawls
  (2006) ile AWC/Ksat/hidrolojik grup, Copernicus GLO-90 (Open-Meteo) 3x3 Horn eğimi. Kansas'ta SSURGO ile doku aynı
  (kumlu tın), Iowa'da 250 m pürüzsüzleştirme yüzünden farklı (killi tın vs tın); AWC SSURGO'dan ~%25-30 düşük.
  Tahmin olarak işaretlenir, rotasyon güven bonusu yarımdır. SoilGrids REST servisi kapalıydı; COG yolu çalışıyor.
- **Çiftçi ürünleri/rotasyonları:** istekte gelir (tarayıcıda saklanır); `build_catalog` taban kataloğu kopyalayıp
  ekler/üzerine yazar, `crops.toml` değişmez. Ekonomi (verim, fiyat, maliyet) yalnızca gösterim: brüt kâr.
- **Aday üretimi:** 2-3 sezon, art arda aynı aile yok, en fazla bir nadas (yalnızca kurak alanda), uygunluk < 0.35
  ürün elenir, kurak + sulamasız alanda örtü bitkili sürüm yok. Daha geniş arama sezgisel profillerin zayıflığını
  açığa çıkardı (Iowa'da sorgum/ayçiçeği mısırı geçiyor) -> öneriler farklı ürün kümesinden seçilir ve en iyi
  katalog rotasyonu her zaman ilk 3'te kalır. `crops.toml` değerleri yerel uzmanla gözden geçirilmeli.
