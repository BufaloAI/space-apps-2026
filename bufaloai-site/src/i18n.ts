// English mode helpers. Backend/ML content is bilingual at the source (`*_en` fields); this picks the right one
// and translates the backend's fixed Turkish messages (errors, statuses). Unknown text falls back to Turkish.
import type { Language } from './model'

/** obj[key + '_en'] in English mode when present, else obj[key]. */
export function pick<T extends object>(lang: Language, obj: T | null | undefined, key: string): string {
  if (!obj) return ''
  const o = obj as Record<string, unknown>
  const v = lang === 'en' ? (o[key + '_en'] ?? o[key]) : o[key]
  return v == null ? '' : String(v)
}
export function pickList<T extends object>(lang: Language, obj: T | null | undefined, key: string): string[] {
  if (!obj) return []
  const o = obj as Record<string, unknown>
  const v = lang === 'en' ? (o[key + '_en'] ?? o[key]) : o[key]
  return Array.isArray(v) ? v.map(String) : []
}

const EXACT: Record<string, string> = {
  'Oturum bulunamadı': 'Session not found',
  'Oturum aktif değil': 'Session is not active',
  'İş bulunamadı': 'Job not found',
  'Bilinmeyen bot': 'Unknown advisor',
  'Model sunucusuna ulaşılamadı.': 'The language model server is unreachable.',
  'Analiz sırasında beklenmeyen bir hata oluştu.': 'An unexpected error occurred during the analysis.',
  'Soru yanıtlanırken beklenmeyen bir hata oluştu.': 'An unexpected error occurred while answering.',
  'Analiz sonuç üretemedi, lütfen tekrar deneyin.': 'The analysis produced no result; please try again.',
  'Bu parsel için önce analiz yapılmalı.': 'Analyse this parcel first.',
  'Gerçek ML sağlayıcısı etkin değil (ML_PROVIDER=xgboost).': 'The real ML provider is not enabled (ML_PROVIDER=xgboost).',
  'İklim verisi (NASA POWER) alınamadı; analiz yapılamıyor.': 'Climate data (NASA POWER) could not be fetched; the analysis cannot run.',
  'Hava görünümü için NASA POWER geçmişi alınamadı; biraz sonra tekrar deneyin.':
    'NASA POWER history for the outlook could not be fetched; try again shortly.',
  'Bu nokta için yeterli iklim geçmişi yok (en az 5 yıl gerekir).':
    'Not enough climate history for this point (at least 5 years needed).',
  'NASA POWER iklim verisine şu an ulaşılamıyor; biraz sonra tekrar deneyin.':
    'NASA POWER climate data is unreachable right now; try again shortly.',
  'Bu konum ve yıl için iklim verisi dönmedi.': 'No climate data was returned for this place and year.',
  'Poligon bir GeoJSON nesnesi olmalı.': 'The polygon must be a GeoJSON object.',
  'Yalnızca tek bir Polygon desteklenir (MultiPolygon veya başka tür olmaz).':
    'Only a single Polygon is supported (no MultiPolygon or other types).',
  'Poligon koordinatları eksik.': 'Polygon coordinates are missing.',
  'İçinde delik olan poligon desteklenmiyor.': 'Polygons with holes are not supported.',
  'Koordinatlar [boylam, enlem] sayı çiftleri olmalı.': 'Coordinates must be [longitude, latitude] number pairs.',
  'Koordinat aralık dışında (boylam -180..180, enlem -90..90).':
    'Coordinate out of range (longitude -180..180, latitude -90..90).',
  'Poligon en az 3 farklı noktadan oluşmalı.': 'The polygon needs at least 3 distinct points.',
  'Poligon kendini kesiyor; sınırları kesişmeyecek şekilde çizin.':
    'The polygon intersects itself; draw borders that do not cross.',
  'Geçersiz poligon.': 'Invalid polygon.',
  'Parsel çok küçük.': 'The parcel is too small.',
}
const PREFIX: [RegExp, (m: RegExpMatchArray) => string][] = [
  [/^Geçersiz rotasyon girdisi: (.*)$/s, (m) => 'Invalid rotation input: ' + m[1].replace('Bilinmeyen ürün', 'Unknown crop').replace('Bilinenler', 'Known')],
  [/^Parsel çok büyük \(en fazla (.*) hektar\)\.$/, (m) => `The parcel is too large (at most ${m[1]} ha).`],
  [/^Poligonda en fazla (\d+) nokta olabilir\.$/, (m) => `A polygon can have at most ${m[1]} points.`],
]

/** Backend message in the UI language (Turkish passes through in Turkish mode). */
export function serverText(lang: Language, text: string | null | undefined): string {
  if (!text) return ''
  if (lang === 'tr') return text
  if (EXACT[text]) return EXACT[text]
  for (const [re, f] of PREFIX) {
    const m = text.match(re)
    if (m) return f(m)
  }
  return text
}

export const LEVEL: Record<string, [string, string]> = {
  yüksek: ['yüksek', 'high'],
  orta: ['orta', 'medium'],
  düşük: ['düşük', 'low'],
  bilinmiyor: ['bilinmiyor', 'unknown'],
}

/** Readable names for backend layer ids (shown as chips under each advisor). */
export const LAYER_NAMES: Record<string, [string, string]> = {
  ndvi_tahmin: ['NDVI tahmini', 'NDVI forecast'],
  risk: ['risk', 'risk'],
  iklim: ['iklim', 'climate'],
  hava_tahmini: ['hava görünümü', 'weather outlook'],
  yagis_tahmini: ['yağış görünümü', 'rain outlook'],
  rotasyon_1: ['öneri 1', 'suggestion 1'],
  rotasyon_2: ['öneri 2', 'suggestion 2'],
  rotasyon_3: ['öneri 3', 'suggestion 3'],
  rotasyon_mevcut: ['mevcut rotasyon', 'current rotation'],
  rotasyon_secilen: ['seçilen rotasyon', 'chosen rotation'],
}
export function layerName(lang: Language, id: string) {
  const n = LAYER_NAMES[id]
  if (n) return lang === 'en' ? n[1] : n[0]
  const m = id.match(/^rotasyon_ozel_(\d+)$/)
  if (m) return lang === 'en' ? 'your rotation ' + m[1] : 'rotasyonunuz ' + m[1]
  return id
}

export const COMPONENTS: Record<string, [string, string]> = {
  vegetation_stress: ['bitki stresi', 'vegetation stress'],
  water_stress: ['su stresi', 'water stress'],
  heat_stress: ['sıcak stresi', 'heat stress'],
  frost_risk: ['don riski', 'frost risk'],
}

export const UNITS: Record<string, [string, string]> = { puan: ['puan', 'points'] }
export const unitText = (lang: Language, u: string) => (UNITS[u] ? UNITS[u][lang === 'en' ? 1 : 0] : u)
