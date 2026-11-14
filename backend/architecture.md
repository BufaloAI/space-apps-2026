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
- Aday modeller: Qwen2.5-7B-Instruct (AWQ), Qwen3-8B (tool calling'de iyi).
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
- Adres: `http://192.168.1.10:8000/v1` (yerel ağ IP'si, değişebilir). Model: `Qwen/Qwen2.5-7B-Instruct-AWQ`.
- Test: `curl http://<IP>:8000/v1/models -H "Authorization: Bearer <KEY>"`.
- Kurulum durumu: vLLM'in `~/vllm-env` içine kurulduğu (`uv pip install vllm`, `vllm --version`) henüz doğrulanmadı.

### Tuzaklar
- Sistem Python'u 3.14 (çok yeni, vLLM desteklemeyebilir) ve Arch'ta "externally managed". `--break-system-packages` kullanma; her zaman venv.
- `~/Masaüstü/vllm/.venv` eski/kullanılmayan bir ortam (3.14). vLLM için `~/vllm-env` kullanılır.
- Sunucu `0.0.0.0`'da dinler; yalnızca güvenilir ağda kullanılmalı, internete açılırsa HTTPS (reverse proxy) veya güvenlik duvarı gerekir.
- API key'i sohbete, repoya veya commit'e yazma. Bir kez sohbette paylaşıldıysa yenile.

## Açık sorular
- ML gerçekten bu makinede mi çalışacak? Backend ona nasıl erişecek (Python çağrısı, API, dosya)?
- ML hesabı ne kadar sürüyor (saniye mi, dakika mı)? Arka plan işi gerekir mi?
- ML sonucu poligon için tek bir özet mi, yoksa piksel/hücre bazlı harita mı?
- ML verisinin formatı ve boyutu (GeoJSON, raster, tablo?).
- Ajan modu: tartışma mı, kullanıcı seçimi mi?
- Web çerçevesi (FastAPI önerildi, karar verilmedi).
- Kesin model seçimi.
