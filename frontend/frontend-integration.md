
1. Diğer modüller (risk, güven, rotasyon) nasıl çalışıyor?

Ayrıca bir şey yapmana gerek yok; hepsi backend'in içinde otomatik çalışıyor. Kullanıcı poligon gönderdiğinde backend sırayla şunları yapıyor:

poligon → hava (POWER) + NDVI (Planetary) + toprak (SSURGO) + ürün geçmişi (CDL)
        → XGBoost NDVI tahmini
        → risk skoru + güven skoru            (ml/model/risk.py)
        → rotasyon puanları + olası zararlar   (ml/model/rotation.py)
        → katmanlar → iki bot yorumluyor

Bunun için gereken üç şey bu bilgisayarda şu an hazır; az önce kontrol ettim:
1. backend/.env içinde ML_PROVIDER=xgboost ✅
2. ml/runs/LATEST yeni modeli gösteriyor ✅
3. Backend servisi çalışıyor (/api/health → ok: true) ✅

İleride yeni bir model eğitirsen: klasörü ml/runs/ altına koy, LATEST'i güncelle ve systemctl --user restart space-backend çalıştır.

2. Frontend gelince siteye entegrasyon

frontend/ klasörü şu an boş. Frontend'i yazan arkadaşın yalnızca backend/frontend-spec.md dosyasını takip etmesi yeterli; bütün API orada yazıyor. Frontend'in yapacakları kısaca:

1. Oturum aç: POST /api/session (tek kullanıcı + sıra sistemi var).
2. Analiz başlat: POST /api/analysis ile kullanıcının haritada çizdiği poligonu gönder. İsteğe bağlı olarak mevcut rotasyon ve öncelikler de gönderilebilir.
3. Sonuçları canlı izle: GET /api/jobs/{id}/events (SSE). Önce ilerleme olayları gelir ("hava verisi alındı", "uydu verisi alındı"), ardından katmanlar:
   - ndvi_tahmin, risk, iklim: sayılar ve eminlik rozeti
   - rotasyon_1..3, rotasyon_mevcut: details alanında nedenler, faydalar ve olası zararlar (verim kaybı aralıklarıyla)
   - İki botun yazdığı metin, kelime kelime akarak
4. Takip sorusu: POST /api/ask
5. Çiftçinin kendi rotasyonu: GET /api/rotati POST /api/rotations ile anında puanla.

Bağlantı ayarı. Backend 127.0.0.1:8000 adresirontend adresleri şu an localhost:5173 (Vite) localhost:3000.                                                                                                      Geliştirme sırasında: frontend aynı bilgisaite'ta /api için bir proxy tanımlamak; o zamanCORS ile hiç uğraşmazsınız:                                                                                            vite.config.jsrver: { proxy: { '/api': 'http://127.0.0.1:8000' } }                                                                  Başka bir bilgisayardan ya da internetten elnızca bu bilgisayara açık. ngrok ya daCloudflare Tunnel gibi bir tünel ancak o zaman gerekir. Jüriye ya da başka cihazlara demo göstereceksen bunu birlikte kurarız. Hangi adresin kullanılacağı belli  satırını da güncelleriz.
Frontend olmadan denemek için frontend-spec.mrl komutları var. Ben de az önce uçtan ucatesti bu API üzerinden yaptım.