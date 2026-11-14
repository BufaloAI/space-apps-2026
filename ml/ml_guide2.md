Entegrasyon bitti: backend artık gerçek ML'i kullanıyor (backend/.env'de ML_PROVIDER=xgboost). Servis yeniden başlatıldı ve canlı vLLM ile uçtan uca denendi. 104 testin hepsi geçiyor, commit atılmadı. ngrok gerekmedi: model backend'in içinde, aynı süreçte çalışıyor.                                                                                                                   Bu PC'deki hız                                                                                                                   ┌──────────────────────────────────────┬────────────────────────────────────────────┐
│                 Adım                 │                    Süre                    │
├──────────────────────────────────────┼────────────────────────────────────────────┤
│ İklim verisi (POWER)                 │ ~3 sn                                      │
├──────────────────────────────────────┼────────────────────────────────────────────┤
│ Uydu NDVI (NASA ORNL)                │ 80–120 sn, servis yavaş ve sık 502 veriyor │
├──────────────────────────────────────┼────────────────────────────────────────────┤
│ Model tahmini (CPU)                  │ milisaniyeler, GPU gerekmiyor              │
├──────────────────────────────────────┼────────────────────────────────────────────┤
│ Aynı gün aynı parsel, ikinci kez     │ < 1 sn (önbellek)                          │
├──────────────────────────────────────┼────────────────────────────────────────────┤
│ Çiftçinin kendi rotasyonunu puanlama │ 0,01 sn                                    │
└──────────────────────────────────────┴────────────────────────────────────────────┘

Toplam analiz yaklaşık 2 dakika. Bu süreyi neredeyse tamamen NASA'nın servisi belirliyor.

Kararlarına göre yapılanlar

1. İstenen poligon: çizilen parselin merkezi için veri o anda çekiliyor. POWER son 3 yıl ve bu yılı, NDVI son 22 haftayı
   getiriyor.
2. Güncel veri: veri her zaman bugüne kadar çekiliyor.
   - MODIS NDVI yaklaşık 4 hafta gecikmeli yayınlanıyor. Bu yüzden tahmin son uydu ölçümünün 2 hafta sonrası için
     yapılıyor ve bu tarih bugünden önce kalabiliyor.
   - Sistem bunu kullanıcıya ve bota açıkça söylüyor: "uydu gecikmeli, bu bugüne yakın durumun tahminidir".
3. Önce öneri, sonra çiftçinin seçimi: analiz 3 rotasyon önerir, mevcut rotasyon girildiyse onunla karşılaştırır. Çiftçi
   sonra POST /api/rotations ile kendi rotasyonunu ve önceliklerini seçer; veri yeniden çekilmez. Rotasyon botu birsonraki soruda güncel seçimi görüyor.
4. Botlar:
   - a: "Risk ve İklim Analisti" (NDVI tahmini, risk, iklim).
   - b: "Rotasyon Danışmanı" (rotasyonlar). Listede olmayan ürün önermemesi kuralı prompt'ta; canlı testte uydurma ürün önermedi.
5. Model: diğer PC'de eğitilen ml/runs/<zaman>/ klasörü buraya kopyalanacak. Adımlar ml/README.md sonunda.
6. Risklerin zararları: her rotasyonda ağırdan hafife sıralı bir zarar listesi var.
   - Ürün bazlı verim kaybı aralıkları: kuraklık, sıcak, geç don, aşırı nem ve yetersiz GDD için. Örneğin Iowa'da mısır için tahmini %6–18.
   - Toprak zararları: erozyon, zararlı birikimi, azot açığı, organik madde kaybı.
   - Bunların hepsi genel tahmin olarak işaretli.

Bilmen gerekenler

- NDVI bazen hiç gelmiyor. İlk canlı denemede ORNL iki kez 502 verdi ve NDVI alınamadı. Sistem çökmedi: iklim verisiyle risk ve rotasyon hesapladı, uyarı verdi; iki bot da bunu doğru söyledi. Tekrar deneme sayısını 3'e çıkardım; ikinci denemede NDVI geldi. Earthdata hesabıyla HLS (30 m) daha güvenilir bir NDVI kaynağı olur, ama henüz yazılmadı.
- Şu anki model sadece deneme için. Elimdeki gerçek veriyle eğittiğim ml/runs/real_small 3 tarlanın NDVI'sına dayanıyor. Kalıcılık bazını geçiyor ama klimatoloji bazını geçemiyor (RMSE 0.118'e karşı 0.103). Eminlik "düşük" görünüyor. Daha çok tarlayla eğitilen GPU modeli gelince değiştirilmeli.
- Ürün profilleri tahmini değerler. Iowa testinde iki yanlışı yakalayıp düzelttim: nohut (nemli iklimde hastalık riski) ve nadas (yağışlı yerde anlamsız) öne çıkıyordu. Düzeltmeden sonra öneriler bölge için bilinen klasik rotasyonlar oldu. Başka bölgelerde de sonuçlara gözle bakmak lazım.

Değişen dosyalar

- Yeni:
  - ml/service.py: parsel servisi.
  - backend/tests/test_ml_xgb.py: gerçek ML yolunun testleri.
  - backend/tests/conftest.py ve backend/tests/agents_fake.toml: testler sahte ML ve eski botlarla sabit kalsın diye.
- Backend:
  - app/ml.py: XGBoostMLProvider ve yeniden puanlama.
  - app/main.py: yeni istek alanları, ilerleme olayları, /api/rotations ve /api/rotations/catalog.
  - sessions.py, agents.py, config.py, agents.toml, .env.example.
- ML:
  - download.py: ayarlanabilir zaman aşımı, kompozit tarihlerinin yerel hesaplanması.
  - rotation.py, risk.py, crops.toml: zararlar, nem ve nadas düzeltmeleri.
- Belgeler: frontend-spec.md'ye frontend için 5.5 rotasyon seçimi bölümü, ilerleme olayları ve yeni katman biçimleri eklendi. architecture.md ve ml/README.md da güncellendi.