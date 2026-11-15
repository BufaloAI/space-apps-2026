import { useState } from 'react'
import { Check } from 'lucide-react'
import { type Field, type FieldInfo, type Translate, hectares } from './model'
import { Dialog } from './ui'
export const crops = [
  ['wheat', 'Buğday', 'Wheat'],
  ['corn', 'Mısır', 'Maize'],
  ['rice', 'Pirinç', 'Rice'],
  ['cotton', 'Pamuk', 'Cotton'],
  ['olive', 'Zeytin', 'Olive'],
  ['other', 'Diğer', 'Other'],
]
const irrigation = [
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
  const [info, set] = useState(field.properties)
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
