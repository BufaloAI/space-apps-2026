import { useEffect, useState } from 'react'
import { Check } from 'lucide-react'
import { blankInfo, type Field, type FieldInfo, type Translate, hectares } from './model'
import { rotationCatalog, type Catalog } from './api'
import { Dialog } from './ui'
export const crops = [
  ['wheat', 'Buğday', 'Wheat'],
  ['corn', 'Mısır', 'Maize'],
  ['rice', 'Pirinç', 'Rice'],
  ['cotton', 'Pamuk', 'Cotton'],
  ['olive', 'Zeytin', 'Olive'],
  ['other', 'Diğer', 'Other'],
]
// USDA texture classes; the backend accepts any label and counts it as known local data.
export const textures = [
  ['sand', 'Kum', 'Sand'],
  ['loamy_sand', 'Tınlı kum', 'Loamy sand'],
  ['sandy_loam', 'Kumlu tın', 'Sandy loam'],
  ['loam', 'Tın', 'Loam'],
  ['silt_loam', 'Siltli tın', 'Silt loam'],
  ['silt', 'Silt', 'Silt'],
  ['sandy_clay_loam', 'Kumlu killi tın', 'Sandy clay loam'],
  ['clay_loam', 'Killi tın', 'Clay loam'],
  ['silty_clay_loam', 'Siltli killi tın', 'Silty clay loam'],
  ['sandy_clay', 'Kumlu kil', 'Sandy clay'],
  ['silty_clay', 'Siltli kil', 'Silty clay'],
  ['clay', 'Kil', 'Clay'],
]
export const irrigation = [
  ['rain', 'Yağışa bağlı', 'Rainfed'],
  ['drip', 'Damla', 'Drip'],
  ['sprinkler', 'Yağmurlama', 'Sprinkler'],
  ['surface', 'Yüzey', 'Surface'],
]
export function FieldForm({
  field,
  commit,
  close,
  t,
}: {
  field: Field
  commit: (f: Field) => void
  close: () => void
  t: Translate
}) {
  const [info, set] = useState<FieldInfo>({ ...blankInfo, ...field.properties })
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  useEffect(() => {
    rotationCatalog()
      .then(setCatalog)
      .catch(() => {})
  }, [])
  const known = [info.irrigation, info.soil_texture, info.soil_om_pct, info.slope_pct].filter(
    (v) => v !== '',
  ).length
  const update = (k: keyof FieldInfo, v: string) =>
    set((o) => ({ ...o, [k]: v }))
  return (
    <Dialog title={t('Tarla bilgileri', 'Field details')} close={close} t={t}>
      <p>
        {t('Çizilen yaklaşık alan', 'Approximate drawn area')} ·{' '}
        {hectares(field).toLocaleString(undefined, {
          maximumFractionDigits: 2,
        })}{' '}
        ha
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          if (info.name.trim())
            commit({
              ...field,
              properties: { ...info, name: info.name.trim() },
            })
        }}
      >
        <label>
          {t('Tarla adı', 'Field name')}
          <input
            required
            maxLength={100}
            value={info.name}
            onChange={(e) => update('name', e.target.value)}
          />
        </label>
        <div className="form-grid">
          {(
            [
              ['crop', t('Ürün', 'Crop'), crops],
              ['irrigation', t('Sulama', 'Irrigation'), irrigation],
            ] as const
          ).map(([k, label, items]) => (
            <label key={k}>
              {label}
              <select
                value={info[k]}
                onChange={(e) => update(k, e.target.value)}
              >
                <option value="">{t('Seçilmedi', 'Unspecified')}</option>
                {info[k] && !items.some((r) => r[0] === info[k]) && (
                  <option>{info[k]}</option>
                )}
                {items.map(([v, tr, en]) => (
                  <option key={v} value={v}>
                    {t(tr, en)}
                  </option>
                ))}
              </select>
            </label>
          ))}
        </div>
        <label>
          {t('Ekim tarihi', 'Planting date')}
          <input
            type="date"
            value={info.planted}
            onChange={(e) => update('planted', e.target.value)}
          />
        </label>
        <fieldset className="farmer-data">
          <legend>{t('Çiftçi bilgisi', "Farmer's knowledge")}</legend>
          <small>
            {t(
              'Bildiğiniz değerleri girin; her biri rotasyon önerisinin eminliğini artırır. Bilmediğiniz alanı boş bırakın.',
              'Enter what you know; each value raises the confidence of rotation advice. Leave unknowns blank.',
            )}{' '}
            <b>
              {known}/4 {t('girildi', 'provided')}
            </b>
          </small>
          <div className="form-grid">
            <label>
              {t('Toprak dokusu', 'Soil texture')}
              <select
                value={info.soil_texture}
                onChange={(e) => update('soil_texture', e.target.value)}
              >
                <option value="">{t('Bilinmiyor', 'Unknown')}</option>
                {info.soil_texture &&
                  !textures.some((r) => r[0] === info.soil_texture) && (
                    <option>{info.soil_texture}</option>
                  )}
                {textures.map(([v, tr, en]) => (
                  <option key={v} value={v}>
                    {t(tr, en)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {t('Organik madde (%)', 'Organic matter (%)')}
              <input
                type="number"
                min={0}
                max={100}
                step={0.1}
                inputMode="decimal"
                placeholder={t('örn. 3.2', 'e.g. 3.2')}
                value={info.soil_om_pct}
                onChange={(e) => update('soil_om_pct', e.target.value)}
              />
            </label>
            <label>
              {t('Eğim (%)', 'Slope (%)')}
              <input
                type="number"
                min={0}
                max={100}
                step={0.5}
                inputMode="decimal"
                placeholder={t('örn. 3', 'e.g. 3')}
                value={info.slope_pct}
                onChange={(e) => update('slope_pct', e.target.value)}
              />
            </label>
            <label>
              {t('Mevcut rotasyon', 'Current rotation')}
              {catalog ? (
                <select
                  value={info.existing_rotation}
                  onChange={(e) => update('existing_rotation', e.target.value)}
                >
                  <option value="">{t('Belirtilmedi', 'Not specified')}</option>
                  {info.existing_rotation &&
                    !catalog.rotations.some((r) => r.id === info.existing_rotation) && (
                      <option>{info.existing_rotation}</option>
                    )}
                  {catalog.rotations.map((r) => (
                    <option key={r.id} value={r.id}>
                      {r.name}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  maxLength={200}
                  placeholder="corn>soybean"
                  value={info.existing_rotation}
                  onChange={(e) => update('existing_rotation', e.target.value)}
                />
              )}
            </label>
          </div>
        </fieldset>
        <label>
          {t('Notlar', 'Notes')}
          <textarea
            value={info.notes}
            onChange={(e) => update('notes', e.target.value)}
          />
        </label>
        <button className="primary" type="submit">
          <Check size={16} />
          {t('Tarlayı kaydet', 'Save field')}
        </button>
      </form>
    </Dialog>
  )
}
