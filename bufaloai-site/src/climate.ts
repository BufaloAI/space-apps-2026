import { useEffect, useState } from 'react'
import { climateYear, type ClimateYear } from './api'
import type { Field } from './model'
// Values are null where the source has no observation (e.g. NASA POWER fill values).
export interface ClimateDay {
  date: string
  temperature: number | null
  rainfall: number | null
  humidity: number | null
  solar: number | null
  moisture: number | null
}
export interface ClimateService {
  getDailySeries(year: number, fieldId?: string): ClimateDay[]
}
export type Metric = Exclude<keyof ClimateDay, 'date'>
export const metrics: Record<Metric, [string, string, string]> = {
  temperature: ['Sıcaklık', 'Temperature', '°C'],
  rainfall: ['Yağış', 'Rainfall', 'mm'],
  humidity: ['Bağıl nem', 'Relative humidity', '%'],
  solar: ['Güneş radyasyonu', 'Solar radiation', 'MJ/m²/gün'],
  moisture: ['Yüzey toprak ıslaklığı', 'Surface soil wetness', '0–1'],
}
export const demonstrationClimate: ClimateService = {
  getDailySeries(year, fieldId = 'global') {
    let seed = Array.from(fieldId).reduce(
      (sum, c) => sum + c.charCodeAt(0),
      year * 331,
    )
    const random = () => {
      seed = (seed * 16807) % 2147483647
      return seed / 2147483647
    }
    const rows: ClimateDay[] = []
    for (
      let dt = Date.UTC(year, 0, 1);
      dt < Date.UTC(year + 1, 0, 1);
      dt += 86400000
    ) {
      const seasonal = Math.sin(((rows.length - 85) * 2 * Math.PI) / 365)
      const wet = random() > 0.72 + seasonal * 0.08
      rows.push({
        date: new Date(dt).toISOString().slice(0, 10),
        temperature: 14 + 12 * seasonal + (random() - 0.5) * 7,
        rainfall: wet ? 1 + random() * 14 : 0,
        humidity: 63 - 11 * seasonal + random() * 12,
        solar: 14 + 8 * seasonal + random() * 3,
        moisture: 0.24 - seasonal * 0.07 + random() * 0.04,
      })
    }
    return rows
  },
}
export function aggregate(rows: ClimateDay[], key: Metric) {
  const vals = rows.map((r) => r[key]).filter((v): v is number => v !== null)
  if (!vals.length) return NaN
  const total = vals.reduce((s, v) => s + v, 0)
  return key === 'rainfall' ? total : total / vals.length
}
export function indicators(
  rows: ClimateDay[],
  base: number,
  threshold: number,
) {
  let dry = 0,
    longest = 0,
    gdd = 0,
    hot = 0
  for (const r of rows) {
    if (r.rainfall !== null) {
      dry = r.rainfall < 1 ? dry + 1 : 0
      longest = Math.max(dry, longest)
    }
    if (r.temperature === null) continue
    gdd += Math.max(0, r.temperature - base)
    if (r.temperature >= threshold) hot++
  }
  return { longest, gdd, hot }
}

/** Field location for climate lookups: mean of the outer ring's vertices (closing point excluded). */
export function fieldPoint(f: Field): [number, number] {
  const ring = f.geometry.coordinates[0].slice(0, -1)
  const n = ring.length || 1
  return [ring.reduce((s, p) => s + p[0], 0) / n, ring.reduce((s, p) => s + p[1], 0) / n]
}

export type ClimateSource =
  | { kind: 'demo' }
  | { kind: 'loading' }
  | { kind: 'live'; info: ClimateYear }
  | { kind: 'failed'; message: string | null }

/**
 * Daily series per year for a field from the backend (NASA POWER, cached server- and client-side).
 * Without a field, or when the backend cannot deliver, the labelled synthetic series is used.
 */
export function useClimate(field: Field | undefined, years: number[]) {
  const [live, setLive] = useState<Record<string, ClimateYear>>({})
  const [state, setState] = useState<ClimateSource>({ kind: 'demo' })
  const point = field ? fieldPoint(field) : null
  const key = point ? point[0].toFixed(1) + ',' + point[1].toFixed(1) : ''
  const wanted = years.join(',')
  useEffect(() => {
    if (!point) {
      setState({ kind: 'demo' })
      return
    }
    let cancelled = false
    setState({ kind: 'loading' })
    Promise.allSettled(years.map((y) => climateYear(point[1], point[0], y))).then((res) => {
      if (cancelled) return
      const ok: Record<string, ClimateYear> = {}
      res.forEach((r, i) => {
        if (r.status === 'fulfilled') ok[key + ':' + years[i]] = r.value
      })
      setLive((prev) => ({ ...prev, ...ok }))
      const first = res.find((r) => r.status === 'fulfilled') as PromiseFulfilledResult<ClimateYear> | undefined
      const err = res.find((r) => r.status === 'rejected') as PromiseRejectedResult | undefined
      if (first && !err) setState({ kind: 'live', info: first.value })
      else
        setState({
          kind: 'failed',
          message: (err?.reason as { userText?: string } | undefined)?.userText ?? null,
        })
    })
    return () => {
      cancelled = true
    }
  }, [key, wanted])
  const rowsFor = (year: number): { rows: ClimateDay[]; live: ClimateYear | null } => {
    // Never mix sources in one view: live only when every requested year arrived.
    const hit = point && state.kind === 'live' ? live[key + ':' + year] : undefined
    return hit
      ? { rows: hit.days, live: hit }
      : { rows: demonstrationClimate.getDailySeries(year, field?.id), live: null }
  }
  return { state, rowsFor }
}
