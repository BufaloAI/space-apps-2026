# BUFALOAI kılavuzu: kurulum, çalıştırma, yayınlama, otomasyon

English version: [TUTORIAL.md](TUTORIAL.md)

Projeyi sıfırdan kurmak, **https://spaceapps.nasko.tech** adresinde yayınlamak, her yeniden başlatmadan sonra
kendiliğinden çalışır halde tutmak, güncellemek ve bir şey bozulunca düzeltmek için gereken her şey.

**İçindekiler**
1. [Parçalar nasıl birleşiyor](#1-parçalar-nasıl-birleşiyor)
2. [Hızlı başvuru](#2-hızlı-başvuru)
3. [Host kurulumu (site + API makinesi)](#3-host-kurulumu)
4. [GPU sunucusu kurulumu (sohbet modeli)](#4-gpu-sunucusu-kurulumu)
5. [Cloudflare ile yayınlama](#5-cloudflare-ile-yayınlama)
6. [Açılışta her şeyi başlatma](#6-açılışta-her-şeyi-başlatma)
7. [Günlük işler](#7-günlük-işler)
8. [Geliştirme (kodu değiştirmek)](#8-geliştirme)
9. [Siteyi kullanma](#9-siteyi-kullanma)
10. [Yeni model eğitip yayına alma](#10-yeni-model-eğitip-yayına-alma)
11. [Host'u başka makineye taşıma](#11-hostu-başka-makineye-taşıma)
12. [Sorun giderme](#12-sorun-giderme)

---

## 1. Parçalar nasıl birleşiyor

İki makine, beş parça:

```
                         ┌──────────────────────── HOST (Fedora laptop) ───────────────────────┐
 ziyaretçi ──https──► Cloudflare ──tünel──► space-tunnel ──► space-backend  :8000              │
 spaceapps.nasko.tech                      (cloudflared)     FastAPI: site + /api + ML          │
                         │                                    │  (NASA POWER, MODIS NDVI,       │
                         │                                    │   SoilGrids, XGBoost, rotasyon) │
                         │      space-watchdog (2 dk'da bir), space-bootcheck (açılıştan sonra) │
                         └────────────────────────────────────┼─────────────────────────────────┘
                                                              │ Tailscale (özel ağ)
                         ┌──────────── GPU SUNUCUSU (archlinux, RTX 5060 Ti) ─┼────────────────┐
                         │  vllm.service :8000  (Qwen3-8B-FP8, botların "beyni")                  │
                         │  tailscaled + vllm-tailscale-fix.timer (WARP'a rağmen Tailscale'i açık tutar) │
                         └────────────────────────────────────────────────────────────────────────┘
```

| Parça | Nerede | Ne | Servis |
|---|---|---|---|
| Site | host | React uygulaması (`bufaloai-site/`), `bufaloai-site/dist`'e derlenir, backend sunar | `space-backend` içinde |
| Backend = sohbet botu sarmalayıcı API | host | FastAPI (`backend/`): oturum/sıra, analiz işleri, iki danışman bot, iklim API'si | `space-backend` (kullanıcı) |
| ML | host, backend içinde | `ml/`: veri indirme, XGBoost NDVI tahmini, risk, hava görünümü, rotasyonlar | `space-backend` içinde |
| Tünel | host | `cloudflared` adlı tünel `bufaloai` → `spaceapps.nasko.tech` | `space-tunnel` (kullanıcı) |
| Dil modeli | GPU sunucusu | API key'li vLLM, Qwen3-8B-FP8 | `vllm` (sistem) |

Tarayıcı yalnızca host'la konuşur. Dil modelinin API key'i host'ta (`backend/.env`) ve GPU sunucusunda
(`~/.vllm_api_key`) kalır.

---

## 2. Hızlı başvuru

**Host**'ta:

```bash
bufaloai status          # ne çalışıyor, açılışta başlıyor mu, tam sağlık kontrolü
bufaloai check           # yalnızca kontrol (site, API, Tailscale, sohbet modeli, açık adres, NASA)
bufaloai logs backend    # günlükleri izle (backend | tunnel | watchdog)
bufaloai restart         # backend + tüneli yeniden başlat (biri siteyi kullanıyorsa reddeder; --force ekle)
bufaloai update          # git pull, kurulum, siteyi yeniden derle, yeniden başlat, kontrol
bufaloai stop | start    # her şeyi yayından al / geri aç
bufaloai enable | disable   # açılışta başlatma açık / kapalı
```

**GPU sunucusunda** (`~/Masaüstü/vllm` içinde):

```bash
./gpu-server.sh check    # GPU, model, servisler, Tailscale yolu
sudo journalctl -u vllm -f
```

Tarayıcıdan sağlık: <https://spaceapps.nasko.tech/api/health> → `{"ok":true,...}`

---

## 3. Host kurulumu

Host siteyi, API'yi ve ML'i çalıştırır. Bugün bu, `/home/nasko/Projeler/space-apps-2026` yolundaki Fedora
laptop. Herhangi bir Linux makine olabilir (bkz. [§11](#11-hostu-başka-makineye-taşıma)).

### 3.1 Araçlar

| Araç | Neden | Kurulum (Fedora) |
|---|---|---|
| git | kodu almak/güncellemek | `sudo dnf install git` |
| [uv](https://docs.astral.sh/uv/) | Python 3.12 + paketler (burada asla pip kullanmayın) | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Node.js 22.12+ | siteyi derlemek | `sudo dnf install nodejs` (`node -v` ile kontrol) |
| Tailscale | GPU sunucusuna özel bağlantı | `sudo dnf install tailscale` |
| cloudflared | açık tünel | sudo'suz: `curl -fL -o ~/.local/bin/cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 && chmod +x ~/.local/bin/cloudflared` |

### 3.2 Kod, gizli bilgiler, model

```bash
git clone <repo-adresi> ~/Projeler/space-apps-2026 && cd ~/Projeler/space-apps-2026
cp backend/.env.example backend/.env       # ya da gerçeğini eski host'tan kopyalayın (USB/scp)
```

`backend/.env` içindeki önemli satırlar:

```ini
VLLM_BASE_URL=http://100.96.165.43:8000/v1   # GPU sunucusunun Tailscale adresi
VLLM_API_KEY=sk-...                          # GPU sunucusundaki ~/.vllm_api_key. .env ASLA commit edilmez
VLLM_MODEL=Qwen/Qwen3-8B-FP8
ML_PROVIDER=xgboost                          # gerçek ML (fake = deneme verisi)
SITE_DIR=bufaloai-site/dist                  # backend derlenmiş siteyi sunar (her şey tek port)
```

Eğitilmiş model git'te yok. `ml/runs/LATEST`, içinde `model.ubj` olan klasörü göstermeli
(eğiten makineden `ml/runs/<run>/` kopyalanır, sonra `realpath ml/runs/<run> > ml/runs/LATEST`).

### 3.3 Ağ

```bash
sudo systemctl enable --now tailscaled     # enable = her açılışta da (önemli!)
sudo tailscale up                          # GPU sunucusuyla aynı tailnet'e giriş
tailscale status                           # "archlinux" listede olmalı
```

**IPv6 tuzağı:** bazı ağlarda IPv6 bağlanır ama TLS takılır; Python (ML) ve npm zaman aşımına düşer, `curl`
çalışır. Deneme: `curl -6 -m 10 https://power.larc.nasa.gov/` takılıyor ama `curl -4 …` çalışıyorsa bir kez:

```bash
echo 'precedence ::ffff:0:0/96  100' | sudo tee -a /etc/gai.conf
```

### 3.4 Kur, derle, her şeyi başlat

```bash
backend/scripts/setup-host.sh --check      # her ✗ satırı nasıl düzeltileceğini söyler
backend/scripts/setup-host.sh --tunnel     # uv sync, npm ci, npm run build, servisler, açık tünel, kontrol
sudo loginctl enable-linger $USER          # bir kez: servisler giriş yapmadan da açılışta başlar
```

`setup-host.sh`, `bufaloai install`'u çağırır: tüm servisleri yazar ([§6](#6-açılışta-her-şeyi-başlatma)) ve
`bufaloai` komutunu `~/.local/bin`'e koyar. `--tunnel` ile `~/.cloudflared/bufaloai.yml` varsa adlı tüneli
([§5](#5-cloudflare-ile-yayınlama)), yoksa geçici bir `trycloudflare.com` adresini kullanır.

Bitti: `bufaloai status` **All good.** ile bitmeli.

---

## 4. GPU sunucusu kurulumu

GPU sunucusu (Arch Linux, makine adı `archlinux`, Tailscale `100.96.165.43`, RTX 5060 Ti 16 GB) yalnızca dil
modelini çalıştırır. Dosyalar `~/Masaüstü/vllm` içinde.

### 4.1 İlk kurulum (archlinux'ta zaten yapıldı)

```bash
uv venv ~/vllm-env && uv pip install --python ~/vllm-env vllm        # vLLM'in kendi ortamı, repodan ayrı
~/vllm-env/bin/hf download Qwen/Qwen3-8B-FP8                         # ~9 GB, ~/.cache/huggingface'e
sudo pacman -S tailscale && sudo systemctl enable --now tailscaled && sudo tailscale up
```

API key ilk açılışta `~/.vllm_api_key` içinde oluşur; aynı değer host'taki `backend/.env`'e yazılır.

### 4.2 Betikleri kopyalama

Repodaki `backend/scripts/` içinden şunları sunucuda `~/Masaüstü/vllm/`'e kopyalayın (orada git pull, USB ya da
scp): `vllm.service`, `vllm_serve.sh`, `vllm-tailscale-fix.sh`, `vllm-tailscale-fix.service`,
`vllm-tailscale-fix.timer`, `gpu-server.sh`.

### 4.3 Açılışta başlatma

```bash
cd ~/Masaüstü/vllm
./gpu-server.sh install --no-sleep    # vllm, tailscaled, düzeltme zamanlayıcısını kurar + açar; uykuyu kapatır
./gpu-server.sh check                 # modelin yüklenmesi için 2-3 dk bekleyin, sonra hepsi ✓
```

Kurulanlar:

| Birim | Ne yapar | Açılışta |
|---|---|---|
| `tailscaled` | özel ağ | açık |
| `vllm.service` | `vllm_serve.sh` → `vllm serve Qwen/Qwen3-8B-FP8`, :8000, çevrimdışı (`HF_HUB_OFFLINE=1`), hata olursa sonsuza kadar yeniden dener | açık |
| `vllm-tailscale-fix.timer` | 30 sn'de bir, Cloudflare WARP'ın sildiği yönlendirme/güvenlik duvarı kurallarını geri koyar (ayrıntı `backend/architecture.md`) | açık |

`--no-sleep` uyku/hazırda bekletmeyi kapatır: uyuyan sunucu = cevap vermeyen botlar. Geri almak için:
`sudo systemctl unmask sleep.target suspend.target hibernate.target hybrid-sleep.target`.

---

## 5. Cloudflare ile yayınlama

Site bir **adlı tünel** ile yayınlanır (sabit adres, yeniden başlatmada değişmez). Mevcut kurulum:

| Öğe | Değer |
|---|---|
| Alan adı | `nasko.tech` (Cloudflare hesabı) |
| Tünel | `bufaloai`, id `23efdb43-3898-4ce2-bc91-f6cf6100c1e3` |
| Kimlik dosyası | `~/.cloudflared/23efdb43-3898-4ce2-bc91-f6cf6100c1e3.json` (gizli, paylaşmayın) |
| Ayar | `~/.cloudflared/bufaloai.yml` |
| DNS | CNAME `spaceapps` → `23efdb43-3898-4ce2-bc91-f6cf6100c1e3.cfargotunnel.com`, proxied |

Sıfırdan (ya da başka bir alan adı için) oluşturmak:

```bash
cloudflared tunnel login                         # tarayıcı: alan adını seçin (sertifika ~/.cloudflared/cert.pem)
cloudflared tunnel create bufaloai               # ~/.cloudflared/<ID>.json yazar
cloudflared tunnel route dns bufaloai spaceapps.nasko.tech
```

`route dns` `Authentication error` verirse CNAME'i Cloudflare panelinden elle ekleyin: DNS → Records → Add:
**CNAME**, ad `spaceapps`, hedef `<ID>.cfargotunnel.com` (tam olarak; `https://` yok, eğik çizgi yok), **Proxied**.

`~/.cloudflared/bufaloai.yml`:

```yaml
tunnel: <ID>
credentials-file: /home/nasko/.cloudflared/<ID>.json
ingress:
  - hostname: spaceapps.nasko.tech
    service: http://127.0.0.1:8000
  - service: http_status:404
```

Sonra `bufaloai install --named-tunnel ~/.cloudflared/bufaloai.yml` (ya da `setup-host.sh --tunnel`). Eski
`~/.cloudflared/config.yml` (silinmiş bir SSH tüneli) kullanılmaz.

Alan adı yoksa: `bufaloai install --quick-tunnel` rastgele bir `https://…trycloudflare.com` adresiyle yayınlar
(her yeniden başlatmada değişir; bulmak için `journalctl --user -u space-tunnel | grep trycloudflare`).

---

## 6. Açılışta her şeyi başlatma

Makine açıldıktan sonra elle hiçbir şey yapmak gerekmez. Sıra:

| Ne zaman | Host | GPU sunucusu |
|---|---|---|
| açılış | `tailscaled` (sistem) | `tailscaled`, `vllm.service` (model 2-3 dk'da yüklenir) |
| +20 sn | | `vllm-tailscale-fix.timer` ilk çalışma, sonra 30 sn'de bir |
| açılış (linger) | `space-backend` → site + API, :8000 | |
| backend'den sonra | `space-tunnel` → spaceapps.nasko.tech | |
| +90 sn | `space-bootcheck` → tam kontrol; sorun varsa masaüstü bildirimi | |
| +3 dk, sonra 2 dk'da bir | `space-watchdog` → backend ya da tünel cevap vermezse (art arda 2 başarısız kontrol) yeniden başlatır; sohbet modeli düşünce/gelince bildirir | |

Host birimleri `~/.config/systemd/user/` içindedir ve `bufaloai install` tarafından yazılır (elle düzenlemeyin;
`bufaloai` betiğini düzenleyip `bufaloai install`'u yeniden çalıştırın). Hepsi hata olursa sonsuza kadar
yeniden başlar.

Gerekenler (bir kez):

```bash
sudo systemctl enable --now tailscaled        # host: Tailscale açılışta
sudo loginctl enable-linger $USER             # host: kullanıcı servisleri girişsiz açılışta
```

Host bir laptopsa uyuyunca site düşer. Prize takılı tutun, otomatik uykuyu kapatın: GNOME Ayarlar → Güç →
Otomatik Askıya Alma **kapalı**; kapak kapanınca uyumasın diye:
`sudo sed -i 's/^#\?HandleLidSwitch=.*/HandleLidSwitch=ignore/' /etc/systemd/logind.conf` (sonra yeniden başlatın).

**Yeniden başlatma testi** (her değişiklikten sonra bir kez):
1. GPU sunucusunu yeniden başlatın, 3 dk bekleyin, `./gpu-server.sh check`.
2. Host'u yeniden başlatın, 2 dk giriş yapmayın, sonra <https://spaceapps.nasko.tech>'u telefondan açın.
3. Host'ta: `bufaloai status` → **All good.**; `journalctl --user -u space-bootcheck -b` açılış kontrolünü gösterir.

---

## 7. Günlük işler

| İş | Komut |
|---|---|
| Her şey yolunda mı? | `bufaloai status` |
| Günlükler | `bufaloai logs backend` · `bufaloai logs tunnel` · `bufaloai logs watchdog` |
| `git pull` / kod değişikliğinden sonra | `bufaloai update` (çeker, `uv sync`, gerekirse siteyi derler, yeniden başlatır, kontrol eder) |
| Yeni commit olmadan siteyi yeniden derle | `bufaloai update --rebuild` |
| `backend/.env` değişti | `bufaloai restart` |
| Siteyi yayından al | `bufaloai stop` (geri: `bufaloai start`) |
| Yayından al ve açılışta başlamasın | `bufaloai stop && bufaloai disable` |
| Sohbet modeli günlükleri | GPU sunucusunda: `sudo journalctl -u vllm -f` |
| Modeli yeniden başlat | GPU sunucusunda: `sudo systemctl restart vllm` |

`restart`, `update` ve `install`, biri aktif oturumdayken reddeder (oturumlar bellekte tutulur, kaybolurdu).
Yine de yapmak için `--force` ekleyin.

Site **aynı anda tek kullanıcıya** hizmet verir; diğerleri sırada bekler. Oturum 5 dk hareketsizlikte kapanır.
Giriş yok: linki olan herkes kullanabilir. Açılışta otomatik güncelleme bilerek **kapalı**: hatalı bir commit
canlı siteyi kendiliğinden düşürmesin.

---

## 8. Geliştirme

Canlı site çalışırken kodu anlık yenilemeyle geliştirin:

```bash
cd bufaloai-site && npm run dev          # http://127.0.0.1:5180, /api'yi :8000'deki backend'e yönlendirir
uv run pytest backend/tests ml/tests -q  # 130+ test, GPU ya da ağ gerekmez
```

| Klasör | Ne | Belgeler |
|---|---|---|
| `backend/app/` | FastAPI: `main.py` (API), `ml.py` (ML adaptörü), `climate.py`, `sessions.py`, `llm.py` | `backend/architecture.md`, API sözleşmesi `backend/frontend-spec.md` |
| `backend/agents.toml` | iki bot: adları, prompt'ları, hangi veriyi gördükleri | |
| `ml/` | veri yükleyiciler, model, risk, `outlook.py`, `rotation.py`, `crops.toml`, `crop_library.toml` | `ml/README.md`, `ml/DECISIONS.md` |
| `bufaloai-site/src/` | `api.ts`, `backend.tsx`, `Intelligence.tsx`, `Planner.tsx`, `FarmerTools.tsx`, `i18n.ts` | |

Kurallar: `uv`, asla `pip`; ML'de kullanıcıya giden her metin Türkçe ve İngilizce yazılır (`ml/model/i18n.py`);
frontend sayıları gösterir, karar backend/ML'indir. Değişikliği `git push` ile gönderin, host'ta `bufaloai update`.

---

## 9. Siteyi kullanma

1. **Dünya atlası / Tarlalarım:** parseli çizin (Çokgen ya da Dikdörtgen), ad verin, **Çiftçi bilgisi**ni
   doldurun (sulama, toprak dokusu, organik madde, eğim, mevcut rotasyon). Her bilinen değer eminliği artırır.
2. **İklim analizi, Tarımsal göstergeler, Veri gezgini:** seçili tarla için gerçek NASA POWER verisi ve
   **Önümüzdeki 4 hafta** (sıcaklık, yağış, donlu ve sıcak günler, mevsim normaliyle).
3. **Yapay zekâ:** tarlayı seçin → **Analiz et**. NDVI tahmini, risk, iklim ve hava görünümü kartları (eminlik
   rozetleriyle), yan yana yazan iki danışman, takip soruları, rotasyon önerileri (katalog / otomatik / sizin)
   ve tahmini zararlar, **Kendi rotasyonumu değerlendir**, bu tarlaya önerilen ürünler.
4. **Ürünler ve rotasyonlar:** ürünleriniz, ürün kütüphanesi (20 hazır ürün), analiz edilen tarlaya öneriler ve
   rotasyon oluşturucu. Tarayıcıda saklanır; sonraki analizde kullanılır.
5. Başlıktaki **TR / EN**: danışmanların cevapları dahil her şey. Analiz et'e basmadan önce seçin.

---

## 10. Yeni model eğitip yayına alma

GPU laptopta eğitin (veri ve NVIDIA GPU gerekir; bkz. `ml/README.md`):

```bash
uv run python -m ml.run_all --check-only     # GPU, Earthdata girişi, API anahtarları, veri
uv run python -m ml.run_all                  # tam çalıştırma; sonuç ml/runs/<zaman>/
```

Host'a almak: `ml/runs/<zaman>/` klasörünü host'un `ml/runs/` içine kopyalayın, sonra host'ta:

```bash
realpath ml/runs/<zaman> > ml/runs/LATEST
bufaloai restart
```

---

## 11. Host'u başka makineye taşıma

1. Yeni makinede: [§3](#3-host-kurulumu) (araçlar, repo, `backend/.env` ve `ml/runs/<run>/` kopyası, Tailscale).
2. Tünel dosyalarını kopyalayın: `~/.cloudflared/bufaloai.yml` ve `~/.cloudflared/<ID>.json` (kullanıcı adı
   farklıysa `.yml` içindeki yolu düzeltin).
3. **Önce eski host'ta:** `bufaloai stop && bufaloai disable` (aynı adda iki tünel ziyaretçileri iki makineye
   böler; iki backend de tek GPU'yu kullanırdı).
4. Yeni host'ta: `backend/scripts/setup-host.sh --tunnel`, sonra `sudo loginctl enable-linger $USER`.

---

## 12. Sorun giderme

`bufaloai check` (host) ve `./gpu-server.sh check` (GPU sunucusu) ile başlayın: her ✗ ne yapılacağını söyler.

| Belirti | Neden → çözüm |
|---|---|
| Botlar: "Model sunucusuna ulaşılamadı" | host'ta Tailscale kapalı (`sudo systemctl enable --now tailscaled`) ya da GPU sunucusu kapalı/uykuda/hâlâ yükleniyor (`./gpu-server.sh check`) |
| Tarayıcı: Cloudflare hata **1016** | DNS kaydı yanlış hedefi gösteriyor → CNAME `<tünel ID>.cfargotunnel.com` olmalı |
| Tarayıcı: hata **1033** / **530** | tünel çalışmıyor → `bufaloai logs tunnel`, `bufaloai restart` |
| Tarayıcı: **502 Bad Gateway** | tünel çalışıyor, backend çalışmıyor → `bufaloai logs backend` |
| DNS değişikliğinden sonra host'un kendisi siteyi bir süre açamıyor | eski yerel DNS önbelleği → `resolvectl flush-caches` |
| Yapay zekâ sayfası: "Sunucuya ulaşılamadı" | backend kapalı → `bufaloai status` |
| Analiz başarısız / iklim sayfası "Gösterim verisi" / günlükte `handshake operation timed out` | IPv6 tuzağı → [§3.3](#33-ağ) |
| `npm ci` takılıyor | Node için IPv6 tuzağı → `NODE_OPTIONS=--dns-result-order=ipv4first npm ci` (bufaloai update bunu yapar) |
| `Cannot find module @rollup/rollup-linux-x64-gnu` | `node_modules` Windows'tan kopyalanmış → `cd bufaloai-site && npm ci` |
| `bufaloai restart` biri siteyi kullanıyor diyor | bekleyin ya da `bufaloai restart --force` |
| "Önceki iş bitmeden yenisi başlatılamaz" | aynı anda tek iş; botların bitmesini bekleyin |
| İngilizce modda bir danışman Türkçe cevap veriyor | nadir; tekrar sorun |
| Analiz 1-2 dk sürüyor | bir tarlanın günün ilk analizi uydu verisini indirir; tekrarı anında |
| Ürün kataloğu alınamadı | `backend/.env`'de `ML_PROVIDER` `xgboost` değil ya da `ml/runs/LATEST`'te model yok |
| `cloudflared tunnel route dns` → Authentication error | eski sertifika DNS düzenleyemiyor → CNAME'i panelden ekleyin ([§5](#5-cloudflare-ile-yayınlama)) |

Daha derin arka plan: `backend/architecture.md` (sunucu ağı, WARP, vLLM ayarları), `ml/DECISIONS.md` (model ve
veri kararları), `backend/frontend-spec.md` (tüm API uçları).
