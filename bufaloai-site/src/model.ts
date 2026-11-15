import type { Feature, Polygon, FeatureCollection } from 'geojson'
import { area } from '@turf/area'
import { booleanValid } from '@turf/boolean-valid'
export type Language = 'tr' | 'en'
export type Translate = (tr: string, en: string) => string
export type Page =
  'intro' | 'map' | 'fields' | 'climate' | 'indicators' | 'data' | 'ai' | 'planner'
export type View = { center: [number, number]; zoom: number }
export type FieldInfo = {
  name: string
  crop: string
  planted: string
  irrigation: string
  notes: string
  // Farmer's own knowledge, sent to the backend as `profile` / `existing_rotation`.
  // Each value the farmer fills in raises the rotation confidence; blanks are never guessed.
  soil_texture: string
  soil_om_pct: string
  slope_pct: string
  existing_rotation: string
}
export type Field = Feature<Polygon, FieldInfo> & { id: string }
export const blankInfo: FieldInfo = {
  name: '',
  crop: '',
  planted: '',
  irrigation: '',
  notes: '',
  soil_texture: '',
  soil_om_pct: '',
  slope_pct: '',
  existing_rotation: '',
}
export function read<T>(key: string, fallback: T): T {
  try {
    const v = localStorage.getItem('bufaloai.atlas.' + key)
    return v ? JSON.parse(v) : fallback
  } catch {
    return fallback
  }
}
export function save(key: string, value: unknown) {
  try {
    localStorage.setItem('bufaloai.atlas.' + key, JSON.stringify(value))
    return true
  } catch {
    return false
  }
}
export const hectares = (f: Field) => area(f) / 10000
export const coords = (p: number[], digits = 2) =>
  Math.abs(p[1]).toFixed(digits) +
  '° ' +
  (p[1] >= 0 ? 'N' : 'S') +
  '   ' +
  Math.abs(p[0]).toFixed(digits) +
  '° ' +
  (p[0] >= 0 ? 'E' : 'W')
export function download(name: string, body: string, type: string) {
  const link = document.createElement('a')
  const url = URL.createObjectURL(new Blob([body], { type }))
  link.href = url
  link.download = name
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
export const collection = (fields: Field[]): FeatureCollection => ({
  type: 'FeatureCollection',
  features: fields,
})
export function importFields(data: unknown, fallback: string): Field[] {
  const d = data as FeatureCollection | Feature | Polygon
  const features =
    d.type === 'FeatureCollection'
      ? d.features
      : d.type === 'Feature'
        ? [d]
        : [{ geometry: d, properties: {} }]
  if (!features.length) throw Error()
  return features.flatMap((f, i) => {
    const g = f.geometry
    if (!g || !['Polygon', 'MultiPolygon'].includes(g.type)) throw Error()
    const polygons =
      g.type === 'MultiPolygon' ? g.coordinates : [(g as Polygon).coordinates]
    return polygons.map((coordinates, j) => {
      const geometry: Polygon = { type: 'Polygon', coordinates }
      if (
        !coordinates.length ||
        !coordinates.every(
          (r) =>
            r.length >= 4 &&
            r.every(
              (p) =>
                Number.isFinite(p[0]) &&
                Number.isFinite(p[1]) &&
                Math.abs(p[0]) <= 180 &&
                Math.abs(p[1]) <= 90,
            ),
        ) ||
        !booleanValid(geometry) ||
        !(area(geometry) > 0)
      )
        throw Error()
      const info = { ...blankInfo }
      for (const k of Object.keys(info) as (keyof FieldInfo)[]) {
        const v = (f.properties as Record<string, unknown> | null)?.[k]
        if (typeof v === 'string') info[k] = v
      }
      return {
        type: 'Feature' as const,
        id: crypto.randomUUID(),
        geometry,
        properties: {
          ...info,
          name:
            (info.name || fallback + ' ' + (i + 1)) +
            (polygons.length > 1 ? ' ' + (j + 1) : ''),
        },
      }
    })
  })
}
export function locate(): Promise<View> {
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) {
      reject(Error())
      return
    }
    navigator.geolocation.getCurrentPosition(
      (p) =>
        resolve({ center: [p.coords.longitude, p.coords.latitude], zoom: 12 }),
      reject,
      { timeout: 10000, maximumAge: 120000 },
    )
  })
}
export const globalView: View = { center: [0, 12], zoom: 1.25 }
export const demoImage = {
  url: '/assets/san-joaquin-landsat.jpg',
  west: -120.65082550048828,
  south: 36.497769483804646,
  east: -120.12348175048828,
  north: 36.77986732466111,
}
export const landsat =
  'https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/Landsat_WELD_CorrectedReflectance_TrueColor_Global_Annual/default/2000-12-01/GoogleMapsCompatible_Level12/{z}/{y}/{x}.jpeg'
