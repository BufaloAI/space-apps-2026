# Frontend'den beklentiler (API sözleşmesi)

Bu belge, frontend'i yazan kişi için. Backend'in ne sunduğunu, frontend'in neyi **göndereceğini**, neyi **çekeceğini** ve **neleri yapmaması gerektiğini** anlatır. Kaynak gerçek: `backend/app/main.py` (belge ile kod çelişirse kod doğrudur, bize haber verin).

## 1. Ürün özeti

Kullanıcı haritada **tarla parselini poligon olarak çizer** ve bir **tarih** seçer. Backend poligonu ML'e verir, sonuçları **iki ayrı sohbet botuna** yorumlatır. Kullanıcı **iki botun yorumunu yan yana** görür, sonra istediği soruyu botlara sorabilir.

- Backend adresi (geliştirme): `http://localhost:8000` (kendi makinemizde: `uv run uvicorn app.main:app --app-dir backend --port 8000 --workers 1`). Tüm uçlar `/api` altında.
- CORS izinli adresler `backend/.env` içindeki `CORS_ORIGINS` ile belirlenir (varsayılan `http://localhost:5173` ve `http://localhost:3000`). Başka bir adres kullanacaksanız bize söyleyin.
- Kimlik doğrulama yok. Kullanıcıyı tanıyan tek şey `session_id`'dir.
- **Aynı anda yalnızca 1 kullanıcı** kullanabilir. Diğerleri sırada bekler (aşağıda).

## 2. Genel akış

```
Sayfa açılır
   │  POST /api/session
   ▼
 status=queued ──(SSE: /api/session/{id}/events, "sıranız: N")──► status=active
   │                                                                   │
   │                                                    harita çiz + tarih seç
   │                                                                   │  POST /api/analysis  → job_id
   │                                                                   ▼
   │                                       SSE: /api/jobs/{job_id}/events
   │                          (ML başladı → ML bitti → iki bot yazıyor → bitti)
   │                                                                   │
   │                                                   POST /api/ask → job_id → aynı SSE
   ▼                                                                   ▼
 Çıkış: POST /api/session/{id}/leave (sekme kapanırken) veya DELETE /api/session/{id}
```

## 3. Frontend'in backend'e GÖNDERDİĞİ veriler

| Ne | Nereye | Biçim |
|---|---|---|
| Oturum isteği | `POST /api/session` | gövde yok |
| Parsel poligonu | `POST /api/analysis` → `polygon` | GeoJSON `Polygon` (veya `Polygon` içeren `Feature`). Koordinatlar **`[boylam, enlem]`** (WGS84; Leaflet'te `[lat, lng]` değil, **ters sıra**!) |
| Tarih (isteğe bağlı) | `POST /api/analysis` → `date` | `"YYYY-MM-DD"`; verilmezse bugün. Gerçek ML **her zaman bugüne kadarki güncel veriyi** kullanır, tarih yalnızca kayıt içindir |
| Rotasyon seçenekleri (isteğe bağlı) | `POST /api/analysis` ve `POST /api/rotations` | `existing_rotation`, `chosen_rotation`, `priorities`, `profile` (bkz. 5.5) |
| Takip sorusu | `POST /api/ask` → `message` | metin, 1–2000 karakter |
| Hedef bot (isteğe bağlı) | `POST /api/ask` → `agent` | bot `id`'si; verilmezse **tüm botlara** gider |
| Oturum kimliği | her istekte | gövdede `session_id` (POST) veya `?session_id=` (GET). Yol parametresi olan uçlarda yolda |
| Canlı tutma | `POST /api/session/{id}/keepalive` | gövde yok |
| Çıkış | `POST /api/session/{id}/leave` | gövde yok |

Başka hiçbir şey gönderilmez: model adı, prompt, ML ayarı, API key frontend'in işi **değildir**.

## 4. Frontend'in backend'den ÇEKTİĞİ veriler

| Ne | Nereden | İçerik |
|---|---|---|
| Oturum durumu | `POST /api/session`, `GET /api/session/{id}` | `status`, sıra no, kalan boşta süresi, mevcut iş ve analiz var mı |
| Sıra güncellemeleri | `GET /api/session/{id}/events` (SSE) | `queued` + `position` → `active` |
| Bot listesi | `GET /api/agents` | her bot: `id`, `name`, `layers` (hangi veriyi gördüğü) |
| ML katmanları | iş akışındaki `ml done` olayı | ad, etiket, birim, ortalama, min, max, eminlik (sayı + etiket) |
| Parsel bilgisi | iş akışındaki `ml started` olayı | alan (hektar), merkez noktası |
| Bot yorumları | iş akışı (SSE) | `delta` parçaları (token token), bot başına |
| Anlık durum | `GET /api/jobs/{job_id}` | sayfa yenilenince ekranı yeniden kurmak için tüm durum |
| Bölge iklim serisi | `GET /api/climate/daily` | seçili tarla merkezi için yıllık günlük NASA POWER verisi (5.7) |

## 5. Uç noktalar

Aşağıdaki örnekler gerçek biçimdedir. Hata durumları 7. bölümde.

### 5.1 Oturum

**`POST /api/session`** → `200`
```json
{ "session_id": "WqnOG2V1HqIkvmFF1fdzOA", "status": "active", "idle_remaining": 299.9, "job_id": null, "has_analysis": false }
```
Slot doluysa:
```json
{ "session_id": "ikNEi03Y-DEj4TUHzDzrcA", "status": "queued", "position": 1 }
```

**`GET /api/session/{session_id}`** → aynı biçim. Oturum bittiyse:
```json
{ "status": "ended", "reason": "idle" }
```
`reason`: `left` (kendisi çıktı), `idle` (5 dk hareketsiz), `queue_timeout` (sırada beklerken sayfa kapandı), `unknown`.
Hiç bilinmeyen oturum → `404`.

**`GET /api/session/{session_id}/events`** (SSE) — yalnızca **sıradayken** kullanın. Durum değişince olay gelir, aktif olunca veya oturum bitince **akış kapanır**:
```
data: {"status":"queued","position":2}

data: {"status":"queued","position":1}

data: {"status":"active","idle_remaining":300.0,"job_id":null,"has_analysis":false}
```
Bu akış aynı zamanda "sırada hâlâ buradayım" sinyalidir: **sıradaki kullanıcı bağlantıyı kapatıp 30 sn hiçbir istek atmazsa sıradan düşer.**

**`POST /api/session/{session_id}/keepalive`** → `200 {"ok":true}`; oturum aktif değilse `409`.

**`POST /api/session/{session_id}/leave`** veya **`DELETE /api/session/{session_id}`** → `200 {"ok":true}`; bilinmiyorsa `404`. İki uç aynı işi yapar; sekme kapanırken `navigator.sendBeacon` yalnızca POST atabildiği için `POST .../leave` var.

### 5.2 Botlar

**`GET /api/agents`** → `200`
```json
[
  { "id": "a", "name": "Risk ve İklim Analisti", "layers": ["ndvi_tahmin", "risk", "iklim"] },
  { "id": "b", "name": "Rotasyon Danışmanı", "layers": ["rotasyon_1", "rotasyon_2", "rotasyon_3", "rotasyon_mevcut", "rotasyon_secilen"] }
]
```
Bot sayısı, adları ve kimlikleri **ayardan** gelir ve değişebilir. Arayüzü "tam 2 sütun, şu adlarla" diye sabitlemeyin; listeyi bu uçtan okuyup dinamik kurun (şimdilik 2 bot var, ekranı 2 sütun düşünün ama kodu N'e dayanıklı yazın). `layers`, botun hangi veriyi gördüğünü kullanıcıya göstermek için kullanılabilir.

### 5.3 Parsel analizi

**`POST /api/analysis`** → `202`
```json
{
  "session_id": "WqnOG2V1HqIkvmFF1fdzOA",
  "date": "2026-05-01",
  "polygon": {
    "type": "Polygon",
    "coordinates": [[[29.0, 41.0], [29.01, 41.0], [29.01, 41.01], [29.0, 41.01], [29.0, 41.0]]]
  }
}
```
İsteğe bağlı alanlar (5.5'teki biçimde): `existing_rotation`, `chosen_rotation`, `priorities`, `profile`. Akış önerir: ilk analizde yalnızca poligon gönderilebilir; çiftçi sonra kendi seçimini `POST /api/rotations` ile değerlendirir.

Cevap: `{ "job_id": "lyugAUXz788" }`. Analiz **arka planda** başlar; hemen ardından aşağıdaki SSE'ye bağlanın.

**Süre (gerçek ML):** iklim verisi ~3 sn, uydu NDVI verisi NASA ORNL servisinden **30 sn – 2 dk** (servis yavaş, arada hata verip tekrar deneniyor). Aynı gün aynı parsel ikinci kez istenirse önbellekten **< 1 sn**. Bu sürede ilerleme olaylarını gösterin. NDVI alınamazsa analiz yine biter (NDVI katmanı olmaz, uyarı gelir).

Poligon kuralları (backend doğrular, hata `422` + Türkçe mesaj; frontend ayrıca ön kontrol yaparsa kullanıcı daha hızlı geri bildirim alır):
- Yalnızca **tek** `Polygon`. `MultiPolygon`, nokta, çizgi, **delikli** poligon kabul edilmez.
- En az 3 farklı nokta, en fazla 1000 nokta. Halka açık çizilmişse backend kendisi kapatır.
- **Kendini kesmemeli** (kelebek biçimi olmaz).
- Alan **0.01 – 5000 hektar**.
- Boylam −180..180, enlem −90..90.

### 5.4 Takip sorusu

**`POST /api/ask`** → `202` (önce bir analiz tamamlanmış olmalı)
```json
{ "session_id": "…", "message": "Bu parsel için sizce ne ekmek mantıklı?" }
```
Yalnızca bir bota sormak için `"agent": "a"` ekleyin. Cevap: `{ "job_id": "qEOwT4INdj4" }`; sonucu aynı SSE ile izleyin. **Yeni bir analiz başlatınca önceki sohbet ve önceki iş silinir.**

### 5.5 Rotasyon seçimi (çiftçinin kendi tercihi)

Önce sistem önerir (analiz sonucu `rotasyon_1..3`), sonra çiftçi isterse kendi rotasyonunu/önceliklerini seçer.

**`GET /api/rotations/catalog`** → form için ürünler, hazır rotasyonlar, öncelikler:
```json
{ "crops": [{"id":"corn","name":"Mısır","family":"poaceae","season":"warm","cover_crop":false}, …],
  "rotations": [{"id":"corn_soy","name":"Mısır → Soya","sequence":["corn","soybean"]}, …],
  "priorities": [{"id":"water","label":"su tasarrufu","default":1.0}, …],
  "rotation_syntax": "ürünler '>' ile sıralanır, örtü bitkisi '+' ile eklenir: corn+cereal_rye>soybean" }
```
Her ürün `profile` (0–1 alanlar, `gdd_need`, `n_fix`) taşır; `crop_fields` formdaki alanları listeler (`floats_0_1`, `seasons`, `economics`, `families`). Çiftçi bir katalog ürününü kopyalayıp düzenleyebilir. (`ML_PROVIDER=fake` iken `503`.)

**`POST /api/rotations`** → `200` (anında, LLM çağırmaz; önce analiz gerekli, yoksa `409 no_analysis`)
```json
{ "session_id": "…",
  "existing_rotation": "corn>soybean",
  "chosen_rotation": "corn+cereal_rye>soybean>winter_wheat",
  "priorities": { "water": 2, "soil_health": 1.5, "income": 1, "erosion": 1, "forage": 0.3 },
  "profile": { "irrigation": false, "slope_pct": 3, "soil_om_pct": 3.2, "soil_texture": "silt_loam" } }
```
- **Çiftçinin ürünleri ve rotasyonları (2026-11-15):** `custom_crops` (en fazla 30) ve `custom_rotations` (en fazla 30) hem `POST /api/analysis` hem `POST /api/rotations`'ta gönderilebilir. Frontend bunları tarayıcıda saklar ve **her istekte ikisini birlikte** yollar (silinen ürünü kullanan rotasyonu göndermez; bilinmeyen ürün `422`).
  ```json
  "custom_crops": [{ "id": "my_mercimek", "name": "Kırmızı mercimek", "family": "fabaceae", "season": "cool",
                     "water_need": 0.3, "drought_tol": 0.7, "heat_tol": 0.4, "frost_tol": 0.6, "wet_tol": 0.6,
                     "n_need": 0.1, "residue_cover": 0.3, "om": 0.3, "market": 0.75, "forage": 0.3,
                     "gdd_need": 0, "n_fix": true, "cover_crop": false,
                     "yield_t_ha": 1.6, "price_per_t": 32000, "cost_per_ha": 21000 }],
  "custom_rotations": [{ "name": "Buğday → Mercimek", "seq": "winter_wheat>my_mercimek" }]
  ```
  `id` `^[a-z0-9_]+$`; katalogdaki bir kimlikle gönderilirse o ürünün değerleri yerine çiftçininki kullanılır. 0–1 alanlar `crops.toml` ile aynı anlamdadır (verilmeyen 0.5). Ekonomi alanları isteğe bağlıdır; bir rotasyonun tüm ana ürünlerinde varsa `details.economics` gelir (sezon başına brüt kâr, tahmini verim kaybı düşülmüş).
- Hepsi isteğe bağlı; verilmeyen alan önceki değerini korur. Rotasyon: katalogdaki `rotations[].id` **ya da** ürün id'leri `>` ile (örtü bitkisi `+` ile). Öncelik anahtarları: `water, soil_health, income, erosion, forage`, ağırlık 0–5.
- Cevap: `{"layers":[…yeni rotasyon_* katmanları…], "options":{…}}`. Ekrandaki rotasyon katmanlarını bunlarla **değiştirin**.
- Bilinmeyen ürün → `422` + Türkçe metin (`"Geçersiz rotasyon girdisi: …"`).
- Rotasyon botu, **bir sonraki takip sorusunda** güncel seçimi görür (ayrı bir istek gerekmez).

### 5.6 İş akışı (SSE) ve anlık durum

**`GET /api/jobs/{job_id}/events?session_id=…`** (SSE, `EventSource` ile kullanılabilir — GET'tir)

Her olayın bir **`id`**'si vardır (0'dan başlar, kesintisiz artar). Bağlantı koparsa `EventSource` kendiliğinden `Last-Event-ID` göndererek **kaldığı yerden devam eder**. `fetch` ile okuyorsanız `?after=<son aldığınız id>` verin. İş bitince akış **kapanır**. Boşta kalırsa `: ping` yorum satırları gelir (yok sayın).

Olay türleri (alanlara göre ayırt edin):

| Olay | Ne zaman | Biçim |
|---|---|---|
| ML başladı | akışın ilk olayı | `{"stage":"ml","status":"started","date":"2026-05-01","parcel":{"area_ha":92.86,"centroid":[29.005,41.005]}}` |
| ML ilerleme | gerçek ML'de birkaç kez | `{"stage":"ml","status":"progress","progress":{"stage":"data","source":"ndvi","status":"started"}}`. `progress.stage`: `data` (`source`: `weather` / `ndvi`; `status`: `started` / `done` / `failed` / `cache`), `features`, `model`, `rotations` |
| ML bitti | ML sonuçları hazır | `{"stage":"ml","status":"done","errors":[],"layers":[ … ]}` (katman biçimi aşağıda) |
| Botlar | botlar yazmaya başlamadan | `{"stage":"agents","agents":[{"id":"a","name":"Risk ve İklim Analisti","layers":["ndvi_tahmin","risk","iklim"]}, …]}` |
| Takip sorusu | yalnızca `ask` işlerinde ilk olay | `{"stage":"ask","message":"…","agents":[…]}` |
| Bot yazıyor | çok sayıda | `{"agent":"a","delta":"Veriler, 30 günlük "}` |
| Bot bitti | bot başına bir kez | `{"agent":"a","done":true}` |
| Bot hatası | yalnızca o bot için | `{"agent":"b","error":"Model sunucusuna ulaşılamadı."}` — **diğer bot etkilenmez** |
| İş bitti | akışın son olayı | `{"done":true}` (`agent` alanı **yok**) |
| İş hatası | iş tamamen başarısız | `{"error":"Analiz sonuç üretemedi, lütfen tekrar deneyin."}` (`agent` alanı **yok**) |

`"agent"` alanı olan olaylar bota, olmayanlar işin kendisine aittir. Botlar **aynı anda** yazar; olaylar iç içe gelir, `agent`'a göre ayrı sütunlara ekleyin.

**Katman (`layers[]`) biçimi:**
```json
{
  "name": "yagis",
  "label": "Yağış (30 günlük toplam)",
  "unit": "mm",
  "mean": 31.6, "min": 14.9, "max": 48.2,
  "confidence": 0.82,
  "confidence_level": "yüksek",
  "note": "(sahte veri)"
}
```
- `confidence` 0–1 arası sayı. **`confidence_level`** (`yüksek` ≥ 0.8, `orta` ≥ 0.6, `düşük`) backend'den gelir; **frontend eşik hesaplamasın**, bunu gösterin (rozet/renk).
- `details` (isteğe bağlı, nesne): katmanın yapısal ayrıntısı; ekranda zengin gösterim için. Bota gitmez. Sahte ML'de yoktur.
- `min == max` ise aralık yoktur (tek değer); "aralık" göstermeyin.
- Gerçek ML katmanları (`ML_PROVIDER=xgboost`):

| `name` | Ne | `mean` / `min`–`max` | `details` |
|---|---|---|---|
| `ndvi_tahmin` | ~2 hafta sonraki bitki örtüsü (NDVI 0–1) | p50 / %80 aralığı (p10–p90) | `forecast` (`target_week`, `ndvi_last_observed`, `is_nowcast`, `top_features`…) ve **`_meta`** (aşağıda) |
| `risk` | genel risk 0–100 | tek değer | `risk.components`: `vegetation_stress`, `water_stress`, `heat_stress`, `frost_risk` → `{score, level}` |
| `iklim` | yaz (mayıs–eylül) yağışı, mm | ort. / en kurak–en yağışlı yaz | `area_conditions` (GDD, sıcak/don günleri, su stresi…) |
| `rotasyon_1..3` | önerilen rotasyonlar, puan 0–100 | tek değer | aşağıdaki rotasyon nesnesi |
| `rotasyon_mevcut` | çiftçinin mevcut rotasyonu (girdiyse) | tek değer | aynı |
| `rotasyon_secilen` | çiftçinin değerlendirmek istediği (girdiyse) | tek değer | aynı |
| `rotasyon_ozel_1..5` | çiftçinin oluşturduğu rotasyonlar (`custom_rotations`), `details.rank` = tüm adaylar içindeki sırası | tek değer | aynı |
| `hava_tahmini` | önümüzdeki 2 haftanın ortalama sıcaklığı, °C | p50 / %10–%90 | **`outlook`**: 4 hafta × (`t2m`, `tmax`, `tmin`, `precip`, `gdd`, `frost_days`, `heat_days`) → `{p10,p50,p90,normal}`, `skill`, `interval_coverage`, `history_years`, `issued_from` |
| `yagis_tahmini` | önümüzdeki 2 haftanın toplam yağışı, mm | p50 / %10–%90 | `outlook_ref` (ayrıntı `hava_tahmini`'nde) |

- **`_meta`** (ilk katmanın `details._meta`'sı): `warnings` (kullanıcıya gösterin: uydu gecikmesi, eksik veri), `data_as_of` (`weather`, `ndvi` tarihleri), `data_sources`, `information_gaps`, `disclaimer` ("nihai karar çiftçiye aittir"; **ekranda gösterin**).
- **Rotasyon adayları:** 13 sabit katalog rotasyonuna ek olarak ML ürün kurallarından 2–3 sezonluk adaylar üretir (`details.source`: `katalog` | `otomatik` | `çiftçi`). Öneriler farklı ürün kümelerinden seçilir ve en iyi katalog rotasyonu her zaman ilk 3'te yer alır. Otomatik adayları ekranda belirtin.
- **Toprak/eğim tahmini:** SSURGO yoksa (ABD dışı ya da servis hatası) SoilGrids + pedotransfer + yükseklik modelinden hesaplanır; `_meta.field_info.soil.estimated = true` ve `_meta.warnings`'te "hesaplandı" uyarısı gelir.
- **Rotasyon `details`**: `source`, `rank`, `economics` (varsa), `name`, `sequence_names` (örn. `["Mısır + Çavdar (örtü bitkisi)", "Soya"]`), `score`, `risk` (0–1), `why[]`, `benefits[]`, **`damages[]`**, `expected_yield_loss_pct` (ürün → `[alt, üst]` %), `risks_for_local_review[]`, `delta_vs_existing` (`score`, `risk` farkı; mevcut rotasyon girildiyse).
- **`damages[]`** (risklerin olası zararları, ağırdan hafife sıralı): `{"risk":"kuraklık / su stresi","affects":"Mısır","severity":"yüksek|orta|düşük","yield_loss_pct":[6.0,18.0] veya null,"impact":"…açıklama…"}`. Verim kaybı aralıkları **genel tahmindir**; ekranda "tahmini" diye belirtin.
- `errors`: bir ML bileşeni çökmüşse mesajları (diğer katmanlar yine gelir). Boş liste = sorun yok. Hiç katman yoksa iş hata ile biter.

**`GET /api/jobs/{job_id}?session_id=…`** → `200` anlık durum (sayfa yenileme / yeniden bağlanma için)
```json
{
  "job_id": "lyugAUXz788", "kind": "analysis", "status": "done",
  "last_event_id": 259,
  "parcel": { "area_ha": 92.86, "centroid": [29.005, 41.005] }, "date": "2026-05-01",
  "layers": [ … ], "ml_errors": [],
  "question": null,
  "agents": [
    { "id": "a", "name": "Yağış Analisti", "layers": ["yagis"], "text": "Veriler, 30 günlük …", "status": "done" },
    { "id": "b", "name": "Toprak ve Nem Analisti", "layers": ["toprak","nem"], "text": "…", "status": "error", "error": "Model sunucusuna ulaşılamadı." }
  ],
  "error": null
}
```
- `kind`: `analysis` veya `ask` (ask'ta `question` dolu, `parcel/date/layers` boş).
- `status`: `running` | `done` | `error` | `cancelled`. Bot `status`: `running` | `done` | `error`.
- **Yeniden bağlanma tarifi:** `GET /api/session/{id}` → `job_id` varsa `GET /api/jobs/{job_id}` ile ekranı kur → `status` `running` ise SSE'ye `?after=<last_event_id>` ile bağlan.

### 5.7 Bölge iklim serisi (NASA POWER, önbellekli)

**`GET /api/climate/daily?lat=41.01&lon=29.02&year=2025`** → `200`. **Oturum gerektirmez** (LLM/ML slotunu kullanmaz); "İklim analizi / Tarımsal göstergeler / Veri gezgini" sayfaları için.
```json
{ "source": "NASA POWER (günlük, AG topluluğu)", "lat": 41.0, "lon": 29.0, "year": 2025,
  "fetched_on": "2026-11-15", "complete": true, "last_date": "2025-12-31", "cached": true,
  "units": {"temperature":"°C","rainfall":"mm/gün","humidity":"%","solar":"MJ/m²/gün","moisture":"0-1 (yüzey toprak ıslaklığı)"},
  "days": [{"date":"2025-01-01","temperature":6.1,"rainfall":0.4,"humidity":81.2,"solar":5.3,"moisture":0.71}, …] }
```
- Nokta 0.1° ızgaraya yuvarlanır (POWER çözünürlüğü ~0.5°); yakın tarlalar aynı önbelleği paylaşır. Önbellek `ml/cache/climate/` (`CLIMATE_CACHE_DIR`).
- Geçmiş yıllar (`complete: true`) kalıcı önbellekte; içinde bulunulan yıl günde bir yenilenir (POWER birkaç gün gecikmeli). Kaynak düşerse eski kopya `"stale": true` ile döner.
- Eksik gün değeri `null`'dur (POWER `-999`).
- Hata: `422` (yıl 1981–bu yıl dışında, koordinat aralık dışı), `502` (POWER'a ulaşılamadı, Türkçe metin).
- Frontend tamamlanmış yılları `localStorage`'da da tutar (sohbet verisi değil, kamuya açık iklim verisi).

### 5.8 Hava görünümü (oturumsuz)

**`GET /api/climate/outlook?lat=39.58&lon=32.15`** → `200`, `hava_tahmini.details.outlook` ile aynı biçim (+ `confidence` değişken başına, `lat`, `lon`, `cached`). Noktanın 15 yıllık NASA POWER geçmişinden mevsim normali + kalıcılık modeli; son 2 yılın geri testinden beceri (`skill`, 0 = mevsim normalinden iyi değil) ve aralık. Aynı gün aynı 0.1° hücre önbellekten. `502` = POWER'a ulaşılamadı ya da 5 yıldan az geçmiş.

### 5.9 Dil, ürün kütüphanesi ve ürün önerileri (2026-11-15)

- **Dil:** `POST /api/analysis` ve `POST /api/ask` gövdesinde `"lang": "tr" | "en"` (varsayılan `tr`). `en` ise botlar İngilizce veriyle beslenir ve İngilizce cevap verir. Katman/ML içeriği **her zaman iki dillidir**: Türkçe alanların yanında `*_en` alanları gelir — katman `details.label_en`, `details.note_en`; `_meta.warnings_en`, `information_gaps_en`, `disclaimer_en`; rotasyon `name_en`, `sequence_names_en`, `why_en`, `benefits_en`, `risks_for_local_review_en`, `damages[].risk_en/affects_en/impact_en` (+ `kind`); `economics.note_en`. Bot listesi `name_en`; katalogda ürün/rotasyon `name_en`, öncelik `label_en`. Frontend İngilizce modda `*_en`, yoksa Türkçesini gösterir. Backend hata metinleri Türkçe kalır (frontend sabit metinleri çevirir).
- **Ürün kütüphanesi:** `GET /api/rotations/catalog` → `library[]` (20 hazır ürün, `ml/model/crop_library.toml`): `id, name, name_en, family, season, cover_crop, note, note_en, profile`. Varsayılan adaylara girmez; çiftçi ekleyince `custom_crops` ile gönderilir (kimliği korunur).
- **`GET /api/crops/suggestions?session_id=…&exclude=a,b`** → `200` (önce analiz gerekli, yoksa `409 no_analysis`): son analizin iklimine uygun (`fit` ≥ 0.5), çiftçinin listesinde olmayan ürünler, uygunluğa göre sıralı, en fazla 8: `{id, name, name_en, from: "library"|"catalog", fit, family, season, expected_yield_loss_pct, main_risk(_en), concerns(_en)[], note(_en)}`. Sezgisel; ölçüm değildir.
- **Site sunumu:** `SITE_DIR` ayarlıysa backend derlenmiş siteyi de sunar (`/`, `/intro`, `/app`); `/api` önce eşleşir.

## 6. Oturum yaşam döngüsü (frontend'in sorumlulukları)

- **`session_id`'yi saklayın** (bellekte veya `sessionStorage`; sayfa yenilenince lazım). Sohbet geçmişini/sonuçları **tarayıcıda kalıcı saklamayın** (backend zaten tutuyor, oturum bitince siliniyor).
- **Hareketsizlik süresi 5 dakika.** `idle_remaining` (saniye) `GET /api/session/{id}` ile okunur. Kullanıcı bir şey yaptıkça (çizim, yazma) `keepalive` atın; süre 60 sn'nin altına inince kullanıcıyı uyarıp "devam et" düğmesi (→ keepalive) gösterin. Süre dolarsa oturum `ended` (`idle`) olur: net bir mesaj ve "yeniden bağlan" (yeni `POST /api/session`) sunun.
- **İş çalışırken (analiz/soru) süre işlemez** (`idle_remaining` `null` gelir); iş bitince sayaç yeniden başlar.
- **Sekme kapanırken** ``navigator.sendBeacon(`/api/session/${id}/leave`)`` çağırın; yoksa slot 5 dk boş yere kilitli kalır ve sıradakiler bekler.
- **Aynı anda tek iş:** iş çalışırken yeni analiz/soru isteği `409 {"status":"busy"}` döner. Gönder düğmelerini iş bitene kadar kapatın.
- Slot boşalınca sıradaki kullanıcı **otomatik** aktif olur; sıra ekranı `active` olayını görünce haritaya geçmeli.

## 7. Hata durumları

| Durum | Ne zaman | `detail` |
|---|---|---|
| `404` | bilinmeyen oturum / iş | metin |
| `409` | oturum aktif değil | nesne: `{"status":"queued","position":2}` veya `{"status":"ended","reason":"idle"}` |
| `409` | iş çalışıyor | `{"status":"busy"}` |
| `409` | analizden önce soru | `{"status":"no_analysis"}` → "önce parseli analiz edin" |
| `422` | geçersiz poligon | **metin**, kullanıcıya olduğu gibi gösterilebilir (örn. `"Poligon kendini kesiyor; sınırları kesişmeyecek şekilde çizin."`) |
| `422` | geçersiz alan/tarih/soru (şema) | **liste** (FastAPI doğrulama nesneleri); genel "geçersiz girdi" gösterin |
| `422` | bilinmeyen bot | `"Bilinmeyen bot"` |
| akışta `{"agent":…,"error":…}` | tek bot hatası | o bot sütununda hata göster, diğeri devam eder |
| akışta `{"error":…}` | iş hatası | tüm alanda hata + "tekrar dene" |

`detail` bazen metin, bazen nesne, bazen liste olabilir; tipine bakarak ele alın. Beklenmedik bir ağ hatasında SSE'ye yeniden bağlanmayı deneyin (iş backend'de sürer, kaybolmaz; oturum bittiyse `409`/`404` alırsınız).

## 8. Arayüzde gösterilmesi gerekenler

- Harita üzerinde poligon çizme/düzenleme/silme, tarih seçici, "Analiz et" düğmesi.
- ML çalışırken ilerleme: `progress` olaylarından "iklim verisi alındı ✓ / uydu verisi bekleniyor…" gibi adımlar (NDVI 2 dakikaya kadar sürebilir; yüzde yok).
- `_meta.warnings` ve `_meta.disclaimer` her zaman görünür olmalı.
- Rotasyon kartları: ad, sıra, puan, risk, **olası zararlar** (`damages`, şiddet rozetiyle), faydalar, mevcut rotasyona göre fark. Altında "Kendi rotasyonumu değerlendir" formu (katalogdan) + öncelik kaydırıcıları → `POST /api/rotations`.
- Katmanlar: ad, birim, ortalama (min–max aralığıyla), **eminlik rozeti** (`confidence_level`). Düşük eminlik **görsel olarak belirgin** olmalı.
- **İki (veya N) bot yan yana**: başlıkta bot adı, altında akarak yazılan metin, hangi veriyi gördüğünü gösteren küçük etiket (`layers`). Mobilde alt alta.
- Takip sorusu kutusu + "tüm botlara / yalnız şu bota" seçimi.
- Sıra ekranı ("Sıranız: 2"), meşgul/uyarı/oturum bitti ekranları.

## 9. Frontend'in YAPMAMASI gerekenler

- **İş mantığı yok:** risk hesabı, eşik/eminlik sınıflandırması, sıralama, karşılaştırma backend'de. Frontend yalnızca gösterir.
- **vLLM'e veya ML'e doğrudan bağlanma yok.** API key, model adı, prompt frontend koduna/ortam değişkenine girmez. Yalnızca bu belgedeki `/api` uçları kullanılır.
- **Ham harita verisi/ML çıktısı bota gönderilmez**; zaten bota gidenleri backend hazırlıyor.
- Koordinatları **`[enlem, boylam]`** sırasıyla göndermeyin; GeoJSON **`[boylam, enlem]`**.
- Sohbet geçmişini `localStorage`'a yazmayın.
- Boş ekran bırakmayın: bekleme sırasında, ML çalışırken ve botlar yazmaya başlamadan önce yükleniyor durumu gösterin.
- Backend'in bot sayısını, bot adlarını ve katman adlarını **koda gömmeyin**; `GET /api/agents` ve olaylardan okuyun (değişecekler).
- Aşırı süslü animasyon / gereksiz bağımlılık eklemeyin.

## 10. Henüz YOK / değişebilir (bunlara göre sabit varsayım yapmayın)

- Gerçek ML katmanları yukarıdaki tabloda; `ML_PROVIDER=fake` iken eski sahte katmanlar (`yagis`, `toprak`, `nem`) gelir. Arayüzü katman listesinden dinamik kurun.
- Bot adları/prompt'ları değişebilir; listeyi `GET /api/agents`'ten okuyun.
- Rate limit ve kimlik doğrulama yok.
- Bir analizi iptal etme ucu yok (oturumdan çıkmak işi iptal eder).
- Analiz ilerleme yüzdesi yok.

## 11. Hızlı deneme (frontend olmadan)

```bash
# oturum aç
curl -s -X POST localhost:8000/api/session
# analiz başlat (SID'yi yukarıdan alın)
curl -s -X POST localhost:8000/api/analysis -H 'Content-Type: application/json' \
  -d '{"session_id":"SID","date":"2026-05-01","polygon":{"type":"Polygon","coordinates":[[[29.0,41.0],[29.01,41.0],[29.01,41.01],[29.0,41.01],[29.0,41.0]]]}}'
# akışı izle (JOB'u yukarıdan alın)
curl -N "localhost:8000/api/jobs/JOB/events?session_id=SID"
```
Etkileşimli şema (FastAPI otomatik): `http://localhost:8000/docs`.
