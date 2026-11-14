Tek açık konu AppEEARS: NASA'nın AppEEARS giriş ucu dün geceden beri 504 veriyor; az önce tekrar denedim, hâlâ bozuk. Bu yüzden uydu verisinin gerçek CSV biçimi henüz görülmedi. Kod, AppEEARS'ın katman listesinden doğrulanan adlarla yazıldı. run_all diğer bilgisayarda tam indirmeden önce otomatik olarak küçük bir deneme yapıp biçimi kontrol edecek; biçim beklenenden farklıysa tam indirmeyi başlatmayacak.

Commit edilecekler

- Yeni:
  - ml/run_all.py (tek komut)
  - ml/data_load/appeears.py, fields.py, usda.py, static.py
  - Bunların testleri: ml/data_load/tests/test_appeears.py, test_fields.py, test_usda.py
- Değişen:
  - ml/service.py, ml/data_load/build.py, features.py, registry.py
  - ml/README.md, ml/DECISIONS.md, ml/tests/test_ml_pipeline.py
  - backend/app/ml.py, backend/tests/test_ml_xgb.py
  - .gitignore
- Silinen: repodaki boş .dodsrc.
- Git'e girmeyenler: ml/.env (API anahtarları) ve ml/cache/ (veri).

Diğer bilgisayarda yapılacaklar

1. git pull, ardından uv sync.
2. ml/.env dosyasını bu bilgisayardan elle kopyala.
3. O bilgisayarda da ~/.netrc olmalı. Daha önce verdiğim read -p ... >> ~/.netrc komutunu orada da çalıştır, sonra chmod 600 ~/.netrc.
4. Sırayla:
uv run python -m ml.run_all --check-only    # her şey hazır mı
uv run python -m ml.run_all --max-fields 5  # küçük deneme
uv run python -m ml.run_all                 # tam çalıştırma (saatler sürebilir; yarıda kalırsa aynı komut)
5. Sonuç ml/cache/run_all_report.md dosyasında. Bir sorun çıkarsa o dosyayı, AppEEARS biçim kontrolü başarısız olursa ml/cache/logs/appeears_smoke.log dosyasını gönder.

Bu bilgisayardaki backend için: toprak ve CDL ürün geçmişi eklemesinin canlıya geçmesi için systemctl --user restart space-backend gerekiyor. Servisi senin onayın olmadan yeniden başlatmadım; istersen ben yaparım ya da sen çalıştırabilirsin.