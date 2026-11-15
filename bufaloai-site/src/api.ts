// Backend API client. Contract: backend/frontend-spec.md (the code in backend/app/main.py wins on conflict).
// All business logic (risk, confidence levels, ranking) lives in the backend; this file only moves data.
import type { Polygon } from 'geojson'

const BASE = (import.meta.env.VITE_API_BASE ?? '').replace(/\/+$/, '')
export const apiUrl = (path: string) => BASE + path

// ---------------------------------------------------------------- types
export type ConfidenceLevel = 'yüksek' | 'orta' | 'düşük' | string
export type Damage = {
  risk: string
  affects: string
  severity: 'yüksek' | 'orta' | 'düşük' | string
  yield_loss_pct: [number, number] | null
  impact: string
}
export type RotationDetails = {
  name?: string
  sequence_names?: string[]
  score?: number
  risk?: number
  why?: string[]
  benefits?: string[]
  damages?: Damage[]
  expected_yield_loss_pct?: Record<string, [number, number]>
  risks_for_local_review?: string[]
  delta_vs_existing?: { score: number; risk: number }
  source?: 'katalog' | 'otomatik' | 'çiftçi' | string
  rank?: number
  economics?: {
    per_season: { crop: string; gross_margin: number; expected_yield_t_ha?: number }[]
    mean_gross_margin_per_ha: number
    note: string
  }
}
export type OutlookVar = { p10: number; p50: number; p90: number; normal: number }
export type Outlook = {
  issued_from: string
  history_years: number
  history_start: string
  weeks: { week: number; start: string; end: string; vars: Record<string, OutlookVar> }[]
  skill: Record<string, (number | null)[]>
  interval_coverage: Record<string, (number | null)[]>
  variables: Record<string, { label: string; unit: string; aggregation: 'mean' | 'sum' }>
  method: string
  confidence?: Record<string, number>
  lat?: number
  lon?: number
}
export type Meta = {
  warnings?: string[]
  data_as_of?: Record<string, string | null>
  data_sources?: unknown
  information_gaps?: string[]
  disclaimer?: string
  model_version?: string
  field_info?: unknown
}
export type Layer = {
  name: string
  label: string
  unit: string
  mean: number
  min: number
  max: number
  confidence: number
  confidence_level: ConfidenceLevel
  note?: string
  details?: (RotationDetails & { _meta?: Meta; [k: string]: unknown }) | null
}
export type Agent = { id: string; name: string; name_en?: string; layers: string[] }
export type Parcel = { area_ha: number; centroid: [number, number] }
export type SessionStatus =
  | { status: 'active'; idle_remaining: number | null; job_id: string | null; has_analysis: boolean }
  | { status: 'queued'; position: number }
  | { status: 'ended'; reason: 'left' | 'idle' | 'queue_timeout' | 'unknown' | string }
export type Progress = {
  stage: 'data' | 'features' | 'model' | 'rotations' | string
  source?: string
  status?: string
  [k: string]: unknown
}
export type JobEvent =
  | { stage: 'ml'; status: 'started'; date: string; parcel: Parcel }
  | { stage: 'ml'; status: 'progress'; progress: Progress }
  | { stage: 'ml'; status: 'done'; errors: string[]; layers: Layer[] }
  | { stage: 'agents'; agents: Agent[] }
  | { stage: 'ask'; message: string; agents: Agent[] }
  | { agent: string; delta: string }
  | { agent: string; done: true }
  | { agent: string; error: string }
  | { done: true }
  | { error: string }
export type AgentState = Agent & {
  text: string
  status: 'running' | 'done' | 'error'
  error?: string
}
export type JobSnapshot = {
  job_id: string
  kind: 'analysis' | 'ask'
  status: 'running' | 'done' | 'error' | 'cancelled'
  last_event_id: number
  parcel: Parcel | null
  date: string | null
  layers: Layer[]
  ml_errors: string[]
  question: string | null
  agents: AgentState[]
  error: string | null
}
export type CropProfile = {
  water_need: number
  drought_tol: number
  heat_tol: number
  frost_tol: number
  residue_cover: number
  om: number
  market: number
  forage: number
  n_need: number
  wet_tol: number
  gdd_need: number
  n_fix: boolean
}
/** A farmer's own crop (or a catalogue crop overridden under the same id). Stored in the browser. */
export type CustomCrop = CropProfile & {
  id: string
  name: string
  family: string
  season: 'warm' | 'cool' | 'perennial' | 'none'
  cover_crop: boolean
  yield_t_ha?: number
  price_per_t?: number
  cost_per_ha?: number
}
export type CustomRotation = { name: string; seq: string }
export type CatalogCrop = {
  id: string
  name: string
  name_en?: string
  family: string
  season: string
  cover_crop: boolean
  profile?: Partial<CropProfile>
  note?: string
  note_en?: string
}
export type Catalog = {
  crops: CatalogCrop[]
  library?: CatalogCrop[]
  crop_fields?: { floats_0_1: string[]; seasons: string[]; economics: string[]; families: string[] }
  rotations: { id: string; name: string; name_en?: string; sequence: string[] }[]
  priorities: { id: string; label: string; label_en?: string; default: number }[]
  rotation_syntax: string
}
export type FarmProfile = {
  irrigation?: boolean
  soil_om_pct?: number
  slope_pct?: number
  soil_texture?: string
}
export type RotationOptions = {
  existing_rotation?: string
  chosen_rotation?: string
  priorities?: Record<string, number>
  profile?: FarmProfile
  custom_crops?: CustomCrop[]
  custom_rotations?: CustomRotation[]
}

// ---------------------------------------------------------------- errors
export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: unknown,
  ) {
    super(typeof detail === 'string' ? detail : 'HTTP ' + status)
  }
  /** Backend `detail` is sometimes text, an object or a FastAPI validation list. */
  get kind():
    | 'busy'
    | 'no_analysis'
    | 'queued'
    | 'ended'
    | 'not_found'
    | 'invalid'
    | 'network'
    | 'other' {
    const d = this.detail as { status?: string } | null
    if (this.status === 0) return 'network'
    if (this.status === 404) return 'not_found'
    if (this.status === 409 && d && typeof d === 'object' && 'status' in d) {
      const s = d.status
      if (s === 'busy' || s === 'no_analysis' || s === 'queued' || s === 'ended') return s
    }
    if (this.status === 422) return 'invalid'
    return 'other'
  }
  /** Text the backend wrote for humans (Turkish), if any. */
  get userText(): string | null {
    return typeof this.detail === 'string' ? this.detail : null
  }
}

async function request<T>(path: string, init?: RequestInit & { json?: unknown }): Promise<T> {
  let res: Response
  try {
    res = await fetch(apiUrl(path), {
      ...init,
      headers: init?.json !== undefined ? { 'Content-Type': 'application/json' } : init?.headers,
      body: init?.json !== undefined ? JSON.stringify(init.json) : init?.body,
    })
  } catch (e) {
    if ((e as Error).name === 'AbortError') throw e
    throw new ApiError(0, null)
  }
  const body = await res.json().catch(() => null)
  if (!res.ok) throw new ApiError(res.status, body?.detail ?? null)
  return body as T
}
const post = <T>(path: string, json?: unknown) =>
  request<T>(path, { method: 'POST', json: json ?? undefined })

// ---------------------------------------------------------------- session
export const joinSession = () =>
  post<SessionStatus & { session_id: string }>('/api/session')
export const sessionStatus = (sid: string) =>
  request<SessionStatus>('/api/session/' + encodeURIComponent(sid))
export const keepalive = (sid: string) =>
  post<{ ok: true }>('/api/session/' + encodeURIComponent(sid) + '/keepalive')
export const leaveSession = (sid: string) =>
  post<{ ok: true }>('/api/session/' + encodeURIComponent(sid) + '/leave')
/** Tab closing: only sendBeacon reliably survives unload, and it can only POST. */
export function leaveBeacon(sid: string) {
  const url = apiUrl('/api/session/' + encodeURIComponent(sid) + '/leave')
  if (!navigator.sendBeacon?.(url)) fetch(url, { method: 'POST', keepalive: true }).catch(() => {})
}

/** Queue updates over SSE. The stream closes once active or ended. Returns a closer. */
export function followQueue(
  sid: string,
  onStatus: (s: SessionStatus) => void,
  onError: () => void,
) {
  const es = new EventSource(apiUrl('/api/session/' + encodeURIComponent(sid) + '/events'))
  es.onmessage = (m) => {
    const s = JSON.parse(m.data) as SessionStatus
    onStatus(s)
    if (s.status !== 'queued') es.close()
  }
  es.onerror = () => {
    if (es.readyState === EventSource.CLOSED) onError()
  }
  return () => es.close()
}

// ---------------------------------------------------------------- analysis & bots
export const getAgents = () => request<Agent[]>('/api/agents')
export const startAnalysis = (
  sid: string,
  polygon: Polygon,
  date: string,
  options: RotationOptions = {},
  lang: 'tr' | 'en' = 'tr',
) => post<{ job_id: string }>('/api/analysis', { session_id: sid, polygon, date, lang, ...options })
export const askAgents = (sid: string, message: string, agent?: string, lang: 'tr' | 'en' = 'tr') =>
  post<{ job_id: string }>('/api/ask', { session_id: sid, message, agent: agent || undefined, lang })

export type CropSuggestion = {
  id: string
  name: string
  name_en: string
  from: 'library' | 'catalog'
  fit: number
  family: string
  season: string
  expected_yield_loss_pct: [number, number]
  main_risk: string | null
  main_risk_en: string | null
  concerns: string[]
  concerns_en: string[]
  note?: string | null
  note_en?: string | null
}
/** Crops that suit the last analysed field and are not in the farmer's list (needs an analysis). */
export const cropSuggestions = (sid: string, exclude: string[]) =>
  request<CropSuggestion[]>(
    '/api/crops/suggestions?' + new URLSearchParams({ session_id: sid, exclude: exclude.join(',') }),
  )
export const jobSnapshot = (sid: string, jobId: string) =>
  request<JobSnapshot>(
    '/api/jobs/' + encodeURIComponent(jobId) + '?session_id=' + encodeURIComponent(sid),
  )

/**
 * Job events over SSE. EventSource resumes with Last-Event-ID on its own after a drop;
 * `after` resumes from a snapshot's `last_event_id`. The server closes the stream when the job ends.
 */
export function followJob(
  sid: string,
  jobId: string,
  onEvent: (e: JobEvent, id: number) => void,
  onClosed: (finished: boolean) => void,
  after?: number,
) {
  const q = new URLSearchParams({ session_id: sid })
  if (after !== undefined && after >= 0) q.set('after', String(after))
  const es = new EventSource(apiUrl('/api/jobs/' + encodeURIComponent(jobId) + '/events?' + q))
  let finished = false
  es.onmessage = (m) => {
    const e = JSON.parse(m.data) as JobEvent
    if (!('agent' in e) && ('done' in e || 'error' in e)) finished = true
    onEvent(e, Number(m.lastEventId))
    if (finished) {
      es.close()
      onClosed(true)
    }
  }
  es.onerror = () => {
    // Server closed after the last event, or the session is gone (404/409 ⇒ CLOSED).
    if (es.readyState === EventSource.CLOSED || finished) {
      es.close()
      onClosed(finished)
    }
  }
  return () => es.close()
}

// ---------------------------------------------------------------- rotations
let catalogPromise: Promise<Catalog> | null = null
export function rotationCatalog() {
  catalogPromise ??= request<Catalog>('/api/rotations/catalog').catch((e) => {
    catalogPromise = null
    throw e
  })
  return catalogPromise
}
export const rescoreRotations = (sid: string, options: RotationOptions) =>
  post<{ layers: Layer[]; options: RotationOptions }>('/api/rotations', {
    session_id: sid,
    ...options,
  })

// ---------------------------------------------------------------- region climate (NASA POWER, cached)
export type ClimateValue = number | null
export type ClimateRow = {
  date: string
  temperature: ClimateValue
  rainfall: ClimateValue
  humidity: ClimateValue
  solar: ClimateValue
  moisture: ClimateValue
}
export type ClimateYear = {
  source: string
  lat: number
  lon: number
  year: number
  fetched_on: string
  complete: boolean
  last_date: string | null
  units: Record<string, string>
  days: ClimateRow[]
  cached?: boolean
  stale?: boolean
}

const memory = new Map<string, Promise<ClimateYear>>()
const STORE = 'bufaloai.climate.'
const STORE_MAX = 24
// Same 0.1° grid as the backend cache, so nearby fields share entries.
const snap = (v: number) => (Math.round(v * 10) / 10).toFixed(1)

function stored(key: string): ClimateYear | null {
  try {
    const v = localStorage.getItem(STORE + key)
    return v ? (JSON.parse(v) as ClimateYear) : null
  } catch {
    return null
  }
}
function store(key: string, data: ClimateYear) {
  // Only complete (past) years are kept across visits; the current year changes daily.
  if (!data.complete) return
  try {
    const keys = Object.keys(localStorage).filter((k) => k.startsWith(STORE))
    if (keys.length >= STORE_MAX) localStorage.removeItem(keys[0])
    localStorage.setItem(STORE + key, JSON.stringify(data))
  } catch {}
}

/** Daily climate for a point and year. Cached in memory, and in localStorage for past years. */
export function climateYear(lat: number, lon: number, year: number): Promise<ClimateYear> {
  const key = snap(lat) + '_' + snap(lon) + '_' + year
  const hit = memory.get(key)
  if (hit) return hit
  const local = stored(key)
  const p = local
    ? Promise.resolve(local)
    : request<ClimateYear>(
        '/api/climate/daily?' + new URLSearchParams({ lat: snap(lat), lon: snap(lon), year: String(year) }),
      ).then((d) => {
        store(key, d)
        return d
      })
  memory.set(key, p)
  p.catch(() => memory.delete(key))
  return p
}

// ---------------------------------------------------------------- weather outlook (NASA POWER history, cached per day)
const outlooks = new Map<string, Promise<Outlook>>()
/** 4-week outlook for a point. The backend caches per day and 0.1° cell; this caches per page load. */
export function climateOutlook(lat: number, lon: number): Promise<Outlook> {
  const key = snap(lat) + '_' + snap(lon) + '_' + new Date().toISOString().slice(0, 10)
  const hit = outlooks.get(key)
  if (hit) return hit
  const p = request<Outlook>('/api/climate/outlook?' + new URLSearchParams({ lat: snap(lat), lon: snap(lon) }))
  outlooks.set(key, p)
  p.catch(() => outlooks.delete(key))
  return p
}
