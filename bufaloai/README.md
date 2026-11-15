# BUFALOAI

Türkçe ve İngilizce tarımsal harita çalışma alanı. Node.js 22.12 veya daha yeni bir sürümle bu klasörde çalıştırın.

```sh
npm ci
npm run dev
```

Geliştirme adresi http://127.0.0.1:5180. Üretim için `npm run build` komutu TypeScript denetimini çalıştırır ve statik dosyaları `dist/` içine yazar. `npm run preview` üretim derlemesini açar.

## Kullanım

- Yeni oturumda `/` adresi `/intro` ekranını açar. Küreyi sürükleyerek döndürün, tekerlek veya iki parmakla yakınlaşın ve uçuş için bir konum seçin. Klavyede küreye odaklanıp oklarla döndürebilir ve Enter ile ortadaki noktayı seçebilirsiniz.
- Uçuş sırasında 2D harita arka planda hazırlanır. Seçilen boylam ve enlem iki kamerada da kullanılır. Girişi atla veya Haritayı aç ile doğrudan `/app` yoluna geçilir. Aynı sekmedeki sonraki kök ziyaretleri çalışma alanını açar. Girişi yeniden oynat oturum işaretini temizleyip `/intro` yoluna döner.
- **Katmanlar** menüsünde siyasi, fiziki, uydu, hibrit, topografik, sokak, minimal açık ve minimal koyu haritalar bulunur. Vektör altlıkları ve uydu görüntüleri üzerinde sınırlar, bölgeler, yerleşimler, yollar ve coğrafi adlar ayrı ayrı açılabilir. Topografik ve sokak rasterlarında etiketler görüntüye işlenmiştir.
- **Dünya atlası** üzerinde nokta aracını seçip koordinat okuyun. Çokgen çizimini ilk köşeye tıklayarak, dikdörtgeni karşı köşeyi belirleyerek bitirin. Seçili tarlanın araçlarından sınırları ve özellikleri düzenleyin.
- **Tarlalarım** birden fazla tarlayı, Polygon/MultiPolygon GeoJSON içe aktarmayı ve GeoJSON dışa aktarmayı destekler. Çizimler, seçim, son harita görünümü, dil ve tema tarayıcıda saklanır.
- **İklim analizi**, **Tarımsal göstergeler** ve **Veri gezgini** belirlenimci sentetik günlük seriler kullanır. CSV seçili tarlanın seçili yıldaki tüm günlük değerlerini içerir. Gelecekteki veri kaynakları için servis sözleşmesi `src/climate.ts` dosyasındadır.
- Konum araması Photon, çevrimiçi harita katmanları OpenFreeMap, OpenStreetMap, OpenTopoMap, NASA GIBS ve Mapzen kaynaklarını kullanır. Koordinat araması `enlem, boylam` biçimindedir. Çevrimiçi katmanlar kullanılamadığında yerel Natural Earth sınırları görüntülenir.

CesiumJS yalnızca açılışta yüklenir. İsteğe bağlı olarak Vite ortam değişkeni `VITE_CESIUM_ION_TOKEN` ile alan adı kısıtlamalı, salt okunur bir Cesium ion istemci anahtarı tanımlandığında uçuşta World Terrain kullanılır. 2D çalışma alanı MapLibre GL JS, Terra Draw ve Turf kullanır. Küre dokusu ve yazı tipi kaynakları `public/assets/CREDITS.txt` dosyasındadır. NASA POWER, SMAP, kimlik doğrulama veya model servisine bağlantı yoktur.