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
- 4 ajan (örn. tarımcı, sucul, ...). Hepsi aynı 7B modeli kullanır, tek fark system prompt.
- Ayrı model kopyası veya elle KV cache yönetimi yok. Prefix caching bunu otomatik yapar.
- Önbellek başa göre eşleştiği için: persona kısa ve başta, ML verisi tool ile istek anında çekilir. (Alternatif: ortak ML raporu başta, persona sonda; cache paylaşılır ama persona etkisi zayıflayabilir.)
- Ajan modu henüz kararlaştırılmadı: ajanlar birbirleriyle tartışıp ortak sonuç mu sunacak, yoksa kullanıcı ajan seçip ayrı ayrı mı konuşacak. Tartışma modu çok daha fazla LLM çağrısı demektir (router ile 1-2 ajan seçmek, tur sayısı ve `max_tokens` sınırı gerekir).
- Aynı turdaki ajan çağrıları paralel gidebilir (vLLM continuous batching).

## Tool tasarımı
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
- Kurulum durumu: vLLM 0.33.0 `~/vllm-env` içine kuruldu ve çalışıyor (doğrulandı: key'siz/yanlış key `401`, doğru key ile sohbet ve tool calling çalıştı).

### Tuzaklar
- Sistem Python'u 3.14 (çok yeni, vLLM desteklemeyebilir) ve Arch'ta "externally managed". `--break-system-packages` kullanma; her zaman venv.
- `~/Masaüstü/vllm/.venv` eski/kullanılmayan bir ortam (3.14). vLLM için `~/vllm-env` kullanılır.
- Sunucuda Cloudflare WARP açıkken Tailscale (`100.64.0.0/10`) ile çakışır: WARP kendi iç adres aralığını (`100.96.0.0/12`) kullanır. Yeni bir port açılacaksa aynı engeller (nft + yönlendirme) tekrar çıkar; `vllm-tailscale-fix.sh` içindeki `PORT`'u kullan.
- Sunucu `0.0.0.0`'da dinler; yalnızca güvenilir ağda kullanılmalı, internete açılırsa HTTPS (reverse proxy) veya güvenlik duvarı gerekir.
- API key'i sohbete, repoya veya commit'e yazma. Bir kez sohbette paylaşıldıysa yenile.

## Uygulama durumu (backend/app)
- Çerçeve: FastAPI + uvicorn (tek worker). Dosyalar: `config.py` (ayarlar, `backend/.env`), `llm.py` (vLLM istemcisi, `enable_thinking: false`), `sessions.py` (tek slot + FIFO sıra), `main.py` (API).
- Çalıştırma (repo kökünden): `uv run uvicorn app.main:app --app-dir backend --port 8001 --workers 1`. Önce `backend/.env.example` dosyasını `backend/.env` olarak kopyalayıp `VLLM_API_KEY` doldurulmalı; key yoksa uygulama açılışta hata verir.
- Testler: `uv run pytest` (17 test: oturum mantığı + API, sahte model ile). Gerçek vLLM ile de elle denendi: iki kullanıcı, sıra, SSE akışı, sohbet geçmişi, slotun devri çalışıyor.
- API: `POST /api/session` (aktif veya sıra no), `GET /api/session/{id}` (durum), `GET /api/session/{id}/events` (SSE, aktif/bitince kapanır), `POST /api/session/{id}/keepalive`, `DELETE /api/session/{id}`, `POST /api/chat` (SSE: `{"delta"}`, `{"done"}`, `{"error"}`), `GET /api/health`.
- Oturum kuralları (uygulandı): aktif olmayan oturum sohbet edemez (409); aktif kullanıcı 5 dk boşta kalırsa slot devredilir, cevap üretilirken sayaç işlemez; sıradaki 30 sn hiç görünmezse sıradan düşer; oturum bitince geçmiş silinir. Geçmiş son 20 mesajla sınırlı, mesaj 4000 karakterle sınırlı. Model hatası kullanıcıya ayrıntı sızdırmadan genel mesajla döner.
- Henüz yok: ajanlar/system prompt'lar (şu an tek genel prompt), ML adaptörü ve tool'lar, tarla poligonu + tarih girişi, kimlik doğrulama/rate limit.

## Açık sorular
- ML gerçekten bu makinede mi çalışacak? Backend ona nasıl erişecek (Python çağrısı, API, dosya)?
- ML hesabı ne kadar sürüyor (saniye mi, dakika mı)? Arka plan işi gerekir mi?
- ML sonucu poligon için tek bir özet mi, yoksa piksel/hücre bazlı harita mı?
- ML verisinin formatı ve boyutu (GeoJSON, raster, tablo?).
- Ajan modu: tartışma mı, kullanıcı seçimi mi?
- Web çerçevesi (FastAPI önerildi, karar verilmedi).
- `vllm-tailscale-fix` zamanlayıcısı sunucuya kuruldu ve doğrulandı: yönlendirme kuralı silinince 30 sn içinde geri geliyor, kopya oluşmuyor, doğrudan bağlantı çalışıyor. Henüz denenmeyenler: sunucu yeniden başlatma sonrası ve WARP yeniden bağlanma (`warp-cli disconnect && warp-cli connect`) sonrası bağlantı.
- Türkçe kalitesi gerçek ML verisi ve system prompt'larla yeniden ölçülmeli (şu an tek bir test sorusu var).
