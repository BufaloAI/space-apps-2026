import { useMemo, useState } from 'react'
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  BarChart,
  Bar,
} from 'recharts'
import {
  Download,
  FlaskConical,
  Sprout,
  Activity,
  Wind,
  Satellite,
  Loader2,
} from 'lucide-react'
import {
  aggregate,
  indicators,
  metrics,
  useClimate,
  type Metric,
} from './climate'
import { download, type Field, type Page, type Translate } from './model'
import { OutlookPanel } from './Outlook'
export default function Analytics({
  page,
  t,
  lang,
  fields,
  selected,
  select,
}: {
  page: Page
  t: Translate
  lang: string
  fields: Field[]
  selected: string
  select: (id: string) => void
}) {
  const thisYear = new Date().getFullYear()
  const yearOptions = Array.from({ length: 6 }, (_, i) => thisYear - i)
  const [year, setYear] = useState(thisYear - 1),
    [metric, setMetric] = useState<Metric>('temperature'),
    [period, setPeriod] = useState('month'),
    [base, setBase] = useState(10),
    [threshold, setThreshold] = useState(25),
    [month, setMonth] = useState(1)
  const field = fields.find((f) => f.id === selected)
  const span =
    page === 'climate' && period === 'year'
      ? Array.from({ length: 5 }, (_, i) => year - 4 + i)
      : [year]
  const climate = useClimate(field, span)
  const { rows, live } = climate.rowsFor(year)
  const isLive = climate.state.kind === 'live'
  const locale = lang === 'tr' ? 'tr-TR' : 'en-US'
  const fmt = (n: number | null, d = 1) =>
    n === null || !Number.isFinite(n)
      ? '—'
      : n.toLocaleString(locale, { maximumFractionDigits: d })
  const mon = (i: number) =>
    new Intl.DateTimeFormat(locale, { month: 'short', timeZone: 'UTC' }).format(
      Date.UTC(year, i, 1),
    )
  const label = (k: Metric) => t(metrics[k][0], metrics[k][1])
  const unit = (k: Metric) =>
    k === 'solar' ? t('MJ/m²/gün', 'MJ/m²/day') : metrics[k][2]
  const data = useMemo(
    () =>
      period === 'month'
        ? Array.from({ length: 12 }, (_, i) => ({
            label: mon(i),
            value: +aggregate(
              rows.filter((r) => +r.date.slice(5, 7) === i + 1),
              metric,
            ).toFixed(3),
          }))
        : Array.from({ length: 5 }, (_, i) => ({
            label: String(year - 4 + i),
            value: +aggregate(
              climate.rowsFor(year - 4 + i).rows,
              metric,
            ).toFixed(3),
          })),
    [rows, metric, period, year, selected, locale, climate.state],
  )
  const keys = Object.keys(metrics) as Metric[],
    daily = rows.filter((r) => +r.date.slice(5, 7) === month)
  const names =
    page === 'climate'
      ? t('İklim analizi', 'Climate analysis')
      : page === 'data'
        ? t('Veri gezgini', 'Data explorer')
        : t('Tarımsal göstergeler', 'Agricultural indicators')
  const calculated = indicators(rows, base, threshold)
  function exportCSV() {
    const data =
      'date,' +
      keys.map((k) => k + ' (' + unit(k) + ')').join(',') +
      '\r\n' +
      rows
        .map((r) =>
          [r.date, ...keys.map((k) => (r[k] === null ? '' : r[k]!.toFixed(3)))].join(','),
        )
        .join('\r\n')
    download(
      'BUFALOAI-' +
        (live ? 'POWER-' + live.lat + '_' + live.lon : 'DEMO-' + (field?.id || 'global')) +
        '-' +
        year +
        '.csv',
      '\uFEFF' + data,
      'text/csv;charset=utf-8',
    )
  }
  return (
    <article className="analytic">
      {climate.state.kind === 'loading' ? (
        <div className="demo-label" role="status">
          <Loader2 size={14} className="spin" />
          {t('NASA POWER verisi alınıyor…', 'Fetching NASA POWER data…')}
        </div>
      ) : isLive && live ? (
        <div className="demo-label live">
          <Satellite size={14} />
          NASA POWER · {live.lat.toFixed(1)}°, {live.lon.toFixed(1)}°
          {live.stale && ' · ' + t('eski kopya', 'stale copy')}
        </div>
      ) : (
        <div className="demo-label">
          <FlaskConical size={14} />
          {t('Gösterim verisi', 'Demonstration data')}
        </div>
      )}
      <div className="analytics-heading">
        <div>
          <h1>{names}</h1>
          <p>
            {isLive && live
              ? t(
                  `Tarla merkezi için NASA POWER günlük model/uydu verisi (~50 km ızgara). ${
                    live.complete ? 'Yıl tamamlandı.' : 'Son veri: ' + (live.last_date ?? '—') + '.'
                  } Tarla ölçümü değildir; yerel ölçümünüz varsa onu esas alın.`,
                  `NASA POWER daily model/satellite data for the field centre (~50 km grid). ${
                    live.complete ? 'Full year.' : 'Latest data: ' + (live.last_date ?? '—') + '.'
                  } Not an on-field measurement; prefer your own readings where you have them.`,
                )
              : climate.state.kind === 'failed'
                ? (climate.state.message ??
                    t('Gerçek veri alınamadı.', 'Live data unavailable.')) +
                  ' ' +
                  t(
                    'Aşağıdaki değerler sentetiktir.',
                    'Values below are synthetic.',
                  )
                : field
                  ? t('Veri hazırlanıyor.', 'Preparing data.')
                  : t(
                      'Sentetik günlük seri. Gerçek NASA POWER verisi için bir tarla seçin.',
                      'Synthetic daily series. Select a field for real NASA POWER data.',
                    )}
          </p>
        </div>
        {page === 'data' && (
          <button className="outline" onClick={exportCSV}>
            <Download size={16} />
            CSV
          </button>
        )}
      </div>
      <div className="filters">
        <label>
          {t('Analiz alanı', 'Analysis area')}
          <select value={selected} onChange={(e) => select(e.target.value)}>
            <option value="">{t('Küresel örnek', 'Global example')}</option>
            {fields.map((f) => (
              <option key={f.id} value={f.id}>
                {f.properties.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          {t('Yıl', 'Year')}
          <select value={year} onChange={(e) => setYear(+e.target.value)}>
            {yearOptions.map((n) => (
              <option key={n}>{n}</option>
            ))}
          </select>
        </label>
      </div>
      {page === 'climate' && (
        <>
          <div className="metric-tabs">
            {keys.map((k) => (
              <button
                className={k === metric ? 'active' : ''}
                key={k}
                onClick={() => setMetric(k)}
              >
                {label(k)}
              </button>
            ))}
          </div>
          <div className="chart-header">
            <div>
              <small>
                {metric === 'rainfall'
                  ? t('Dönem toplamı', 'Period total')
                  : t('Dönem ortalaması', 'Period mean')}
              </small>
              <h2>
                {label(metric)} <span>{unit(metric)}</span>
              </h2>
            </div>
            <div className="segmented">
              {['month', 'year'].map((r, i) => (
                <button
                  className={period === r ? 'active' : ''}
                  key={r}
                  onClick={() => setPeriod(r)}
                >
                  {i ? t('Yıllık', 'Yearly') : t('Aylık', 'Monthly')}
                </button>
              ))}
            </div>
          </div>
          <div className="chart">
            <ResponsiveContainer width="100%" height="100%">
              {metric === 'rainfall' ? (
                <BarChart data={data}>
                  <CartesianGrid vertical={false} stroke="var(--border)" />
                  <XAxis dataKey="label" />
                  <YAxis width={45} />
                  <Tooltip />
                  <Bar
                    dataKey="value"
                    name={label(metric)}
                    unit={' ' + unit(metric)}
                    isAnimationActive={
                      !matchMedia('(prefers-reduced-motion: reduce)').matches
                    }
                    animationDuration={350}
                    fill="#859e76"
                    radius={[3, 3, 0, 0]}
                  />
                </BarChart>
              ) : (
                <AreaChart data={data}>
                  <defs>
                    <linearGradient id="chart-fill" x1="0" x2="0" y1="0" y2="1">
                      <stop offset="0" stopColor="#8fad75" stopOpacity=".45" />
                      <stop offset="1" stopColor="#8fad75" stopOpacity=".03" />
                    </linearGradient>
                  </defs>
                  <CartesianGrid vertical={false} stroke="var(--border)" />
                  <XAxis dataKey="label" />
                  <YAxis width={45} domain={['auto', 'auto']} />
                  <Tooltip />
                  <Area
                    isAnimationActive={
                      !matchMedia('(prefers-reduced-motion: reduce)').matches
                    }
                    animationDuration={350}
                    type="monotone"
                    dataKey="value"
                    name={label(metric)}
                    unit={' ' + unit(metric)}
                    stroke="#789452"
                    strokeWidth={3}
                    fill="url(#chart-fill)"
                  />
                </AreaChart>
              )}
            </ResponsiveContainer>
          </div>
          <OutlookPanel field={field} t={t} />
          <details>
            <summary>{t('Grafik değerleri', 'Chart values')}</summary>
            <table>
              <tbody>
                {data.map((d) => (
                  <tr key={d.label}>
                    <th>{d.label}</th>
                    <td>
                      {fmt(d.value, 3)} {unit(metric)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        </>
      )}
      {page === 'indicators' && (
        <>
          <div className="filters">
            <label>
              {t('GDD taban sıcaklığı (°C)', 'GDD base temperature (°C)')}
              <input
                type="number"
                value={base}
                onChange={(e) => setBase(+e.target.value)}
              />
            </label>
            <label>
              {t('Sıcaklık eşiği (°C)', 'Temperature threshold (°C)')}
              <input
                type="number"
                value={threshold}
                onChange={(e) => setThreshold(+e.target.value)}
              />
            </label>
          </div>
          <div className="indicators">
            {[
              [
                Sprout,
                t('Büyüme derece günleri', 'Growing degree days'),
                fmt(calculated.gdd) + ' °C·d',
              ],
              [
                Wind,
                t(
                  'En uzun kurak dönem (<1 mm/gün)',
                  'Longest dry spell (<1 mm/day)',
                ),
                calculated.longest + ' ' + t('gün', 'days'),
              ],
              [
                Activity,
                t('Eşiğe ulaşan günler', 'Days reaching threshold'),
                calculated.hot + ' ' + t('gün', 'days'),
              ],
            ].map(([Icon, title, value]) => {
              const I = Icon as typeof Sprout
              return (
                <section key={String(title)}>
                  <I size={19} />
                  <h3>{String(title)}</h3>
                  <strong>{String(value)}</strong>
                </section>
              )
            })}
          </div>
          <h2>
            {t('Tarımsal iklim takvimi', 'Agricultural climate calendar')}
          </h2>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  {[
                    t('Ay', 'Month'),
                    'GDD (°C·d)',
                    t('Kurak dönem (gün)', 'Dry spell (days)'),
                    t('Eşik günleri', 'Threshold days'),
                  ].map((s) => (
                    <th key={s}>{s}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {Array.from({ length: 12 }, (_, i) => {
                  const a = indicators(
                    rows.filter((r) => +r.date.slice(5, 7) === i + 1),
                    base,
                    threshold,
                  )
                  return (
                    <tr key={i}>
                      <th>{mon(i)}</th>
                      <td>{fmt(a.gdd)}</td>
                      <td>{a.longest}</td>
                      <td>{a.hot}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
      {page === 'data' && (
        <>
          <div className="chart-header">
            <h2>{t('Günlük kayıtlar', 'Daily records')}</h2>
            <select
              aria-label={t('Ay', 'Month')}
              value={month}
              onChange={(e) => setMonth(+e.target.value)}
            >
              {Array.from({ length: 12 }, (_, i) => (
                <option key={i} value={i + 1}>
                  {mon(i)}
                </option>
              ))}
            </select>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t('Tarih', 'Date')}</th>
                  {keys.map((k) => (
                    <th key={k}>
                      {label(k)}
                      <small>{unit(k)}</small>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {daily.map((r) => (
                  <tr key={r.date}>
                    <th>{r.date}</th>
                    {keys.map((k) => (
                      <td key={k}>{fmt(r[k], k === 'moisture' ? 3 : 1)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </article>
  )
}
