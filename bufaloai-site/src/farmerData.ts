// Farmer's own crops and rotations, kept in this browser (localStorage) and sent with every analysis.
// Later, with accounts and a database, this module is the only place that needs to change.
import { useEffect, useState } from 'react'
import { rotationCatalog, type Catalog, type CatalogCrop, type CustomCrop, type CustomRotation, type RotationOptions } from './api'
import { read, save } from './model'

export const MAX_CROPS = 30
export const MAX_ROTATIONS = 30
export const MAX_SEASONS = 6

function useStored<T>(key: string, fallback: T) {
  const [value, set] = useState<T>(() => read(key, fallback))
  useEffect(() => {
    save(key, value)
  }, [key, value])
  // Other tabs editing the same list stay in sync.
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key === 'bufaloai.atlas.' + key) set(read(key, fallback))
    }
    window.addEventListener('storage', onStorage)
    return () => window.removeEventListener('storage', onStorage)
  }, [key])
  return [value, set] as const
}

export const useCustomCrops = () => useStored<CustomCrop[]>('customCrops', [])
export const useCustomRotations = () => useStored<CustomRotation[]>('customRotations', [])

const TR: Record<string, string> = { ç: 'c', ğ: 'g', ı: 'i', ö: 'o', ş: 's', ü: 'u', â: 'a', î: 'i', û: 'u' }
/** Backend crop ids must match ^[a-z0-9_]+$; farmer crops get a `my_` prefix so they never clash by accident. */
export function cropId(name: string, taken: string[]) {
  const base =
    'my_' +
      name
        .toLowerCase()
        .replace(/[çğıöşüâîû]/g, (c) => TR[c] ?? c)
        .replace(/[^a-z0-9]+/g, '_')
        .replace(/^_+|_+$/g, '')
        .slice(0, 30) || 'my_crop'
  let id = base,
    n = 2
  while (taken.includes(id)) id = base + '_' + n++
  return id
}

export type Season = { main: string; cover: string }
export const seqToString = (s: Season[]) =>
  s.map((x) => (x.cover ? x.main + '+' + x.cover : x.main)).join('>')
export const seqFromString = (s: string): Season[] =>
  s.split('>').map((x) => {
    const [main, cover = ''] = x.split('+')
    return { main, cover }
  })

/** Rotations that reference a crop the farmer has since deleted would be rejected by the backend. */
export function usableRotations(rotations: CustomRotation[], knownIds: Set<string>) {
  return rotations.filter((r) =>
    r.seq.split(/[>+]/).every((id) => knownIds.has(id)),
  )
}

// ---------------------------------------------------------------- catalogue (shared by AI page and planner)

export function useCatalog() {
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    let alive = true
    rotationCatalog()
      .then((c) => alive && setCatalog(c))
      .catch(() => alive && setFailed(true))
    return () => {
      alive = false
    }
  }, [])
  return { catalog, failed }
}

/** What every analysis / re-score sends: the farmer's crops and the rotations that still resolve. */
export function farmerOptions(
  catalog: Catalog | null,
  crops: CustomCrop[],
  rotations: CustomRotation[],
): RotationOptions {
  const known = new Set([...(catalog?.crops.map((c) => c.id) ?? []), ...crops.map((c) => c.id)])
  return { custom_crops: crops, custom_rotations: catalog ? usableRotations(rotations, known) : [] }
}

/** Library/catalogue crop -> editable farmer crop (keeps the id so a catalogue crop is overridden). */
export function cropFromCatalog(c: CatalogCrop, lang: 'tr' | 'en'): CustomCrop {
  const p = c.profile ?? {}
  return {
    id: c.id,
    name: (lang === 'en' ? c.name_en : c.name) ?? c.name,
    family: c.family,
    season: (['warm', 'cool', 'perennial', 'none'].includes(c.season) ? c.season : 'warm') as CustomCrop['season'],
    cover_crop: c.cover_crop,
    water_need: p.water_need ?? 0.5,
    drought_tol: p.drought_tol ?? 0.5,
    heat_tol: p.heat_tol ?? 0.5,
    frost_tol: p.frost_tol ?? 0.5,
    residue_cover: p.residue_cover ?? 0.5,
    om: p.om ?? 0.5,
    market: p.market ?? 0.5,
    forage: p.forage ?? 0.3,
    n_need: p.n_need ?? 0.5,
    wet_tol: p.wet_tol ?? 0.7,
    gdd_need: p.gdd_need ?? 0,
    n_fix: p.n_fix ?? false,
  }
}
