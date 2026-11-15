// 4-week weather outlook for a field: table used on the AI page and the climate page.
import { useEffect, useState } from 'react'
import { CloudSun, Loader2 } from 'lucide-react'
import { climateOutlook, type Outlook } from './api'
import { fieldPoint } from './climate'
import type { Field, Translate } from './model'

export function OutlookTable({ o, t }: { o: Outlook; t: Translate }) {
  const rows: [string, string, string, number][] = [
    ['t2m', t('Ort. sıcaklık', 'Mean temp.'), '°C', 1],
    ['tmin', t('En düşük', 'Min'), '°C', 1],
    ['precip', t('Yağış', 'Rain'), 'mm', 0],
    ['frost_days', t('Donlu gün', 'Frost days'), '', 1],
    ['heat_days', t('>35 °C gün', '>35 °C days'), '', 1],
    ['gdd', 'GDD', '', 0],
  ]
  const f = (v: number, d: number) => v.toLocaleString(undefined, { maximumFractionDigits: d })
  const sk = o.skill.t2m?.[0] ?? 0
  return (
    <div className="outlook">
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th />
              {o.weeks.map((w) => (
                <th key={w.week}>
                  {w.start.slice(5)}
                  <small>
                    {t('hafta', 'week')} {w.week}
                  </small>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map(([k, label, unit, d]) => (
              <tr key={k}>
                <th>
                  {label}
                  {unit && <small>{unit}</small>}
                </th>
                {o.weeks.map((w) => {
                  const v = w.vars[k]
                  return (
                    <td key={w.week} title={t('aralık', 'range') + ' ' + f(v.p10, d) + '–' + f(v.p90, d)}>
                      {f(v.p50, d)}
                      <small>
                        {t('normal', 'normal')} {f(v.normal, d)}
                      </small>
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <small className="note">
        {sk < 0.1
          ? t(
              'Bu noktada tahmin mevsim normalinden belirgin şekilde iyi değil: değerleri “bu dönemin normali” olarak okuyun.',
              'Here the forecast is not clearly better than the seasonal normal: read the values as “normal for this time of year”.',
            )
          : t(
              `1. hafta tahmini mevsim normalinden %${Math.round(sk * 100)} daha isabetli (son 2 yılın geri testi).`,
              `Week 1 is ${Math.round(sk * 100)}% more accurate than the seasonal normal (2-year back-test).`,
            )}{' '}
        {t(
          `${o.history_years} yıllık NASA POWER geçmişi, son veri ${o.issued_from}. Üzerine gelince %10–%90 aralığı görünür.`,
          `${o.history_years}-year NASA POWER history, latest data ${o.issued_from}. Hover for the 10–90% range.`,
        )}
      </small>
    </div>
  )
}


/** Climate page panel: fetches the outlook for the selected field (backend caches it per day). */
export function OutlookPanel({ field, t }: { field: Field | undefined; t: Translate }) {
  const [o, setO] = useState<Outlook | null>(null)
  const [state, setState] = useState<'idle' | 'loading' | 'ok' | 'failed'>('idle')
  const [msg, setMsg] = useState<string | null>(null)
  const point = field ? fieldPoint(field) : null
  const key = point ? point[0].toFixed(1) + ',' + point[1].toFixed(1) : ''
  useEffect(() => {
    if (!point) return setState('idle')
    let cancelled = false
    setState('loading')
    climateOutlook(point[1], point[0])
      .then((r) => {
        if (cancelled) return
        setO(r)
        setState('ok')
      })
      .catch((e) => {
        if (cancelled) return
        setMsg((e as { userText?: string }).userText ?? null)
        setState('failed')
      })
    return () => {
      cancelled = true
    }
  }, [key])
  if (state === 'idle') return null
  return (
    <section className="outlook-panel">
      <h2>
        <CloudSun size={20} /> {t('Önümüzdeki 4 hafta', 'Next 4 weeks')}
      </h2>
      {state === 'loading' && (
        <p role="status">
          <Loader2 size={14} className="spin" /> {t('15 yıllık geçmişten hesaplanıyor…', 'Computing from 15 years of history…')}
        </p>
      )}
      {state === 'failed' && <p>{msg ?? t('Hava görünümü hesaplanamadı.', 'Outlook unavailable.')}</p>}
      {state === 'ok' && o && <OutlookTable o={o} t={t} />}
    </section>
  )
}
