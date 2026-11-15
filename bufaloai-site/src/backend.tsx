// Session + job state for the backend, kept at App level so results survive page switches.
// Lifecycle rules come from backend/frontend-spec.md §6: one active user, FIFO queue,
// 5 min idle timeout (paused while a job runs), sendBeacon on tab close, one job at a time.
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import * as api from './api'
import { serverText } from './i18n'
import type { Field, FieldInfo } from './model'

export type Phase = 'idle' | 'joining' | 'queued' | 'active' | 'ended' | 'offline'
export type Round = {
  jobId: string
  kind: 'analysis' | 'ask'
  question: string | null
  agents: api.AgentState[]
}
export type Analysis = {
  fieldId: string
  fieldName: string
  parcel: api.Parcel | null
  date: string
  layers: api.Layer[]
  mlErrors: string[]
  progress: api.Progress[]
  mlDone: boolean
}
export type JobInfo = {
  id: string
  kind: 'analysis' | 'ask'
  status: 'running' | 'done' | 'error'
  error: string | null
}
type State = {
  phase: Phase
  sid: string | null
  position: number | null
  reason: string | null
  idleRemaining: number | null
  idleAt: number
  agents: api.Agent[]
  analysis: Analysis | null
  rounds: Round[]
  job: JobInfo | null
  notice: string | null
}
const initial: State = {
  phase: 'idle',
  sid: null,
  position: null,
  reason: null,
  idleRemaining: null,
  idleAt: 0,
  agents: [],
  analysis: null,
  rounds: [],
  job: null,
  notice: null,
}

const SID_KEY = 'bufaloai.backend.session'
const readSid = () => {
  try {
    return sessionStorage.getItem(SID_KEY)
  } catch {
    return null
  }
}
const writeSid = (sid: string | null) => {
  try {
    if (sid) sessionStorage.setItem(SID_KEY, sid)
    else sessionStorage.removeItem(SID_KEY)
  } catch {}
}

/** Farmer-supplied field knowledge → backend `profile`. Unknown values are left out, never guessed. */
export function profileOf(info: Partial<FieldInfo>): api.FarmProfile | undefined {
  const p: api.FarmProfile = {}
  if (info.irrigation) p.irrigation = info.irrigation !== 'rain'
  const num = (s?: string) => (s && s.trim() !== '' && Number.isFinite(+s) ? +s : undefined)
  const om = num(info.soil_om_pct),
    slope = num(info.slope_pct)
  if (om !== undefined) p.soil_om_pct = om
  if (slope !== undefined) p.slope_pct = slope
  if (info.soil_texture) p.soil_texture = info.soil_texture
  return Object.keys(p).length ? p : undefined
}

function applyEvent(s: State, e: api.JobEvent): State {
  const job = s.job
  if (!job) return s
  const round = s.rounds.find((r) => r.jobId === job.id)
  const setRound = (f: (r: Round) => Round): State =>
    round ? { ...s, rounds: s.rounds.map((r) => (r === round ? f(r) : r)) } : s
  if ('agent' in e) {
    return setRound((r) => ({
      ...r,
      agents: r.agents.map((a) =>
        a.id !== e.agent
          ? a
          : 'delta' in e
            ? { ...a, text: a.text + e.delta }
            : 'error' in e
              ? { ...a, status: 'error', error: e.error }
              : { ...a, status: 'done' },
      ),
    }))
  }
  if ('stage' in e) {
    if (e.stage === 'ml' && s.analysis) {
      const a = s.analysis
      if (e.status === 'started') return { ...s, analysis: { ...a, parcel: e.parcel, date: e.date } }
      if (e.status === 'progress')
        return { ...s, analysis: { ...a, progress: [...a.progress, e.progress] } }
      if (e.status === 'done')
        return { ...s, analysis: { ...a, layers: e.layers, mlErrors: e.errors, mlDone: true } }
    }
    if (e.stage === 'agents' || e.stage === 'ask') {
      const agents = e.agents.map((a) => ({ ...a, text: '', status: 'running' as const }))
      const fresh: Round = {
        jobId: job.id,
        kind: e.stage === 'ask' ? 'ask' : 'analysis',
        question: e.stage === 'ask' ? e.message : null,
        agents,
      }
      return {
        ...s,
        agents: s.agents.length ? s.agents : e.agents,
        rounds: round
          ? s.rounds.map((r) => (r === round ? { ...fresh, agents } : r))
          : [...s.rounds, fresh],
      }
    }
    return s
  }
  if ('error' in e) return { ...s, job: { ...job, status: 'error', error: e.error } }
  if ('done' in e) return { ...s, job: { ...job, status: 'done' } }
  return s
}

type Ctx = State & {
  connect: () => void
  leave: () => void
  touch: () => void
  analyze: (field: Field, date: string, options?: api.RotationOptions) => Promise<void>
  ask: (message: string, agent?: string) => Promise<void>
  rescore: (options: api.RotationOptions) => Promise<api.RotationOptions>
  clearNotice: () => void
}
const BackendContext = createContext<Ctx | null>(null)
export function useBackend() {
  const c = useContext(BackendContext)
  if (!c) throw Error('BackendProvider missing')
  return c
}

export function BackendProvider({
  children,
  t,
  lang,
}: {
  children: ReactNode
  t: (tr: string, en: string) => string
  lang: 'tr' | 'en'
}) {
  const [state, setState] = useState<State>(initial)
  const ref = useRef(state)
  ref.current = state
  const closeQueue = useRef<() => void>(undefined)
  const closeJob = useRef<() => void>(undefined)
  const lastTouch = useRef(0)
  const tr = useRef(t)
  tr.current = t
  const language = useRef(lang)
  language.current = lang
  const patch = useCallback((p: Partial<State>) => setState((s) => ({ ...s, ...p })), [])

  /** Turns an API failure into session state (ended/queued) or a visible notice. */
  const fail = useCallback(
    (e: unknown) => {
      const t = tr.current
      if (!(e instanceof api.ApiError)) {
        patch({ notice: t('Beklenmeyen bir hata oluştu.', 'Something went wrong.') })
        return
      }
      const d = e.detail as { status?: string; reason?: string; position?: number }
      switch (e.kind) {
        case 'ended':
          patch({ phase: 'ended', reason: d.reason ?? 'unknown', job: null })
          return
        case 'not_found':
          patch({ phase: 'ended', reason: 'unknown', job: null })
          return
        case 'queued':
          patch({ phase: 'queued', position: d.position ?? null })
          return
        case 'busy':
          patch({ notice: t('Önceki iş bitmeden yenisi başlatılamaz.', 'Wait for the current job to finish.') })
          return
        case 'no_analysis':
          patch({ notice: t('Önce parseli analiz edin.', 'Analyse a field first.') })
          return
        case 'network':
          patch({
            notice: t(
              'Sunucuya ulaşılamadı. Backend çalışıyor mu?',
              'Cannot reach the server. Is the backend running?',
            ),
          })
          return
        case 'invalid':
          patch({
            notice: serverText(language.current, e.userText) || t('Geçersiz girdi.', 'Invalid input.'),
          })
          return
        default:
          patch({ notice: serverText(language.current, e.userText) || t('Sunucu hatası.', 'Server error.') })
      }
    },
    [patch],
  )

  const follow = useCallback(
    (sid: string, jobId: string, after?: number) => {
      closeJob.current?.()
      closeJob.current = api.followJob(
        sid,
        jobId,
        (e) => setState((s) => applyEvent(s, e)),
        (finished) => {
          if (finished) {
            // Idle timer restarts when a job ends.
            setState((s) => ({ ...s, idleRemaining: null, idleAt: 0 }))
            refresh()
            return
          }
          // Dropped without a final event: rebuild from a snapshot (session may have ended).
          api
            .jobSnapshot(sid, jobId)
            .then((snap) => {
              restore(snap)
              if (snap.status === 'running') follow(sid, jobId, snap.last_event_id)
            })
            .catch(fail)
        },
        after,
      )
    },
    [fail],
  )

  const restore = useCallback((snap: api.JobSnapshot) => {
    setState((s) => {
      const round: Round = {
        jobId: snap.job_id,
        kind: snap.kind,
        question: snap.question,
        agents: snap.agents,
      }
      const analysis =
        snap.kind === 'analysis'
          ? {
              fieldId: s.analysis?.fieldId ?? '',
              fieldName: s.analysis?.fieldName ?? '',
              parcel: snap.parcel,
              date: snap.date ?? '',
              layers: snap.layers,
              mlErrors: snap.ml_errors,
              progress: s.analysis?.progress ?? [],
              mlDone: snap.layers.length > 0 || snap.status !== 'running',
            }
          : s.analysis
      return {
        ...s,
        analysis,
        rounds: [...s.rounds.filter((r) => r.jobId !== snap.job_id), round],
        job: {
          id: snap.job_id,
          kind: snap.kind,
          status: snap.status === 'running' ? 'running' : snap.status === 'done' ? 'done' : 'error',
          error: snap.error,
        },
      }
    })
  }, [])

  const refresh = useCallback(() => {
    const sid = ref.current.sid
    if (!sid) return
    api
      .sessionStatus(sid)
      .then((st) => {
        if (st.status === 'active')
          patch({ phase: 'active', idleRemaining: st.idle_remaining, idleAt: Date.now() })
        else if (st.status === 'queued') patch({ phase: 'queued', position: st.position })
        else patch({ phase: 'ended', reason: st.reason, job: null })
      })
      .catch(fail)
  }, [fail, patch])

  const onActive = useCallback(
    (sid: string, st: Extract<api.SessionStatus, { status: 'active' }>) => {
      patch({ phase: 'active', sid, idleRemaining: st.idle_remaining, idleAt: Date.now(), position: null })
      api
        .getAgents()
        .then((agents) => patch({ agents }))
        .catch(() => {})
      if (st.job_id)
        api
          .jobSnapshot(sid, st.job_id)
          .then((snap) => {
            restore(snap)
            if (snap.status === 'running') follow(sid, snap.job_id, snap.last_event_id)
          })
          .catch(() => {})
    },
    [follow, patch, restore],
  )

  const watchQueue = useCallback(
    (sid: string) => {
      closeQueue.current?.()
      closeQueue.current = api.followQueue(
        sid,
        (st) => {
          if (st.status === 'queued') patch({ phase: 'queued', position: st.position })
          else if (st.status === 'active') onActive(sid, st)
          else patch({ phase: 'ended', reason: st.reason })
        },
        () => refresh(),
      )
    },
    [onActive, patch, refresh],
  )

  const connect = useCallback(() => {
    const cur = ref.current
    if (cur.phase === 'joining' || cur.phase === 'active' || cur.phase === 'queued') return
    patch({ ...initial, phase: 'joining' })
    const join = () =>
      api
        .joinSession()
        .then((st) => {
          writeSid(st.session_id)
          patch({ sid: st.session_id })
          if (st.status === 'active') onActive(st.session_id, st)
          else if (st.status === 'queued') {
            patch({ phase: 'queued', position: st.position })
            watchQueue(st.session_id)
          }
        })
        .catch(() => patch({ phase: 'offline' }))
    // A reload keeps the id in sessionStorage; reuse it if the backend still knows it.
    const old = readSid()
    if (!old) return void join()
    api
      .sessionStatus(old)
      .then((st) => {
        patch({ sid: old })
        if (st.status === 'active') onActive(old, st)
        else if (st.status === 'queued') {
          patch({ phase: 'queued', position: st.position })
          watchQueue(old)
        } else join()
      })
      .catch((e) => (e instanceof api.ApiError && e.kind === 'network' ? patch({ phase: 'offline' }) : join()))
  }, [onActive, patch, watchQueue])

  const leave = useCallback(() => {
    const sid = ref.current.sid
    closeQueue.current?.()
    closeJob.current?.()
    writeSid(null)
    if (sid) api.leaveSession(sid).catch(() => {})
    setState({ ...initial, phase: 'ended', reason: 'left' })
  }, [])

  const touch = useCallback(() => {
    const { sid, phase, job } = ref.current
    if (!sid || phase !== 'active' || job?.status === 'running') return
    if (Date.now() - lastTouch.current < 20000) return
    lastTouch.current = Date.now()
    api
      .keepalive(sid)
      .then(() => patch({ idleRemaining: null, idleAt: 0 }))
      .then(refresh)
      .catch(fail)
  }, [fail, patch, refresh])

  const analyze = useCallback(
    async (field: Field, date: string, options: api.RotationOptions = {}) => {
      const sid = ref.current.sid
      if (!sid) return
      try {
        const { job_id } = await api.startAnalysis(sid, field.geometry, date, options, language.current)
        // A new analysis wipes the previous conversation on the backend too.
        patch({
          notice: null,
          job: { id: job_id, kind: 'analysis', status: 'running', error: null },
          idleRemaining: null,
          rounds: [],
          analysis: {
            fieldId: field.id,
            fieldName: field.properties.name,
            parcel: null,
            date,
            layers: [],
            mlErrors: [],
            progress: [],
            mlDone: false,
          },
        })
        follow(sid, job_id)
      } catch (e) {
        fail(e)
      }
    },
    [fail, follow, patch],
  )

  const ask = useCallback(
    async (message: string, agent?: string) => {
      const sid = ref.current.sid
      if (!sid) return
      try {
        const { job_id } = await api.askAgents(sid, message, agent, language.current)
        patch({ notice: null, job: { id: job_id, kind: 'ask', status: 'running', error: null }, idleRemaining: null })
        follow(sid, job_id)
      } catch (e) {
        fail(e)
      }
    },
    [fail, follow, patch],
  )

  const rescore = useCallback(
    async (options: api.RotationOptions) => {
      const sid = ref.current.sid
      if (!sid) throw Error('no session')
      try {
        const res = await api.rescoreRotations(sid, options)
        setState((s) =>
          s.analysis
            ? {
                ...s,
                notice: null,
                analysis: {
                  ...s.analysis,
                  layers: [...s.analysis.layers.filter((l) => !l.name.startsWith('rotasyon_')), ...res.layers],
                },
              }
            : s,
        )
        refresh()
        return res.options
      } catch (e) {
        fail(e)
        throw e
      }
    },
    [fail, refresh],
  )

  // Idle countdown is re-read from the server every 20 s while active and not running a job.
  useEffect(() => {
    if (state.phase !== 'active') return
    const id = setInterval(() => {
      if (ref.current.job?.status !== 'running') refresh()
    }, 20000)
    return () => clearInterval(id)
  }, [state.phase, refresh])

  // Free the slot when the tab closes; otherwise the next user waits up to 5 minutes.
  useEffect(() => {
    const bye = () => {
      const sid = ref.current.sid
      if (sid && (ref.current.phase === 'active' || ref.current.phase === 'queued')) api.leaveBeacon(sid)
    }
    window.addEventListener('pagehide', bye)
    return () => window.removeEventListener('pagehide', bye)
  }, [])

  const value = useMemo<Ctx>(
    () => ({
      ...state,
      connect,
      leave,
      touch,
      analyze,
      ask,
      rescore,
      clearNotice: () => patch({ notice: null }),
    }),
    [state, connect, leave, touch, analyze, ask, rescore, patch],
  )
  return <BackendContext.Provider value={value}>{children}</BackendContext.Provider>
}
