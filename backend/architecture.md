# <sonnet>

## Genel amaç
- Backend, ML'den gelen harita/risk verisi ile vLLM'deki model arasında köprü olur.
- ML çıktısı: belirli bir alan için, farklı bitki ve rotasyonlara (örn. mısır → domates → ...) göre risk dağılımı ve eminlik (belirsizlik) değerleri. Format değişebilir.
- Model bu veriyi yorumlar ve kullanıcıyla tartışır.

## Uçtan uca akış (tarla → ML → yorum)
1. Kullanıcı haritada tarla sınırını poligon olarak çizer ve bir tarih girer.
2. Backend girdiyi doğrular (poligon kapalı mı, kendini kesmiyor mu, koordinat sistemi, tarih mantıklı mı). Doğrulama sadece backend'de yapılır, bozuk veri ML'e gitmez.
3. Backend poligon + tarihi ML'e gönderir. ML aynı makinede (backend PC) çalışacak, ama bu henüz kesin değil.
4. ML analizi çalışır; rotasyon senaryolarına göre risk dağılımı ve eminlik döner.
5. Backend sonucu işler: sıralama/karşılaştırmayı kendisi yapar, modelin okuyacağı küçük bir özet üretir.
6. Ajan(lar) özeti yorumlar ve kullanıcıyla tartışır. Ek sorularda tool'lar ML sonucundan ilgili parçayı verir.
7. Harita görselini backend (veya ML) doğrudan kullanıcıya iletir. Model haritayı görmez, sadece özeti okur.

## ML entegrasyonu
- ML bu backend makinesinde çalışacak (kesin değil). Bu makinede NVIDIA GPU yok, ML ağır işlerde yavaş kalabilir; vLLM zaten uzak GPU sunucuda olduğu için bu iki iş birbirinden bağımsız.
- Harita verisinin formatı belli değil. Bu yüzden backend ile ML arasında bir **adaptör katmanı** olur: ML'in çıktısı bu katmanda sabit bir iç formata çevrilir. Format değişirse yalnızca adaptör değişir; tool'lar ve ajanlar etkilenmez.
- ML hesabı uzun sürebilir. Bu durumda sohbetten ayrı bir arka plan işi olarak çalışır: backend işi başlatır, kullanıcıya ilerleme durumu gösterir, bitince ajan yorumlamaya geçer.
- ML çalışırken aktif oturum slotu dolu kalır ve idle timeout (5 dk) ML süresini saymaz.

## Sabit çizgiler
- vLLM kullanılır, model uzak GPU sunucuda çalışır. Backend API key ile OpenAI uyumlu API üzerinden bağlanır (`openai` istemcisi, anahtar `.env`'den).
- GPU: 16 GB VRAM, 6-7B parametreli tek model.
- Backend'e `vllm` paketi kurulmaz, sadece istemci kurulur.

## Model / vLLM ayarları (öneri)
- Nicemlenmiş model: AWQ/GPTQ 4-bit (~5 GB) veya FP8 (~8 GB). fp16 7B KV cache'e yer bırakmaz.
- **Seçilen model: `Qwen/Qwen3-8B-FP8`** (~9 GB, RTX 5060 Ti FP8'i donanımda destekler). Qwen2.5-7B-Instruct-AWQ ile denendi, Türkçe cevapları uydurma/karışıktı; Qwen3-8B belirgin şekilde daha iyi çıktı.
- Qwen3 varsayılan olarak "düşünme modu" açık gelir (gecikme ve token artar). Backend her istekte `chat_template_kwargs: {"enable_thinking": false}` göndermeli.
- Test sonucu: `hermes` tool-call parser'ı ile tool calling çalışıyor (model `get_risk_summary` tool'unu doğru argümanlarla çağırdı, `finish_reason: tool_calls`).
- Alternatifler (denenmedi): Qwen3.5-9B (16 GB'a nicemlenmiş sürümü gerekir, parser ayarı riskli), Granite 4.2 8B, Ministral 3 8B. Türkçe için güncel bir benchmark sıralaması bulunamadı.
- `--enable-prefix-caching`: her ajanın system promptu otomatik cache'lenir.
- `--enable-auto-tool-choice --tool-call-parser hermes`: tool calling için.
- `--gpu-memory-utilization 0.90`.
- `--max-model-len`: tek kullanıcı olduğu için 8k yerine ~16k denenebilir.

## Ajanlar
- **Karar verildi:** botlar birbirleriyle tartışmaz. İki (ayardan N) bağımsız bot, her biri kendi bakış açısıyla ve **kendi verisiyle** yorum yapar; kullanıcı ikisini yan yana görür, sonra botlara soru sorabilir. Bot sayısı, kimlikleri, prompt'ları ve göreceği katmanlar `backend/agents.toml`'da (kod değişmez). Bakış açıları henüz belli değil.
- Hepsi aynı modeli kullanır, tek fark system prompt ve verdiğimiz veri. Ayrı model kopyası veya elle KV cache yönetimi yok; prefix caching bunu otomatik yapar.
- Botlar paralel çağrılır (vLLM continuous batching): iki bot toplamda tek botun süresine yakın sürer.
- Önbellek başa göre eşleştiği için persona (system prompt) kısa ve başta tutulur; bot verisi kullanıcı mesajı olarak gelir.

## Tool tasarımı
- **Güncel durum:** şimdilik tool çağırma (function calling) kullanılmıyor. Backend her bota kendi veri özetini istemde verir (ML katmanları → `Layer.summary()`). Tool'lar ileride gerekirse (ürün/rotasyon bilgisi, karşılaştırma) eklenir; `hermes` parser'ı ile tool calling'in vLLM'de çalıştığı doğrulandı. Aşağıdaki ilkeler geçerli kalır.
- Sayısal işlemler (sıralama, karşılaştırma) backend'de yapılır, LLM'e bırakılmaz. Model sonucu yorumlar.
- Tool'lar küçük ve yapısal sonuç döner, ham harita verisi dönmez (bağlam bütçesi).
- Örnek tool'lar: `get_risk_summary(area, rotation)`, `compare_rotations(area, rotations)`, `get_uncertainty(area, rotation)`.
- Eminlik düşükse ajan bunu açıkça belirtir (system prompt kuralı).
- Tool çağrı döngüsüne üst sınır konur (örn. en fazla 5 tur).

## Eşzamanlılık: tek kullanıcı + sıra
- Aynı anda yalnızca 1 aktif kullanıcı oturumu olur.
- Slot doluyken gelen kullanıcılar FIFO sırada bekletilir. Sıra pozisyonu SSE/WebSocket ile bildirilir, slot boşalınca sıradaki otomatik alınır.
- Durum bellekte tutulur (Redis/Celery yok). Backend tek süreç (tek worker) çalışmalıdır.
- Idle timeout: 5 dakika. Kullanıcı kendisi bitirirse veya bağlantı koparsa slot hemen boşalır.
- Slot boşalınca sohbet geçmişi bellekten silinir.
- Sonradan çok kullanıcıya geçiş: tek slotlu kilidi N slotlu semaforla değiştirmek; çok worker gerekirse Redis.

## Ortam notları (bir daha uğraşmayalım)

### Backend makinesi (bu repo)
- Python ortamı repo kökünde ortak: `/home/nasko/Projeler/space-apps-2026/.venv`, Python 3.12 (`.python-version` ile sabit).
- Paket yönetimi `uv` ile (`uv add`, `uv run`). Şu an kurulu: `openai`, `python-dotenv`. Torch/ML paketleri kurulmadı, `ml/` için gerekince eklenir.
- `.env` içinde (git'e girmez) tutulacaklar: vLLM adresi ve API key. Önerilen isimler: `VLLM_BASE_URL`, `VLLM_API_KEY`.

### GPU sunucusu (Arch Linux, kullanıcı `nasko`)
- vLLM ortamı: `~/vllm-env` (Python 3.12.15, `uv venv --python 3.12` ile oluşturuldu).
- Her yeni terminalde önce: `source ~/vllm-env/bin/activate` (prompt'ta `(vllm-env)` görünmeli).
- Başlatma betiği: `~/Masaüstü/vllm/vllm_serve.sh` (repodaki kopya: `backend/scripts/vllm_serve.sh`).
- API key `~/.vllm_api_key` dosyasında; yoksa betik üretir. Yenilemek için dosyayı silip betiği tekrar çalıştırmak yeterli.
- Donanım: NVIDIA GeForce RTX 5060 Ti, 16 GB VRAM. Model: `Qwen/Qwen3-8B-FP8`.
- Adres: Tailscale üzerinden `100.96.165.43` (makine adı `archlinux`), port 8000. Yerel ağ IP'si `192.168.1.10` backend makinesinden erişilemez (farklı ağ).
- Backend doğrudan `http://100.96.165.43:8000/v1` adresine bağlanır (doğrulandı: key'siz `401`, key'li sohbet ~1.2 sn). SSH tüneline gerek yok.
- **Erişimi bozan 3 engel (ve çözümü):** (1) sunucuda `inet filter` `input` zinciri `policy drop` ve 8000'e izin yok; (2) Cloudflare WARP'ın nft tablosu `100.64.0.0/12` ve `100.96.0.0/12` kaynak/hedeflerini düşürüyor (backend'in Tailscale adresi `100.108.49.44` bu aralıkta); (3) WARP'ın yönlendirme kuralı (öncelik 5209) Tailscale'inkinden (5270) önce geldiği için sunucunun cevapları `CloudflareWARP` tüneline gidiyor. WARP "include" modunda ve `Allow Mode Switch: false` olduğundan Tailscale aralığı CLI'dan hariç tutulamıyor.
- **Çözüm:** `ip rule add to 100.64.0.0/10 lookup 52 priority 5200` (Tailscale adresleri önce Tailscale tablosuna baksın) + üç nft kuralı (yalnızca `tailscale0` arayüzünden, yalnızca port 8000). Bunları uygulayan idempotent betik: `backend/scripts/vllm-tailscale-fix.sh`, `systemd` zamanlayıcısı ile 30 sn'de bir çalışır (WARP yeniden bağlanınca nft tablosunu sıfırladığı için tek seferlik servis yetmez).
- Kurulum (sunucuda, sudo): `sudo install -m 755 vllm-tailscale-fix.sh /usr/local/bin/ && sudo install -m 644 vllm-tailscale-fix.{service,timer} /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now vllm-tailscale-fix.timer`.
- Tanı ipucu: bağlantı yine kopuk görünürse sunucuda `ip route get 100.108.49.44` çıktısı `dev tailscale0` olmalı; `CloudflareWARP` yazıyorsa 5200 numaralı kural kaybolmuştur. `sudo tcpdump -ni tailscale0 tcp port 8000` ile SYN gelip SYN-ACK gitmiyorsa da aynı sorun.
- Geri alma: `sudo ip rule del to 100.64.0.0/10 lookup 52 priority 5200` ve `sudo systemctl disable --now vllm-tailscale-fix.timer`.
- Betik modeli başlatmadan önce `hf download` ile indirir; ilk açılışta ~9 GB iner ve FlashInfer kernel derlemesi birkaç dakika sürebilir (sonra önbellekten hızlı açılır).
- Test: `curl http://<IP>:8000/v1/models -H "Authorization: Bearer <KEY>"`.
- **vLLM `systemd` servisi (`vllm.service`):** dosya `backend/scripts/vllm.service`, sunucuda `~/Masaüstü/vllm/vllm.service`. Kurulum (sunucuda, önce terminaldeki elle çalışan vLLM `Ctrl+C` ile durdurulmalı, aynı GPU ve portu tutuyor): `cd ~/Masaüstü/vllm && sudo install -m 644 vllm.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now vllm.service`. Günlük: `sudo journalctl -u vllm -f`; durum: `systemctl status vllm`; yeniden başlat/durdur: `sudo systemctl restart|stop vllm`. İlk açılış 2-3 dk sürer (port o zamana kadar kapalı). Serviste `HF_HUB_OFFLINE=1` var (model önbellekte, açılışta internet beklemez); başka bir model denenecekse önce elle (internetle) indirilmeli ya da bu satır kaldırılmalı. Betik key'i yalnızca terminalde basar, serviste günlüğe yazmaz (key `~/.vllm_api_key` içinde). Servisin yolları `/home/nasko/...` ve `~/Masaüstü/vllm` klasörüne sabittir; klasör taşınırsa dosya güncellenmeli.
- Kurulum durumu: vLLM 0.33.0 `~/vllm-env` içine kuruldu ve çalışıyor (doğrulandı: key'siz/yanlış key `401`, doğru key ile sohbet ve tool calling çalıştı).

### Tuzaklar
- Sistem Python'u 3.14 (çok yeni, vLLM desteklemeyebilir) ve Arch'ta "externally managed". `--break-system-packages` kullanma; her zaman venv.
- `~/Masaüstü/vllm/.venv` eski/kullanılmayan bir ortam (3.14). vLLM için `~/vllm-env` kullanılır.
- Sunucuda Cloudflare WARP açıkken Tailscale (`100.64.0.0/10`) ile çakışır: WARP kendi iç adres aralığını (`100.96.0.0/12`) kullanır. Yeni bir port açılacaksa aynı engeller (nft + yönlendirme) tekrar çıkar; `vllm-tailscale-fix.sh` içindeki `PORT`'u kullan.
- Sunucu `0.0.0.0`'da dinler; yalnızca güvenilir ağda kullanılmalı, internete açılırsa HTTPS (reverse proxy) veya güvenlik duvarı gerekir.
- API key'i sohbete, repoya veya commit'e yazma. Bir kez sohbette paylaşıldıysa yenile.

## Uygulama durumu (backend/app)
- Çerçeve: FastAPI + uvicorn (**tek worker**: oturum ve iş durumu bellekte). Dosyalar: `config.py` (ayarlar, `backend/.env`), `llm.py` (vLLM istemcisi, `enable_thinking: false`), `sessions.py` (tek slot + FIFO sıra + `Job`), `geo.py` (poligon doğrulama), `ml.py` (ML adaptörü), `agents.py` + `agents.toml` (botlar), `main.py` (API). Çalıştırma, kurulum, dosya yapısı: `backend/README.md`. **Frontend için API sözleşmesi: `backend/frontend-spec.md`** (uç noktaların asıl ayrıntısı orada; burada tekrar etmiyoruz).
- Testler: `uv run pytest` (80 test; sahte model ile). Gerçek vLLM ile de elle denendi: iki kullanıcı/sıra, poligon → ML → iki botun paralel yorumu (~5 sn), akış ortasında bağlantı koparıp kaldığı yerden devam, takip soruları (iki bota birden ~1.6 sn, tek bota ~1.1 sn).
- **Oturum:** `POST /api/session` (aktif veya sıra no), durum, sıra SSE'si, keepalive, `DELETE`/`POST .../leave` (sekme kapanırken `sendBeacon` için). Aktif olmayan oturum iş başlatamaz (409). 5 dk boşta kalan slot devredilir; iş çalışırken sayaç işlemez, iş bitince yeniden başlar. Sırada 30 sn görünmeyen düşer. Oturum bitince iş iptal edilir, geçmiş ve analiz silinir.
- **İş (job) modeli:** analiz ve takip sorusu arka plan görevidir (`POST /api/analysis`, `POST /api/ask` → `202 {job_id}`). Görev olaylarını bir günlüğe yazar (`Job.events`, her olayın numarası var); istemci `GET /api/jobs/{id}/events` SSE'sine bağlanır, koparsa `Last-Event-ID` / `?after=` ile kaldığı yerden devam eder, `GET /api/jobs/{id}` ile anlık durum (sayfa yenileme) alır. Aynı anda tek iş (meşgulken 409). Tasarım gerekçesi: gerçek ML uzun sürebilir ve mobil ağlar kopar; iş istemciden bağımsız sürmeli.
- **Parsel analizi:** poligon önce doğrulanır (`geo.py`: tek Polygon, kendini kesmemeli, delik yok, ≤1000 nokta, 0.01–5000 ha (`MAX_AREA_HA`), açık halka otomatik kapanır; hata `422` + Türkçe mesaj, ML'e/modele gidilmez). Sonra ML → katmanlar → botlar paralel. Her bot yalnızca kendi katmanlarını görür.
- **ML adaptörü (`ml.py`):** `MLProvider.analyze(parcel, date, *, options, progress) -> list[Layer]` (eski 2 argümanlı imza da çalışır). `Layer` = ad, etiket, birim, ortalama, min, max, eminlik (0–1) + `confidence_level` + isteğe bağlı `details` (frontend için, bota gitmez). `.env`'de `ML_PROVIDER=fake|xgboost`. **`XGBoostMLProvider`** repo kökündeki `ml/service.py`'yi thread'de çalıştırır: parsel merkezi için NASA POWER (~3 sn) + MODIS NDVI (ORNL, 30 sn–2 dk, kararsız) -> XGBoost NDVI tahmini + risk + rotasyon (zararlarıyla). Aynı gün aynı parsel önbellekten (<1 sn). NDVI düşerse iklimle devam eder, uyarı verir. Katmanlar: `ndvi_tahmin`, `risk`, `iklim`, `rotasyon_1..3`, `rotasyon_mevcut`, `rotasyon_secilen`. `POST /api/rotations` çiftçinin seçimini veri çekmeden yeniden puanlar; bot bir sonraki soruda güncel veriyi görür (`Session.pending_updates`). Model dosyası git'e girmez: `ml/runs/<zaman>/` kopyalanır, `ml/runs/LATEST` onu gösterir (ya da `ML_MODEL_DIR`). Testler `tests/conftest.py` ile sahte ML + `tests/agents_fake.toml` kullanır.
- **Botlar (`agents.toml`):** her bot `id`, `name`, `system_prompt`, `layers`. Kod değişmeden bot eklenir/çıkarılır; frontend bot listesini `GET /api/agents`'ten okur (prompt'lar dışarı verilmez). Şu an 2 örnek bot ve **yer tutucu prompt'lar**. Her prompta ortak "uydurma yapma, sayıyı yaz, bilmiyorsan söyle" kuralları otomatik eklenir. Botlar paralel çağrılır, birinin hatası diğerini etkilemez.
- **Takip soruları:** `POST /api/ask` `{message, agent?}`; `agent` yoksa tüm botlara, varsa tek bota. Her botun ayrı geçmişi vardır (`[veri mesajı, ilk yorum, soru, cevap, ...]`); `HISTORY_MAX_MESSAGES` aşılınca kırpılır ama ilk ikisi (veri + ilk yorum) hep korunur. Analizi başarısız olmuş botun veri mesajı yeniden kurulur. Yeni analiz önceki sohbeti siler. Analizden önce soru → 409 `no_analysis`.
- **Dersler:** (1) 7-8B model yüzde olarak verilen eminliği yanlış yorumladı ("%82 = kesin karar zor"); çözüm: hesabı backend yapar, eminliği yüksek/orta/düşük etiketine çevirir, model etikete dayanır. Aynı ilke diğer sayısal yorumlar için de geçerli: karşılaştırma/eşik kararlarını backend'de ver, modele sonucu yorumlat. (2) Takip sorusunda bot, verisinde olmayan ürün önerileri yaptı ("buğday veya çavdar"); ortak kural prompt'u yetmedi. Gerçek bot prompt'larında "yalnızca verilen katmanlara dayan, ürün/bilgi uydurma" vurgulanmalı ve gerekirse ürün bilgisi ayrı bir veri/tool olarak verilmeli.
- **Henüz yok:** gerçek ML, gerçek bot prompt'ları, analizi iptal etme ucu, ilerleme yüzdesi, kimlik doğrulama/rate limit, vLLM `systemd` servisinin kurulumu (dosyalar hazır, doğrulama bekliyor).

## Açık sorular
- **ML:** gerçekten bu makinede mi çalışacak, backend ona nasıl erişecek (Python çağrısı, API, dosya)? Hesap süresi (saniye/dakika)? Sonuç poligon için tek özet mi, piksel/hücre bazlı harita mı? Format ve boyut (GeoJSON, raster, tablo)? Şu an sahte sağlayıcı var; `Layer` biçimi sabit, gerçek ML buna çevrilecek.
- **Botların bakış açıları ve prompt'ları** ve hangi bota hangi katmanların verileceği (şu an yer tutucu: `yagis` / `toprak`+`nem`).
- Takip sorularında bot verisinde olmayan bilgi uyduruyor ("buğday veya çavdar" önerisi). Gerçek prompt'larla ölçülmeli; gerekirse ürün bilgisi ayrı veri/tool olarak verilmeli.
- Türkçe kalitesi gerçek ML verisi ve gerçek prompt'larla yeniden ölçülmeli (şu an yalnızca sahte veriyle denendi). Qwen3.5-9B / Granite 4.2 8B / Ministral 3 8B denenmedi.
- `vllm-tailscale-fix` zamanlayıcısı sunucuda çalışıyor (yönlendirme kuralı silinince 30 sn içinde geri geliyor, doğrudan bağlantı çalışıyor). **Henüz denenmeyenler:** sunucu yeniden başlatma sonrası ve WARP yeniden bağlanma (`warp-cli disconnect && warp-cli connect`) sonrası bağlantı.
- `vllm.service` dosyası hazır ve sözdizimi doğrulandı (`systemd-analyze verify`), ama sunucuya **kurulup çalıştığı henüz doğrulanmadı** (kurulum `sudo` ister, yukarıdaki komutlar). Kurulduktan sonra yeniden başlatmada kendiliğinden kalkıp kalkmadığı da denenmedi.
- API key bu sohbetlerde göründü ve **yenilenmeyecek**: karar verildi, key yaklaşık 2 gün geçerli (yarışma/demo süresi). Süre bitince ya da proje paylaşılırken yenilenmeli (`rm ~/.vllm_api_key`, servisi yeniden başlat, yeni key'i `backend/.env`'e yaz).
- Frontend framework ve harita kütüphanesi kararı (arkadaşı yazıyor), analiz ilerleme yüzdesi, analizi iptal ucu, kimlik doğrulama/rate limit.
