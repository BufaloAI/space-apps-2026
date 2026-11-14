# Backend

Kullanıcı ile vLLM'deki model (Qwen3-8B) arasında köprü. Kullanıcı haritada parselini çizer, backend ML sonuçlarını iki ayrı sohbet botuna yorumlatır, kullanıcı ikisini yan yana görüp takip sorusu sorabilir.

- **Mimari, kararlar, sunucu notları:** [`architecture.md`](architecture.md) (önce bunu okuyun)
- **Frontend için API sözleşmesi:** [`frontend-spec.md`](frontend-spec.md)
- **Bot ayarları:** [`agents.toml`](agents.toml)

## Kurulum

Gereken: [uv](https://docs.astral.sh/uv/). Python ortamı repo kökünde ortaktır (`.venv`, Python 3.12).

```bash
uv sync                                   # repo kökünde: bağımlılıkları kurar
cp backend/.env.example backend/.env      # sonra backend/.env içine VLLM_API_KEY'i yazın
```

`backend/.env` git'e **girmez** (`.gitignore`). API key'i sohbete/commit'e yazmayın. Key GPU sunucusunda `~/.vllm_api_key` içindedir.

## Çalıştırma

```bash
# repo kökünden
uv run uvicorn app.main:app --app-dir backend --port 8000 --workers 1
```

- **`--workers 1` şart:** oturum ve iş durumu bellekte tutulur, birden fazla süreçte paylaşılmaz.
- Etkileşimli API şeması: http://localhost:8000/docs
- Sağlık kontrolü: `curl localhost:8000/api/health`
- Açılışta `VLLM_API_KEY` yoksa uygulama açık bir hata ile durur.

## Ayarlar (`backend/.env`)

Tüm değişkenler ve açıklamaları `backend/.env.example` içinde. Öne çıkanlar: `VLLM_BASE_URL` (Tailscale adresi), `VLLM_API_KEY`, `VLLM_MODEL`, `IDLE_TIMEOUT_SECONDS` (varsayılan 300), `MAX_AREA_HA`, `ML_FAKE_DELAY_SECONDS`, `CORS_ORIGINS`.

## Testler

```bash
uv run pytest            # 80 test, sahte model ile; GPU sunucusu gerekmez
```

## Yardımcı betikler (`backend/scripts/`)

| Betik | Ne işe yarar |
|---|---|
| `chat.py` | Terminalden Qwen ile sohbet (`sen:` / `qwen:`). Doğrudan çalıştırılabilir: `backend/scripts/chat.py`. `--sistem "..."` ile sistem promptu verilir |
| `vllm_serve.sh` | **GPU sunucusunda** vLLM'i API key ile başlatır (modeli indirir, key yoksa üretir) |
| `vllm.service` | **GPU sunucusunda** vLLM'i arka planda/açılışta çalıştıran `systemd` servisi (kurulum `architecture.md`'de) |
| `vllm-tailscale-fix.*` | **GPU sunucusunda** Tailscale erişimini WARP/nft engellerine karşı açık tutan `systemd` zamanlayıcısı (kurulum `architecture.md`'de) |

## Dosya yapısı

```
backend/
  app/
    main.py       API uçları, iş (job) başlatma, bot istemleri
    sessions.py   tek kullanıcılı oturum slotu, FIFO sıra, Job (kaldığı yerden devam eden olay günlüğü)
    geo.py        parsel poligonu doğrulama (GeoJSON, shapely)
    ml.py         ML adaptörü (şimdilik sahte sağlayıcı), Layer, eminlik etiketi
    agents.py     bot ayarını okur + ortak "uydurma yapma" kuralları
    llm.py        vLLM istemcisi (OpenAI uyumlu API)
    config.py     ortam değişkenleri
  agents.toml     botların adı, prompt'u, göreceği veriler (kod değişmeden güncellenir)
  tests/          pytest
  scripts/        yardımcı betikler
```

## Yeni bir ML sağlayıcısı eklemek

`app/ml.py` içinde `MLProvider` arayüzüne uyan bir sınıf yazın (`async analyze(parcel, date) -> list[Layer]`), `app/main.py` içindeki `lifespan`'de `app.state.ml_providers` listesine ekleyin. Katman adları `agents.toml`'daki `layers` ile eşleşmelidir.

## Bot eklemek / değiştirmek

`agents.toml` içine `[[agent]]` bloğu ekleyin (`id`, `name`, `system_prompt`, `layers`). Kod değişmez, backend'i yeniden başlatın. Ortak kurallar (sayıya dayan, uydurma, eminlik düşükse söyle) her prompta otomatik eklenir.
