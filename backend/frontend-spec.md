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
| Tarih | `POST /api/analysis` → `date` | `"YYYY-MM-DD"` (yıl 2000–2100) |
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
  { "id": "a", "name": "Yağış Analisti", "layers": ["yagis"] },
  { "id": "b", "name": "Toprak ve Nem Analisti", "layers": ["toprak", "nem"] }
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
Cevap: `{ "job_id": "lyugAUXz788" }`. Analiz **arka planda** başlar; hemen ardından aşağıdaki SSE'ye bağlanın.

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

### 5.5 İş akışı (SSE) ve anlık durum

**`GET /api/jobs/{job_id}/events?session_id=…`** (SSE, `EventSource` ile kullanılabilir — GET'tir)

Her olayın bir **`id`**'si vardır (0'dan başlar, kesintisiz artar). Bağlantı koparsa `EventSource` kendiliğinden `Last-Event-ID` göndererek **kaldığı yerden devam eder**. `fetch` ile okuyorsanız `?after=<son aldığınız id>` verin. İş bitince akış **kapanır**. Boşta kalırsa `: ping` yorum satırları gelir (yok sayın).

Olay türleri (alanlara göre ayırt edin):

| Olay | Ne zaman | Biçim |
|---|---|---|
| ML başladı | akışın ilk olayı | `{"stage":"ml","status":"started","date":"2026-05-01","parcel":{"area_ha":92.86,"centroid":[29.005,41.005]}}` |
| ML bitti | ML sonuçları hazır | `{"stage":"ml","status":"done","errors":[],"layers":[ … ]}` (katman biçimi aşağıda) |
| Botlar | botlar yazmaya başlamadan | `{"stage":"agents","agents":[{"id":"a","name":"Yağış Analisti","layers":["yagis"]}, …]}` |
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
- Şu an ML **sahte veri** üretiyor (`note` içinde yazıyor); gerçek ML gelince alanlar aynı kalır.
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
- ML çalışırken ilerleme (şimdilik yalnızca "ML çalışıyor" → "botlar yazıyor" aşamaları var; yüzde yok).
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

- ML şu an **sahte veri** üretiyor; katmanların adları (`yagis`, `toprak`, `nem`) ve sayısı gerçek ML ile değişebilir. Arayüzü katman listesinden dinamik kurun.
- Bot adları/prompt'ları yer tutucu; botların kimlikleri değişebilir.
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
