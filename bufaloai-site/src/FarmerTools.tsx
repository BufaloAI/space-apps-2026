// Farmer tools on the AI page: their own crops (with their own numbers) and a rotation builder.
// Values are the farmer's; the backend scores them with the same rules as catalogue crops.
import { useEffect, useMemo, useState } from 'react'
import { ArrowRight, Check, Copy, Lightbulb, Loader2, Pencil, Play, Plus, Save, Search, Trash2, X } from 'lucide-react'
import {
  cropSuggestions,
  type Catalog,
  type CatalogCrop,
  type CropProfile,
  type CropSuggestion,
  type CustomCrop,
  type CustomRotation,
} from './api'
import {
  MAX_CROPS,
  MAX_ROTATIONS,
  MAX_SEASONS,
  cropFromCatalog,
  cropId,
  seqFromString,
  seqToString,
  type Season,
} from './farmerData'
import { pick } from './i18n'
import type { Language, Translate } from './model'

const nameOf = (lang: Language, c: { name: string; name_en?: string }) =>
  lang === 'en' ? (c.name_en ?? c.name) : c.name

const FLOATS: [keyof CropProfile, string, string, [string, string], [string, string]][] = [
  ['water_need', 'Su ihtiyacı', 'Water need', ['az', 'low'], ['çok', 'high']],
  ['drought_tol', 'Kuraklık dayanımı', 'Drought tolerance', ['zayıf', 'weak'], ['güçlü', 'strong']],
  ['heat_tol', 'Sıcak dayanımı', 'Heat tolerance', ['zayıf', 'weak'], ['güçlü', 'strong']],
  ['frost_tol', 'Don dayanımı', 'Frost tolerance', ['zayıf', 'weak'], ['güçlü', 'strong']],
  ['wet_tol', 'Nem/hastalık dayanımı', 'Wet-season tolerance', ['zayıf', 'weak'], ['güçlü', 'strong']],
  ['n_need', 'Azot (gübre) ihtiyacı', 'Nitrogen need', ['az', 'low'], ['çok', 'high']],
  ['residue_cover', 'Toprak örtüsü (erozyon koruması)', 'Residue cover', ['az', 'low'], ['çok', 'high']],
  ['om', 'Organik maddeye katkı', 'Organic matter added', ['az', 'low'], ['çok', 'high']],
  ['market', 'Pazar değeri / gelir', 'Market value', ['düşük', 'low'], ['yüksek', 'high']],
  ['forage', 'Yem değeri', 'Forage value', ['düşük', 'low'], ['yüksek', 'high']],
]
const SEASON_LABEL: Record<string, [string, string]> = {
  warm: ['Yazlık', 'Warm season'],
  cool: ['Kışlık', 'Cool season'],
  perennial: ['Çok yıllık', 'Perennial'],
  none: ['Nadas / ürünsüz', 'Fallow'],
}
const blankCrop = (id: string): CustomCrop => ({
  id,
  name: '',
  family: 'other',
  season: 'warm',
  cover_crop: false,
  water_need: 0.5,
  drought_tol: 0.5,
  heat_tol: 0.5,
  frost_tol: 0.5,
  residue_cover: 0.5,
  om: 0.5,
  market: 0.5,
  forage: 0.3,
  n_need: 0.5,
  wet_tol: 0.7,
  gdd_need: 0,
  n_fix: false,
})

function CropForm({
  t,
  crop,
  catalog,
  isNew,
  onSave,
  onCancel,
}: {
  t: Translate
  crop: CustomCrop
  catalog: Catalog
  isNew: boolean
  onSave: (c: CustomCrop) => void
  onCancel: () => void
}) {
  const [c, set] = useState(crop)
  const up = <K extends keyof CustomCrop>(k: K, v: CustomCrop[K]) => set((o) => ({ ...o, [k]: v }))
  const families = catalog.crop_fields?.families ?? ['other']
  const overrides = catalog.crops.some((x) => x.id === c.id)
  const num = (v: string) => (v.trim() === '' ? undefined : Math.max(0, +v))
  const margin =
    c.yield_t_ha !== undefined && c.price_per_t !== undefined && c.cost_per_ha !== undefined
      ? c.yield_t_ha * c.price_per_t - c.cost_per_ha
      : null
  return (
    <form
      className="crop-form"
      onSubmit={(e) => {
        e.preventDefault()
        if (c.name.trim()) onSave({ ...c, name: c.name.trim() })
      }}
    >
      <div className="form-grid">
        <label>
          {t('Ürün adı', 'Crop name')}
          <input required maxLength={60} value={c.name} onChange={(e) => up('name', e.target.value)} />
        </label>
        <label>
          {t('Ürün ailesi', 'Plant family')}
          <select value={c.family} onChange={(e) => up('family', e.target.value)}>
            {[...new Set([...families, c.family])].map((f) => (
              <option key={f}>{f}</option>
            ))}
          </select>
        </label>
        <label>
          {t('Sezon', 'Season')}
          <select value={c.season} onChange={(e) => up('season', e.target.value as CustomCrop['season'])}>
            {Object.entries(SEASON_LABEL).map(([k, [tr, en]]) => (
              <option key={k} value={k}>
                {t(tr, en)}
              </option>
            ))}
          </select>
        </label>
        <label>
          {t('GDD ihtiyacı (10 °C taban)', 'GDD need (base 10 °C)')}
          <input
            type="number"
            min={0}
            max={5000}
            step={50}
            value={c.gdd_need}
            onChange={(e) => up('gdd_need', Math.max(0, +e.target.value))}
          />
        </label>
      </div>
      <div className="checks">
        <label>
          <input type="checkbox" checked={c.n_fix} onChange={(e) => up('n_fix', e.target.checked)} />
          {t('Azot bağlar (baklagil)', 'Fixes nitrogen (legume)')}
        </label>
        <label>
          <input type="checkbox" checked={c.cover_crop} onChange={(e) => up('cover_crop', e.target.checked)} />
          {t('Örtü bitkisi olarak kullanılır', 'Used as a cover crop')}
        </label>
      </div>
      <fieldset className="crop-sliders">
        <legend>{t('Ürün özellikleri (0–1, sizin değerlendirmeniz)', 'Crop traits (0–1, your judgement)')}</legend>
        {FLOATS.map(([k, tr, en, lo, hi]) => (
          <label key={k} className="slider">
            <span>{t(tr, en)}</span>
            <small>{t(...lo)}</small>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={c[k] as number}
              onChange={(e) => up(k, +e.target.value as never)}
            />
            <small>{t(...hi)}</small>
            <b>{(c[k] as number).toFixed(2)}</b>
          </label>
        ))}
      </fieldset>
      <fieldset className="econ">
        <legend>{t('Kendi hesabınız (isteğe bağlı)', 'Your own figures (optional)')}</legend>
        <div className="form-grid three">
          <label>
            {t('Verim (t/ha)', 'Yield (t/ha)')}
            <input
              type="number"
              min={0}
              step={0.1}
              value={c.yield_t_ha ?? ''}
              onChange={(e) => up('yield_t_ha', num(e.target.value))}
            />
          </label>
          <label>
            {t('Fiyat (para/t)', 'Price (per t)')}
            <input
              type="number"
              min={0}
              step={1}
              value={c.price_per_t ?? ''}
              onChange={(e) => up('price_per_t', num(e.target.value))}
            />
          </label>
          <label>
            {t('Maliyet (para/ha)', 'Cost (per ha)')}
            <input
              type="number"
              min={0}
              step={1}
              value={c.cost_per_ha ?? ''}
              onChange={(e) => up('cost_per_ha', num(e.target.value))}
            />
          </label>
        </div>
        <small>
          {margin !== null
            ? t('Brüt kâr (kayıpsız)', 'Gross margin (no losses)') +
              ': ' +
              margin.toLocaleString(undefined, { maximumFractionDigits: 0 }) +
              ' /ha · ' +
              t(
                'analizde tahmini verim kaybı düşülerek rotasyon başına hesaplanır.',
                'the analysis subtracts estimated yield losses per rotation.',
              )
            : t(
                'Üçü de girilirse rotasyon kartlarında brüt kârınız hesaplanır.',
                'Fill in all three to see your gross margin on rotation cards.',
              )}
        </small>
      </fieldset>
      {overrides && (
        <p className="hint">
          {t(
            'Bu kimlik katalogdaki ürünle aynı: analizde katalog değerleri yerine sizin değerleriniz kullanılır.',
            'Same id as a catalogue crop: your values replace the catalogue values in analyses.',
          )}
        </p>
      )}
      <div className="row-actions">
        <button className="primary">
          <Save size={15} />
          {isNew ? t('Ürünü ekle', 'Add crop') : t('Kaydet', 'Save')}
        </button>
        <button type="button" className="outline" onClick={onCancel}>
          <X size={15} />
          {t('Vazgeç', 'Cancel')}
        </button>
      </div>
    </form>
  )
}

export function CropEditor({
  t,
  lang,
  catalog,
  crops,
  setCrops,
}: {
  t: Translate
  lang: Language
  catalog: Catalog
  crops: CustomCrop[]
  setCrops: (f: (c: CustomCrop[]) => CustomCrop[]) => void
}) {
  const [editing, setEditing] = useState<{ crop: CustomCrop; isNew: boolean } | null>(null)
  const [copyFrom, setCopyFrom] = useState('')
  const taken = [...catalog.crops.map((c) => c.id), ...crops.map((c) => c.id)]
  if (editing)
    return (
      <CropForm
        t={t}
        catalog={catalog}
        crop={editing.crop}
        isNew={editing.isNew}
        onCancel={() => setEditing(null)}
        onSave={(c) => {
          const known = [...catalog.crops, ...(catalog.library ?? [])].some((x) => x.id === c.id)
          const id = editing.isNew && !known ? cropId(c.name, taken) : c.id
          setCrops((prev) =>
            prev.some((p) => p.id === id) ? prev.map((p) => (p.id === id ? { ...c, id } : p)) : [...prev, { ...c, id }],
          )
          setEditing(null)
        }}
      />
    )
  return (
    <div className="tool">
      <p className="hint">
        {t(
          'Listede olmayan ürünlerinizi ekleyin ya da bir katalog ürününü kopyalayıp kendi değerlerinizle düzenleyin. Bu tarayıcıda saklanır ve her analizde gönderilir.',
          'Add crops that are not listed, or copy a catalogue crop and edit it with your own values. Saved in this browser and sent with every analysis.',
        )}
      </p>
      <ul className="saved">
        {crops.map((c) => (
          <li key={c.id}>
            <span>
              <b>{c.name}</b>
              <small>
                {c.id} · {t(...SEASON_LABEL[c.season])}
                {c.cover_crop ? ' · ' + t('örtü', 'cover') : ''}
                {c.yield_t_ha !== undefined ? ' · ' + c.yield_t_ha + ' t/ha' : ''}
              </small>
            </span>
            <button className="iconbtn" title={t('Düzenle', 'Edit')} onClick={() => setEditing({ crop: c, isNew: false })}>
              <Pencil size={15} />
            </button>
            <button
              className="iconbtn"
              title={t('Sil', 'Delete')}
              onClick={() => setCrops((prev) => prev.filter((p) => p.id !== c.id))}
            >
              <Trash2 size={15} />
            </button>
          </li>
        ))}
        {!crops.length && <li className="empty">{t('Henüz ürün eklemediniz.', 'No crops added yet.')}</li>}
      </ul>
      <div className="row-actions">
        <button
          className="primary"
          disabled={crops.length >= MAX_CROPS}
          onClick={() => setEditing({ crop: blankCrop(''), isNew: true })}
        >
          <Plus size={15} />
          {t('Yeni ürün', 'New crop')}
        </button>
        <select aria-label={t('Katalogdan kopyala', 'Copy from catalogue')} value={copyFrom} onChange={(e) => setCopyFrom(e.target.value)}>
          <option value="">{t('Katalogdan kopyala…', 'Copy from catalogue…')}</option>
          {[...catalog.crops, ...(catalog.library ?? [])].map((c) => (
            <option key={c.id} value={c.id}>
              {nameOf(lang, c)}
            </option>
          ))}
        </select>
        <button
          className="outline"
          disabled={!copyFrom || crops.length >= MAX_CROPS}
          onClick={() => {
            const src = [...catalog.crops, ...(catalog.library ?? [])].find((c) => c.id === copyFrom)
            if (!src) return
            const existing = crops.find((c) => c.id === src.id)
            // Keep the source id: a catalogue crop is then overridden, a library crop added under its own id.
            setEditing({ isNew: !existing, crop: existing ?? cropFromCatalog(src, lang) })
          }}
        >
          <Copy size={15} />
          {t('Kopyala ve düzenle', 'Copy and edit')}
        </button>
      </div>
    </div>
  )
}

export function RotationBuilder({
  t,
  lang,
  catalog,
  crops,
  rotations,
  setRotations,
  busy,
  onEvaluate,
}: {
  t: Translate
  lang: Language
  catalog: Catalog
  crops: CustomCrop[]
  rotations: CustomRotation[]
  setRotations: (f: (r: CustomRotation[]) => CustomRotation[]) => void
  busy: boolean
  onEvaluate: (seq: string) => void
}) {
  const all = useMemo(() => {
    const m = new Map<string, { id: string; name: string; cover: boolean; mine: boolean }>()
    for (const c of catalog.crops) m.set(c.id, { id: c.id, name: nameOf(lang, c), cover: c.cover_crop, mine: false })
    for (const c of crops) m.set(c.id, { id: c.id, name: c.name, cover: c.cover_crop, mine: true })
    return m
  }, [catalog, crops, lang])
  const mains = [...all.values()].filter((c) => !c.cover)
  const covers = [...all.values()].filter((c) => c.cover)
  const [seasons, setSeasons] = useState<Season[]>([{ main: mains[0]?.id ?? '', cover: '' }])
  const [name, setName] = useState('')
  const [editIndex, setEditIndex] = useState<number | null>(null)
  const label = (id: string) => all.get(id)?.name ?? id + ' ⚠'
  const auto = seasons.map((s) => label(s.main) + (s.cover ? ' + ' + label(s.cover) : '')).join(' → ')
  const setSeason = (i: number, p: Partial<Season>) =>
    setSeasons((ss) => ss.map((s, j) => (j === i ? { ...s, ...p } : s)))
  const broken = (r: CustomRotation) => r.seq.split(/[>+]/).some((id) => !all.has(id))
  return (
    <div className="tool">
      <p className="hint">
        {t(
          'Sezon sezon kendi rotasyonunuzu kurun. Kaydedilen rotasyonlar her analizde önerilerle birlikte puanlanır ve sıralamadaki yeri gösterilir.',
          'Build your rotation season by season. Saved rotations are scored with the suggestions in every analysis and their rank is shown.',
        )}
      </p>
      <ol className="builder">
        {seasons.map((s, i) => (
          <li key={i}>
            <span className="season-no">{i + 1}</span>
            <select aria-label={t('Ana ürün', 'Main crop')} value={s.main} onChange={(e) => setSeason(i, { main: e.target.value })}>
              {mains.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                  {c.mine ? ' ★' : ''}
                </option>
              ))}
            </select>
            <span>+</span>
            <select aria-label={t('Örtü bitkisi', 'Cover crop')} value={s.cover} onChange={(e) => setSeason(i, { cover: e.target.value })}>
              <option value="">{t('örtü bitkisi yok', 'no cover crop')}</option>
              {covers.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                  {c.mine ? ' ★' : ''}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="iconbtn"
              title={t('Sezonu kaldır', 'Remove season')}
              disabled={seasons.length <= 1}
              onClick={() => setSeasons((ss) => ss.filter((_, j) => j !== i))}
            >
              <X size={15} />
            </button>
            {i < seasons.length - 1 && <ArrowRight size={14} className="arrow" />}
          </li>
        ))}
      </ol>
      <div className="row-actions">
        <button
          type="button"
          className="outline"
          disabled={seasons.length >= MAX_SEASONS}
          onClick={() => setSeasons((ss) => [...ss, { main: mains[0]?.id ?? '', cover: '' }])}
        >
          <Plus size={15} />
          {t('Sezon ekle', 'Add season')}
        </button>
      </div>
      <label className="name">
        {t('Rotasyon adı', 'Rotation name')}
        <input maxLength={80} placeholder={auto} value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <div className="row-actions">
        <button
          className="primary"
          disabled={!seasons.every((s) => s.main) || (editIndex === null && rotations.length >= MAX_ROTATIONS)}
          onClick={() => {
            const r = { name: (name.trim() || auto).slice(0, 80), seq: seqToString(seasons) }
            setRotations((prev) =>
              editIndex === null ? [...prev, r] : prev.map((p, j) => (j === editIndex ? r : p)),
            )
            setEditIndex(null)
            setName('')
          }}
        >
          <Save size={15} />
          {editIndex === null ? t('Rotasyonu kaydet', 'Save rotation') : t('Değişikliği kaydet', 'Save changes')}
        </button>
        <button className="outline" disabled={busy || !seasons.every((s) => s.main)} onClick={() => onEvaluate(seqToString(seasons))}>
          <Play size={15} />
          {t('Hemen değerlendir', 'Evaluate now')}
        </button>
      </div>
      <h4>{t('Kayıtlı rotasyonlarım', 'My saved rotations')}</h4>
      <ul className="saved">
        {rotations.map((r, i) => (
          <li key={i} className={broken(r) ? 'is-broken' : ''}>
            <span>
              <b>{r.name}</b>
              <small>
                {seqFromString(r.seq)
                  .map((s) => label(s.main) + (s.cover ? ' + ' + label(s.cover) : ''))
                  .join(' → ')}
                {broken(r) && ' · ' + t('silinmiş ürün içeriyor, gönderilmez', 'uses a deleted crop, not sent')}
              </small>
            </span>
            <button
              className="iconbtn"
              title={t('Düzenle', 'Edit')}
              onClick={() => {
                setSeasons(seqFromString(r.seq))
                setName(r.name)
                setEditIndex(i)
              }}
            >
              <Pencil size={15} />
            </button>
            <button
              className="iconbtn"
              title={t('Sil', 'Delete')}
              onClick={() => {
                setRotations((prev) => prev.filter((_, j) => j !== i))
                if (editIndex === i) setEditIndex(null)
              }}
            >
              <Trash2 size={15} />
            </button>
          </li>
        ))}
        {!rotations.length && <li className="empty">{t('Henüz kayıtlı rotasyon yok.', 'No saved rotations yet.')}</li>}
      </ul>
    </div>
  )
}


/** Ready-made crops (crop_library.toml): one click copies a crop into the farmer's list, editable later. */
export function LibraryBrowser({
  t,
  lang,
  catalog,
  crops,
  setCrops,
}: {
  t: Translate
  lang: Language
  catalog: Catalog
  crops: CustomCrop[]
  setCrops: (f: (c: CustomCrop[]) => CustomCrop[]) => void
}) {
  const [q, setQ] = useState('')
  const lib = catalog.library ?? []
  const mine = new Set(crops.map((c) => c.id))
  const shown = lib.filter((c) =>
    (c.name + ' ' + (c.name_en ?? '') + ' ' + c.family).toLowerCase().includes(q.trim().toLowerCase()),
  )
  const season = (s: string) => ({ warm: t('yazlık', 'warm season'), cool: t('kışlık', 'cool season'), perennial: t('çok yıllık', 'perennial'), none: t('nadas', 'fallow') })[s] ?? s
  return (
    <div className="tool">
      <p className="hint">
        {t(
          'Hazır ürünler genel değerlerle gelir; listenize ekleyince rotasyon adaylarına girer ve kendi değerlerinizle düzenleyebilirsiniz.',
          'Ready-made crops come with general values; once added to your list they join the rotation candidates and you can edit them.',
        )}
      </p>
      <label className="search-line">
        <Search size={15} />
        <input
          placeholder={t('Ürün ara…', 'Search crops…')}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          aria-label={t('Ürün ara', 'Search crops')}
        />
      </label>
      <ul className="library">
        {shown.map((c) => (
          <li key={c.id}>
            <span>
              <b>{nameOf(lang, c)}</b>
              <small>
                {c.family} · {season(c.season)}
                {c.cover_crop ? ' · ' + t('örtü bitkisi', 'cover crop') : ''}
                {c.profile?.n_fix ? ' · ' + t('azot bağlar', 'fixes N') : ''}
              </small>
              {pick(lang, c, 'note') && <small>{pick(lang, c, 'note')}</small>}
            </span>
            {mine.has(c.id) ? (
              <span className="added">
                <Check size={14} /> {t('Eklendi', 'Added')}
              </span>
            ) : (
              <button
                className="outline"
                disabled={crops.length >= MAX_CROPS}
                onClick={() => setCrops((prev) => [...prev, cropFromCatalog(c, lang)])}
              >
                <Plus size={14} /> {t('Ekle', 'Add')}
              </button>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}

/** Crops that suit the last analysed field (backend scores the climate fit) and are not in the farmer's list. */
export function CropSuggestions({
  t,
  lang,
  sid,
  analysisKey,
  catalog,
  crops,
  setCrops,
}: {
  t: Translate
  lang: Language
  sid: string | null
  analysisKey: string | null
  catalog: Catalog
  crops: CustomCrop[]
  setCrops: (f: (c: CustomCrop[]) => CustomCrop[]) => void
}) {
  const [items, setItems] = useState<CropSuggestion[] | null>(null)
  const [state, setState] = useState<'idle' | 'loading' | 'failed'>('idle')
  const exclude = crops.map((c) => c.id)
  useEffect(() => {
    if (!sid || !analysisKey) return setItems(null)
    let alive = true
    setState('loading')
    cropSuggestions(sid, exclude)
      .then((r) => {
        if (!alive) return
        setItems(r)
        setState('idle')
      })
      .catch(() => alive && setState('failed'))
    return () => {
      alive = false
    }
  }, [sid, analysisKey, exclude.join(',')])
  if (!analysisKey)
    return (
      <p className="hint">
        {t(
          'Öneriler için yapay zekâ sayfasında bir tarlayı analiz edin; tarlanın iklimine uyan ürünler burada listelenir.',
          'Analyse a field on the AI page to get suggestions; crops that suit its climate will be listed here.',
        )}
      </p>
    )
  const add = (s: CropSuggestion) => {
    const src = [...(catalog.library ?? []), ...catalog.crops].find((c) => c.id === s.id) as CatalogCrop | undefined
    if (src) setCrops((prev) => (prev.some((p) => p.id === s.id) ? prev : [...prev, cropFromCatalog(src, lang)]))
  }
  return (
    <div className="suggestions">
      {state === 'loading' && !items && (
        <p role="status">
          <Loader2 size={14} className="spin" /> {t('Hesaplanıyor…', 'Calculating…')}
        </p>
      )}
      {state === 'failed' && <p className="hint">{t('Öneriler alınamadı.', 'Suggestions unavailable.')}</p>}
      {items && !items.length && (
        <p className="hint">{t('Bu tarla için eklenecek uygun yeni ürün bulunamadı.', 'No new suitable crops found for this field.')}</p>
      )}
      <ul className="library">
        {items?.map((s) => (
          <li key={s.id}>
            <span>
              <b>
                <Lightbulb size={14} /> {lang === 'en' ? s.name_en : s.name}
              </b>
              <small>
                {t('iklim uygunluğu', 'climate fit')} <b>{Math.round(s.fit * 100)}%</b> ·{' '}
                {t('tahmini verim kaybı', 'est. yield loss')} %{s.expected_yield_loss_pct[0]}–{s.expected_yield_loss_pct[1]}
                {pick(lang, s, 'main_risk') && ' · ' + t('ana risk', 'main risk') + ': ' + pick(lang, s, 'main_risk')}
              </small>
              {pick(lang, s, 'note') && <small>{pick(lang, s, 'note')}</small>}
            </span>
            {s.from === 'catalog' ? (
              <span className="added" title={t('Zaten rotasyon adaylarında', 'Already a rotation candidate')}>
                {t('katalogda', 'in catalogue')}
              </span>
            ) : (
              <button className="outline" disabled={crops.length >= MAX_CROPS} onClick={() => add(s)}>
                <Plus size={14} /> {t('Ekle', 'Add')}
              </button>
            )}
          </li>
        ))}
      </ul>
      {!!items?.length && (
        <small className="hint">
          {t(
            'Uygunluk, ürün profillerinin genel değerleri ve tarlanın iklim verisinden hesaplanır; ölçüm değildir.',
            'Fit is computed from general crop profiles and the field\'s climate data; it is not a measurement.',
          )}
        </small>
      )}
    </div>
  )
}
