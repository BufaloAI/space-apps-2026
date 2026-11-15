# BufaloAI

NASA Space Apps için geliştirilen Türkçe ve İngilizce tarımsal veri çalışma alanı. Açılış ekranı, küresel harita, tarihsel analitik ve veri gezgini aynı seçili konumu kullanır.

## Çalıştırma

Node.js 22.12+ ile repo kökünde:

```sh
cd frontend
npm ci
npm run dev
```

http://localhost:5173 adresini açın. Statik üretim paketi için `npm run build`, paketi denemek için `npm run preview` kullanın.

## Veri ve bağlantılar

- Tarihsel ekranlar 1 Ocak 2024 – 31 Aralık 2025 için tekrarlanabilir sentetik günlük veriler kullanır. Tarih ve kaynak filtreleri analiz ve tablo arasında ortaktır. CSV, kaynak türünü `provenance=synthetic-demo` olarak belirtir. SMAP sütunu bağlı veri bulunana kadar boştur.
- Ortalamalar seçili günlerden hesaplanır. Yağış toplanır. En yüksek ve en düşük sıcaklıklar dönem uç değerleridir. Yıllık kıyaslar yalnız seçilen tarih aralığındaki günleri içerir.
- [FastAPI backend’ini](../backend/README.md) ayrı terminalde 8000 portunda başlatın. Ön yüzde **Parseli Analiz Et** oturum açar, çizilen GeoJSON poligonunu ve tek analiz tarihini gönderir. Sıra, iş durumu, uzman yanıt akışı ve takip soruları [mevcut API sözleşmesini](../backend/frontend-spec.md) kullanır. Örnek ML sağlayıcısının kaynak notları korunur.
- Vite, `/api` yolunu yerel backend’e iletir. Üretimde aynı yolu FastAPI’ye yönlendirin ve SSE yanıt tamponlamasını kapatın. Farklı origin için derleme sırasında `VITE_API_BASE_URL` (sonunda `/api`) tanımlayın ve backend CORS ayarını güncelleyin.
- `src/api.ts` gerçek API erişimini, `src/data.ts` koordinat, poligon, yanıt ve tarihsel seri tiplerini içerir. Tarihsel sağlayıcı eklendiğinde veriyi bu gözlem modeline dönüştürün. Gelecek hava ve toprak nemi uçlarına bugün istek gönderilmez.
- Leaflet uydu ve topoğrafya için Esri, sokak haritası için OpenStreetMap kullanır. Şehir ve ülke araması kullanıcı gönderdiğinde Nominatim'e gider. Koordinat araması, yerleşik konumlar ve Çumra başlangıç altlığı çevrimdışı çalışır. Küresel karolar internet gerektirir. Açılıştaki temsili arazi görseli bir ölçüm katmanı değildir.

