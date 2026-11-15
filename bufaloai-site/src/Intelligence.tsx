// AI page: field analysis (ML) → N bots side by side → follow-up questions → farmer's own rotation.
// Everything numeric (risk, confidence level, ranking) comes from the backend; this only displays it.
// Backend content is bilingual (`*_en` fields); `pick` chooses by the UI language.
import { useEffect, useMemo, useState, type Dispatch, type SetStateAction } from 'react'
import {
  AlertTriangle,
  BrainCircuit,
  Check,
  CircleDashed,
  Info,
  Loader2,
  LogOut,
  Pencil,
  Play,
  RefreshCw,
  Send,
  ShieldCheck,
  Sprout,
  X,
} from 'lucide-react'
import { useBackend, profileOf, type Analysis } from './backend'
import type { Catalog, Layer, Meta, Outlook, Progress, RotationOptions } from './api'
import { FieldForm } from './FieldForm'
import { CropSuggestions } from './FarmerTools'
import { OutlookTable } from './Outlook'
import { farmerOptions, useCatalog, useCustomCrops, useCustomRotations } from './farmerData'
import { COMPONENTS, LEVEL, layerName, pick, pickList, serverText, unitText } from './i18n'
import { hectares, type Field, type Language, type Translate } from './model'

type Props = {
  t: Translate
  lang: Language
  fields: Field[]
  setFields: Dispatch<SetStateAction<Field[]>>
  selected: string
  select: (id: string) => void
  goFields: () => void
  goPlanner: () => void
}

const today = () => new Date().toISOString().slice(0, 10)
const levelText = (lang: Language, l: string) => (LEVEL[l] ? LEVEL[l][lang === 'en' ? 1 : 0] : l)

export function ConfidenceBadge({ layer, t }: { layer: Pick<Layer, 'confidence' | 'confidence_level'>; t: Translate }) {
  // The level comes from the backend; we only map it to a colour.
  const lvl = layer.confidence_level
  const cls = lvl === 'yüksek' ? 'high' : lvl === 'orta' ? 'mid' : 'low'
  const name = cls === 'high' ? t('yüksek', 'high') : cls === 'mid' ? t('orta', 'medium') : t('düşük', 'low')
  return (
    <span
      className={'conf conf-' + cls}
      title={t('Model eminliği', 'Model confidence') + ': ' + Math.round(layer.confidence * 100) + '%'}
    >
      {cls === 'low' ? <AlertTriangle size={12} /> : <ShieldCheck size={12} />}
      {t('eminlik', 'confidence')} {name} · {Math.round(layer.confidence * 100)}%
    </span>
  )
}

function Severity({ s, t }: { s: string; t: Translate }) {
  const cls = s === 'yüksek' ? 'low' : s === 'orta' ? 'mid' : 'high'
  return (
    <span className={'conf conf-' + cls}>
      {s === 'yüksek' ? t('yüksek', 'high') : s === 'orta' ? t('orta', 'medium') : t('düşük', 'low')}
    </span>
  )
}

function progressSteps(progress: Progress[], t: Translate) {
  const names: Record<string, string> = {
    'data:weather': t('İklim verisi (NASA POWER)', 'Climate data (NASA POWER)'),
    'data:ndvi': t('Uydu bitki örtüsü (MODIS NDVI)', 'Satellite vegetation (MODIS NDVI)'),
    'data:soil': t('Toprak ve eğim (SSURGO ya da SoilGrids + yükseklik)', 'Soil and slope (SSURGO or SoilGrids + elevation)'),
    'data:outlook': t('Hava görünümü (15 yıllık NASA POWER)', 'Weather outlook (15-year NASA POWER)'),
    'data:crop_history': t('Ürün geçmişi (CDL)', 'Crop history (CDL)'),
    features: t('Özellikler', 'Features'),
    model: t('NDVI tahmini (XGBoost)', 'NDVI forecast (XGBoost)'),
    rotations: t('Risk ve rotasyon puanları', 'Risk and rotation scores'),
  }
  const steps = new Map<string, string>()
  for (const p of progress) {
    if (p.stage === 'data' && p.status === 'cache') {
      steps.set('cache', 'done')
      continue
    }
    const key = p.stage === 'data' ? 'data:' + p.source : p.stage
    steps.set(key, String(p.status ?? 'started'))
  }
  return [...steps].map(([k, status]) => ({
    key: k,
    label: k === 'cache' ? t('Bugünkü önbellekten okundu', "Read from today's cache") : (names[k] ?? k),
    status,
  }))
}

function Steps({ a, running, t }: { a: Analysis; running: boolean; t: Translate }) {
  const steps = progressSteps(a.progress, t)
  return (
    <ol className="steps" aria-live="polite">
      <li className="done">
        <Check size={14} />
        {t('Parsel doğrulandı', 'Parcel validated')}
        {a.parcel && <small>{a.parcel.area_ha.toLocaleString(undefined, { maximumFractionDigits: 2 })} ha</small>}
      </li>
      {steps.map((s) => (
        <li key={s.key} className={s.status === 'failed' ? 'failed' : s.status === 'started' ? 'busy' : 'done'}>
          {s.status === 'failed' ? (
            <X size={14} />
          ) : s.status === 'started' && running ? (
            <Loader2 size={14} className="spin" />
          ) : (
            <Check size={14} />
          )}
          {s.label}
          {s.key === 'data:ndvi' && s.status === 'started' && (
            <small>{t('2 dakikaya kadar sürebilir', 'can take up to 2 minutes')}</small>
          )}
        </li>
      ))}
      {running && !a.mlDone && !steps.length && (
        <li className="busy">
          <Loader2 size={14} className="spin" />
          {t('Analiz başlıyor…', 'Starting analysis…')}
        </li>
      )}
    </ol>
  )
}

function fmt(n: number, unit: string) {
  const d = Math.abs(n) < 2 ? 2 : Math.abs(n) < 100 ? 1 : 0
  return n.toLocaleString(undefined, { maximumFractionDigits: d }) + (unit ? ' ' + unit : '')
}

function LayerCard({ layer, t, lang }: { layer: Layer; t: Translate; lang: Language }) {
  const det = layer.details ?? {}
  // Component scores are 0–1 from the backend; shown on the same 0–100 scale as the overall risk.
  const comps = (det as { risk?: { components?: Record<string, { score: number | null; level: string }> } })
    .risk?.components
  const outlook = (det as { outlook?: Outlook }).outlook
  const forecast = (det as { forecast?: { target_week?: string; is_nowcast?: boolean } }).forecast
  const unit = unitText(lang, layer.unit)
  const label = lang === 'en' ? ((det as { label_en?: string }).label_en ?? layer.label) : layer.label
  const note = lang === 'en' ? (det as { note_en?: string }).note_en : layer.note
  return (
    <section className={'layer-card' + (layer.confidence_level === 'düşük' ? ' is-low' : '')}>
      <header>
        <h3>{label}</h3>
        <ConfidenceBadge layer={layer} t={t} />
      </header>
      <strong>{fmt(layer.mean, unit)}</strong>
      {layer.min !== layer.max && (
        <small>
          {t('aralık', 'range')} {fmt(layer.min, '')} – {fmt(layer.max, unit)}
        </small>
      )}
      {forecast?.target_week && (
        <small>
          {forecast.is_nowcast
            ? t('Uydu gecikmesini kapatan şimdiki durum tahmini', 'Nowcast bridging the satellite delay')
            : t('Hedef hafta', 'Target week')}{' '}
          · {forecast.target_week}
        </small>
      )}
      {comps && (
        <ul className="components">
          {Object.entries(comps).map(([k, v]) => (
            <li key={k}>
              <span>{COMPONENTS[k] ? COMPONENTS[k][lang === 'en' ? 1 : 0] : k.replace(/_/g, ' ')}</span>
              <b>{v.score === null ? '—' : Math.round(v.score * 100)}</b>
              <Severity s={v.level} t={t} />
            </li>
          ))}
        </ul>
      )}
      {outlook && <OutlookTable o={outlook} t={t} />}
      {note && !outlook && !comps && <small className="note">{note}</small>}
    </section>
  )
}

function RotationCard({ layer, t, lang }: { layer: Layer; t: Translate; lang: Language }) {
  const d = layer.details ?? {}
  const tag =
    layer.name === 'rotasyon_mevcut'
      ? t('Mevcut rotasyonunuz', 'Your current rotation')
      : layer.name === 'rotasyon_secilen'
        ? t('Sizin seçiminiz', 'Your choice')
        : layer.name.startsWith('rotasyon_ozel')
          ? t('Sizin rotasyonunuz', 'Your rotation') + (d.rank ? ' · ' + t('sıra', 'rank') + ' ' + d.rank : '')
          : t('Öneri', 'Suggestion') + ' ' + layer.name.split('_')[1]
  const src =
    d.source === 'otomatik'
      ? t('otomatik üretildi', 'auto-generated')
      : d.source === 'çiftçi'
        ? t('sizin', 'yours')
        : t('katalog', 'catalogue')
  const seq = pickList(lang, d, 'sequence_names')
  const why = [...pickList(lang, d, 'why'), ...pickList(lang, d, 'benefits')]
  const review = pickList(lang, d, 'risks_for_local_review')
  return (
    <section className={'rotation-card' + (layer.confidence_level === 'düşük' ? ' is-low' : '')}>
      <header>
        <small>
          {tag} · <i className={'src src-' + (d.source ?? 'katalog')}>{src}</i>
        </small>
        <ConfidenceBadge layer={layer} t={t} />
      </header>
      <h3>{pick(lang, d, 'name') || layer.label}</h3>
      {!!seq.length && <p className="sequence">{seq.join(' → ')}</p>}
      <div className="scores">
        <span>
          {t('Puan', 'Score')} <b>{Math.round(layer.mean)}</b>/100
        </span>
        {d.risk !== undefined && (
          <span>
            {t('Risk', 'Risk')} <b>{Math.round(d.risk * 100)}%</b>
          </span>
        )}
        {d.delta_vs_existing && (
          <span className={d.delta_vs_existing.score >= 0 ? 'up' : 'down'}>
            {t('mevcuda göre', 'vs current')} {d.delta_vs_existing.score >= 0 ? '+' : ''}
            {Math.round(d.delta_vs_existing.score * 100)}
          </span>
        )}
      </div>
      {d.economics && (
        <p className="econ-line">
          {t('Sizin hesabınızla brüt kâr', 'Gross margin, your figures')}:{' '}
          <b>{d.economics.mean_gross_margin_per_ha.toLocaleString(undefined, { maximumFractionDigits: 0 })}</b>
          {' /ha · '}
          {t('sezon ortalaması, tahmini kayıp düşüldü', 'per-season mean, estimated losses subtracted')}
        </p>
      )}
      {!!d.damages?.length && (
        <>
          <h4>{t('Olası zararlar (tahmini)', 'Possible damages (estimated)')}</h4>
          <ul className="damages">
            {d.damages.map((x, i) => (
              <li key={i}>
                <Severity s={x.severity} t={t} />
                <span>
                  <b>{pick(lang, x, 'risk')}</b> · {pick(lang, x, 'affects')}
                  {x.yield_loss_pct && (
                    <em>
                      {' '}
                      {t('tahmini verim kaybı', 'est. yield loss')} %{x.yield_loss_pct[0]}–{x.yield_loss_pct[1]}
                    </em>
                  )}
                  <small>{pick(lang, x, 'impact')}</small>
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
      {!!why.length && (
        <details>
          <summary>{t('Neden ve faydalar', 'Why and benefits')}</summary>
          <ul>
            {why.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ul>
          {!!review.length && (
            <>
              <h4>{t('Yerelde kontrol edin', 'Check locally')}</h4>
              <ul>
                {review.map((s, i) => (
                  <li key={i}>{s}</li>
                ))}
              </ul>
            </>
          )}
        </details>
      )}
    </section>
  )
}

function RotationForm({
  t,
  lang,
  field,
  busy,
  catalog,
  weights,
  setWeights,
  onSubmit,
}: {
  t: Translate
  lang: Language
  field: Field | undefined
  busy: boolean
  catalog: Catalog
  weights: Record<string, number>
  setWeights: Dispatch<SetStateAction<Record<string, number>>>
  onSubmit: (o: RotationOptions) => Promise<unknown>
}) {
  const [chosen, setChosen] = useState('')
  const [sending, setSending] = useState(false)
  return (
    <form
      className="rotation-form"
      onSubmit={async (e) => {
        e.preventDefault()
        setSending(true)
        try {
          await onSubmit({
            chosen_rotation: chosen || undefined,
            existing_rotation: field?.properties.existing_rotation || undefined,
            priorities: weights,
            profile: field ? profileOf(field.properties) : undefined,
          })
        } catch {
        } finally {
          setSending(false)
        }
      }}
    >
      <label>
        {t('Rotasyon', 'Rotation')}
        <select value={chosen} onChange={(e) => setChosen(e.target.value)}>
          <option value="">{t('Yalnızca öncelikleri güncelle', 'Only update priorities')}</option>
          {catalog.rotations.map((r) => (
            <option key={r.id} value={r.id}>
              {pick(lang, r, 'name')}
            </option>
          ))}
        </select>
      </label>
      <fieldset>
        <legend>{t('Öncelikleriniz (0–5)', 'Your priorities (0–5)')}</legend>
        {catalog.priorities.map((p) => (
          <label key={p.id} className="slider">
            <span>{pick(lang, p, 'label')}</span>
            <input
              type="range"
              min={0}
              max={5}
              step={0.5}
              value={weights[p.id] ?? p.default}
              onChange={(e) => setWeights((w) => ({ ...w, [p.id]: +e.target.value }))}
            />
            <b>{weights[p.id] ?? p.default}</b>
          </label>
        ))}
      </fieldset>
      <button className="primary" disabled={busy || sending}>
        <RefreshCw size={15} className={sending ? 'spin' : ''} />
        {t('Yeniden puanla', 'Re-score')}
      </button>
    </form>
  )
}

function SessionPanel({ t }: { t: Translate }) {
  const b = useBackend()
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    if (b.phase !== 'active' || b.idleRemaining === null) return
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [b.phase, b.idleRemaining])
  if (b.phase === 'active') {
    const left = b.idleRemaining === null ? null : Math.max(0, b.idleRemaining - (now - b.idleAt) / 1000)
    if (left === null || left > 60) return null
    return (
      <div className="session-bar warn" role="alert">
        <AlertTriangle size={16} />
        {t(
          `Hareketsizlik nedeniyle oturum ${Math.ceil(left)} sn içinde kapanacak.`,
          `Session closes in ${Math.ceil(left)} s due to inactivity.`,
        )}
        <button className="outline" onClick={b.touch}>
          {t('Devam et', 'Stay')}
        </button>
      </div>
    )
  }
  return null
}

function Gate({ t }: { t: Translate }) {
  const b = useBackend()
  const reasons: Record<string, string> = {
    idle: t('5 dakika hareketsiz kaldığınız için oturum kapandı.', 'Session closed after 5 minutes of inactivity.'),
    left: t('Oturumdan çıktınız.', 'You left the session.'),
    queue_timeout: t('Sıradayken bağlantı koptu.', 'Connection dropped while in the queue.'),
  }
  return (
    <div className="gate">
      <BrainCircuit className="ai-icon" size={42} strokeWidth={1.2} />
      {b.phase === 'queued' && (
        <>
          <h2>
            {t('Sıranız', 'Your place in line')}: {b.position}
          </h2>
          <p>
            {t(
              'Model aynı anda tek kullanıcıya hizmet veriyor. Bu sayfayı açık tutun; sıra gelince çalışma alanı açılır.',
              'The model serves one user at a time. Keep this page open; the workspace opens when it is your turn.',
            )}
          </p>
          <Loader2 className="spin" />
        </>
      )}
      {(b.phase === 'idle' || b.phase === 'joining') && (
        <>
          <p>{t('Sunucuya bağlanılıyor…', 'Connecting to the server…')}</p>
          <Loader2 className="spin" />
        </>
      )}
      {b.phase === 'offline' && (
        <>
          <p>{t('Sunucuya ulaşılamadı. Backend çalışıyor olmalı.', 'Cannot reach the server. The backend must be running.')}</p>
          <button className="primary" onClick={b.connect}>
            <RefreshCw size={15} />
            {t('Tekrar dene', 'Try again')}
          </button>
        </>
      )}
      {b.phase === 'ended' && (
        <>
          <p>{reasons[b.reason ?? ''] ?? t('Oturum sona erdi.', 'The session ended.')}</p>
          <button className="primary" onClick={b.connect}>
            <RefreshCw size={15} />
            {t('Yeniden bağlan', 'Reconnect')}
          </button>
        </>
      )}
    </div>
  )
}

function AskBox({ t, lang }: { t: Translate; lang: Language }) {
  const b = useBackend()
  const [text, setText] = useState('')
  const [target, setTarget] = useState('')
  const running = b.job?.status === 'running'
  return (
    <form
      className="ask"
      onSubmit={(e) => {
        e.preventDefault()
        const m = text.trim()
        if (!m || running) return
        b.ask(m, target || undefined)
        setText('')
      }}
    >
      <textarea
        value={text}
        maxLength={2000}
        rows={2}
        placeholder={t('Botlara bir soru sorun…', 'Ask the advisors a question…')}
        onChange={(e) => {
          setText(e.target.value)
          b.touch()
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault()
            e.currentTarget.form?.requestSubmit()
          }
        }}
      />
      <div>
        <select aria-label={t('Hedef bot', 'Target advisor')} value={target} onChange={(e) => setTarget(e.target.value)}>
          <option value="">{t('Tüm botlara', 'All advisors')}</option>
          {b.agents.map((a) => (
            <option key={a.id} value={a.id}>
              {t('Yalnız', 'Only')} {pick(lang, a, 'name')}
            </option>
          ))}
        </select>
        <button className="primary" disabled={running || !text.trim()}>
          <Send size={15} />
          {t('Sor', 'Ask')}
        </button>
      </div>
    </form>
  )
}

function Agents({ t, lang }: { t: Translate; lang: Language }) {
  const b = useBackend()
  const agents = b.agents.length ? b.agents : (b.rounds[0]?.agents ?? [])
  if (!b.rounds.length) return null
  return (
    <div className="agents" style={{ ['--cols' as string]: Math.max(1, agents.length) }}>
      {agents.map((a) => (
        <section key={a.id} className="agent">
          <header>
            <h3>{pick(lang, a, 'name')}</h3>
            <div className="chips">
              {a.layers.map((l) => (
                <span key={l}>{layerName(lang, l)}</span>
              ))}
            </div>
          </header>
          {b.rounds.map((r) => {
            const s = r.agents.find((x) => x.id === a.id)
            if (!s) return null
            return (
              <div key={r.jobId} className="turn">
                {r.question && <p className="question">{r.question}</p>}
                {s.text ? (
                  <p className="answer">{s.text}</p>
                ) : s.status === 'running' ? (
                  <p className="answer typing">
                    <CircleDashed size={14} className="spin" />
                    {t('yazıyor…', 'writing…')}
                  </p>
                ) : null}
                {s.status === 'error' && <p className="agent-error">{serverText(lang, s.error)}</p>}
              </div>
            )
          })}
        </section>
      ))}
    </div>
  )
}

function MetaWarnings({ meta, lang, t }: { meta: Meta; lang: Language; t: Translate }) {
  const warnings = pickList(lang, meta, 'warnings')
  const source = (k: string) =>
    ({ weather: t('iklim', 'weather'), ndvi: 'NDVI' })[k] ?? k
  return (
    <div className="meta">
      {!!warnings.length && (
        <ul className="warnings">
          {warnings.map((w, i) => (
            <li key={i}>
              <AlertTriangle size={14} />
              {w}
            </li>
          ))}
        </ul>
      )}
      {meta.data_as_of && (
        <small>
          {t('Veri tarihi', 'Data as of')}:{' '}
          {Object.entries(meta.data_as_of)
            .filter(([, v]) => v)
            .map(([k, v]) => source(k) + ' ' + v)
            .join(' · ')}
        </small>
      )}
    </div>
  )
}

export default function Intelligence({ t, lang, fields, setFields, selected, select, goFields, goPlanner }: Props) {
  const b = useBackend()
  const [date, setDate] = useState(today)
  const [editing, setEditing] = useState<Field | null>(null)
  const [crops, setCrops] = useCustomCrops()
  const [myRotations] = useCustomRotations()
  const { catalog, failed: catalogFailed } = useCatalog()
  const [weights, setWeights] = useState<Record<string, number>>({})
  useEffect(() => {
    b.connect()
  }, [])
  useEffect(() => {
    if (catalog && !Object.keys(weights).length)
      setWeights(Object.fromEntries(catalog.priorities.map((p) => [p.id, p.default])))
  }, [catalog])
  // Farmer's crops and saved rotations go with every analysis and re-score (they live in this browser).
  const extra = () => farmerOptions(catalog, crops, myRotations)
  const field = fields.find((f) => f.id === selected) ?? fields[0]
  const running = b.job?.status === 'running'
  const a = b.analysis
  const meta = useMemo(() => a?.layers.find((l) => l.details?._meta)?.details?._meta, [a?.layers])
  const plain = a?.layers.filter((l) => !l.name.startsWith('rotasyon_')) ?? []
  const rotations = a?.layers.filter((l) => l.name.startsWith('rotasyon_')) ?? []
  const analysedField = fields.find((f) => f.id === a?.fieldId)
  const gaps = pickList(lang, meta, 'information_gaps')

  const check = (f: Field): string | null => {
    if (f.geometry.coordinates.length > 1)
      return t('Delikli poligonlar desteklenmiyor.', 'Polygons with holes are not supported.')
    const ha = hectares(f)
    if (ha < 0.01 || ha > 5000) return t('Alan 0,01–5000 ha olmalı.', 'Area must be 0.01–5000 ha.')
    if (f.geometry.coordinates[0].length > 1001) return t('En fazla 1000 köşe olabilir.', 'At most 1000 vertices.')
    return null
  }
  const problem = field ? check(field) : null
  const profile = field ? profileOf(field.properties) : undefined
  const known = profile ? Object.keys(profile).length : 0
  const runAnalysis = () =>
    field &&
    b.analyze(field, date, {
      existing_rotation: field.properties.existing_rotation || undefined,
      profile,
      ...extra(),
    })

  return (
    <article className="analytic intelligence" onPointerDown={b.touch} onKeyDown={b.touch}>
      <div className="analytics-heading">
        <div>
          <h1>{t('Yapay zekâ istihbaratı', 'AI intelligence')}</h1>
          <p>
            {t(
              'Parselinizi NASA uydu ve iklim verisiyle analiz edin; iki danışman bot sonuçları yorumlar, sonra sorularınızı yanıtlar.',
              'Analyse your parcel with NASA satellite and climate data; two advisor bots interpret the results and answer your questions.',
            )}
          </p>
        </div>
        {b.phase === 'active' && (
          <button className="outline" onClick={b.leave} disabled={running}>
            <LogOut size={15} />
            {t('Oturumu kapat', 'Leave session')}
          </button>
        )}
      </div>
      <SessionPanel t={t} />
      {b.notice && (
        <div className="session-bar" role="alert">
          <Info size={16} />
          {b.notice}
          <button className="iconbtn" aria-label={t('Kapat', 'Close')} onClick={b.clearNotice}>
            <X size={15} />
          </button>
        </div>
      )}
      {b.phase !== 'active' ? (
        <Gate t={t} />
      ) : (
        <>
          <div className="filters run-bar">
            <label>
              {t('Tarla', 'Field')}
              <select value={field?.id ?? ''} onChange={(e) => select(e.target.value)} disabled={!fields.length}>
                {!fields.length && <option value="">{t('Tarla yok', 'No fields')}</option>}
                {fields.map((f) => (
                  <option key={f.id} value={f.id}>
                    {f.properties.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {t('Tarih', 'Date')}
              <input type="date" value={date} max={today()} onChange={(e) => setDate(e.target.value)} />
            </label>
            {field && (
              <button type="button" className="outline farmer-btn" onClick={() => setEditing(field)}>
                <Pencil size={14} />
                {t('Çiftçi bilgisi', "Farmer's data")} · {known}/4
              </button>
            )}
            <button type="button" className="outline" onClick={goPlanner}>
              <Sprout size={14} />
              {t('Ürünler ve rotasyonlar', 'Crops & rotations')} · {crops.length}/{myRotations.length}
            </button>
            {fields.length ? (
              <button className="primary" disabled={running || !!problem || !field} onClick={runAnalysis}>
                {running && b.job?.kind === 'analysis' ? <Loader2 size={15} className="spin" /> : <Play size={15} />}
                {t('Analiz et', 'Analyse')}
              </button>
            ) : (
              <button className="primary" onClick={goFields}>
                {t('Önce bir tarla çizin', 'Draw a field first')}
              </button>
            )}
          </div>
          {problem && <p className="agent-error">{problem}</p>}
          <p className="hint">
            {t(
              'Tarih yalnızca kayıt içindir; analiz her zaman bugüne kadarki güncel veriyi kullanır.',
              'The date is for the record only; analysis always uses the latest data up to today.',
            )}
          </p>

          {a && (
            <>
              <h2 className="result-title">
                {a.fieldName || analysedField?.properties.name}
                {a.parcel && (
                  <small>
                    {' '}
                    · {a.parcel.area_ha.toLocaleString(undefined, { maximumFractionDigits: 2 })} ha ·{' '}
                    {a.parcel.centroid[1].toFixed(3)}, {a.parcel.centroid[0].toFixed(3)}
                  </small>
                )}
              </h2>
              {(!a.mlDone || running) && b.job?.kind === 'analysis' && <Steps a={a} running={running} t={t} />}
              {b.job?.status === 'error' && b.job.error && (
                <div className="session-bar warn" role="alert">
                  <AlertTriangle size={16} />
                  {serverText(lang, b.job.error)}
                  <button className="outline" disabled={!field} onClick={runAnalysis}>
                    {t('Tekrar dene', 'Retry')}
                  </button>
                </div>
              )}
              {!!a.mlErrors.length && (
                <ul className="warnings">
                  {a.mlErrors.map((e, i) => (
                    <li key={i}>
                      <AlertTriangle size={14} />
                      {serverText(lang, e)}
                    </li>
                  ))}
                </ul>
              )}
              {meta && <MetaWarnings meta={meta} lang={lang} t={t} />}
              {!!plain.length && (
                <div className="layer-grid">
                  {plain.map((l) => (
                    <LayerCard key={l.name} layer={l} t={t} lang={lang} />
                  ))}
                </div>
              )}
              <Agents t={t} lang={lang} />
              {lang === 'en' && !!b.rounds.length && (
                <p className="hint">
                  Advisors are asked to answer in English. Their data comes from the Turkish-language model, so an
                  occasional Turkish term may slip through.
                </p>
              )}
              {!!b.rounds.length && <AskBox t={t} lang={lang} />}
              {!!rotations.length && (
                <>
                  <h2>{t('Rotasyonlar', 'Rotations')}</h2>
                  {!!gaps.length && (
                    <details className="gaps">
                      <summary>
                        {t('Bilgi boşlukları', 'Information gaps')} ({gaps.length}) ·{' '}
                        {t('çiftçi bilgisi ekleyerek eminliği artırın', "add farmer's data to raise confidence")}
                      </summary>
                      <ul>
                        {gaps.map((g, i) => (
                          <li key={i}>{g}</li>
                        ))}
                      </ul>
                    </details>
                  )}
                  <div className="rotation-grid">
                    {rotations.map((l) => (
                      <RotationCard key={l.name} layer={l} t={t} lang={lang} />
                    ))}
                  </div>
                  <section className="tools">
                    <h3>{t('Kendi rotasyonumu değerlendir', 'Evaluate my own rotation')}</h3>
                    {catalogFailed && (
                      <p className="hint">{t('Rotasyon kataloğu alınamadı.', 'Rotation catalogue unavailable.')}</p>
                    )}
                    {catalog && (
                      <RotationForm
                        t={t}
                        lang={lang}
                        field={analysedField}
                        busy={running}
                        catalog={catalog}
                        weights={weights}
                        setWeights={setWeights}
                        onSubmit={(o) => b.rescore({ ...o, ...extra() })}
                      />
                    )}
                    <p className="hint">
                      {t(
                        'Kendi ürünlerinizi eklemek, hazır kütüphaneden seçmek ya da sezon sezon rotasyon kurmak için ',
                        'To add your own crops, pick from the library or build a rotation season by season, open ',
                      )}
                      <button type="button" className="linkish" onClick={goPlanner}>
                        {t('Ürünler ve rotasyonlar', 'Crops & rotations')}
                      </button>
                      .
                    </p>
                  </section>
                  {catalog && (
                    <section className="tools">
                      <h3>{t('Bu tarlaya önerilen ürünler', 'Crops suggested for this field')}</h3>
                      <CropSuggestions
                        t={t}
                        lang={lang}
                        sid={b.sid}
                        analysisKey={a.mlDone ? a.fieldId + a.date : null}
                        catalog={catalog}
                        crops={crops}
                        setCrops={setCrops}
                      />
                      {!!crops.length && (
                        <p className="hint">
                          {t(
                            'Eklenen ürünler bir sonraki “Analiz et” ya da “Yeniden puanla” ile rotasyon adaylarına girer.',
                            'Added crops join the rotation candidates from the next “Analyse” or “Re-score”.',
                          )}
                        </p>
                      )}
                    </section>
                  )}
                </>
              )}
              {meta && pick(lang, meta, 'disclaimer') && <p className="disclaimer">{pick(lang, meta, 'disclaimer')}</p>}
            </>
          )}
        </>
      )}
      {editing && (
        <FieldForm
          field={editing}
          t={t}
          close={() => setEditing(null)}
          commit={(f) => {
            setFields((prev) => prev.map((p) => (p.id === f.id ? f : p)))
            setEditing(null)
          }}
        />
      )}
    </article>
  )
}
