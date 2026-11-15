// Crops & rotations page: the farmer's own crops, the ready-made library, suggestions for the analysed field
// and the rotation builder. Usable any time; anything saved here goes with the next analysis.
import { useState } from 'react'
import { Library, ListPlus, Repeat, Sprout } from 'lucide-react'
import { useBackend } from './backend'
import { CropEditor, CropSuggestions, LibraryBrowser, RotationBuilder } from './FarmerTools'
import { farmerOptions, useCatalog, useCustomCrops, useCustomRotations } from './farmerData'
import type { Field, Language, Translate } from './model'

type Tab = 'crops' | 'library' | 'suggest' | 'rotations'

export default function Planner({
  t,
  lang,
  fields,
  goAI,
}: {
  t: Translate
  lang: Language
  fields: Field[]
  goAI: () => void
}) {
  const b = useBackend()
  const { catalog, failed } = useCatalog()
  const [crops, setCrops] = useCustomCrops()
  const [rotations, setRotations] = useCustomRotations()
  const [tab, setTab] = useState<Tab>('crops')
  const a = b.analysis
  const analysed = a?.mlDone && a.layers.length > 0 && b.phase === 'active'
  const field = fields.find((f) => f.id === a?.fieldId)
  const tabs: [Tab, typeof Sprout, string][] = [
    ['crops', Sprout, t('Ürünlerim', 'My crops') + ` (${crops.length})`],
    ['library', Library, t('Ürün kütüphanesi', 'Crop library') + (catalog?.library ? ` (${catalog.library.length})` : '')],
    ['suggest', ListPlus, t('Tarlama önerilenler', 'Suggested for my field')],
    ['rotations', Repeat, t('Rotasyonlarım', 'My rotations') + ` (${rotations.length})`],
  ]
  return (
    <article className="analytic intelligence planner">
      <div className="analytics-heading">
        <div>
          <h1>{t('Ürünler ve rotasyonlar', 'Crops & rotations')}</h1>
          <p>
            {t(
              'Kendi ürünlerinizi ekleyin, hazır kütüphaneden seçin, tarlanıza uygun ürün önerilerini görün ve rotasyonunuzu oluşturun. Hepsi bu tarayıcıda saklanır ve bir sonraki analizde hesaba girer.',
              'Add your own crops, pick from the ready-made library, see crops suited to your field and build your rotations. Everything is saved in this browser and used in your next analysis.',
            )}
          </p>
        </div>
        <button className="outline" onClick={goAI}>
          {t('Analize git', 'Go to analysis')}
        </button>
      </div>
      {failed && (
        <p className="hint">
          {t(
            'Ürün kataloğu alınamadı. Backend çalışıyor ve gerçek ML açık olmalı (ML_PROVIDER=xgboost).',
            'Crop catalogue unavailable. The backend must be running with the real ML enabled (ML_PROVIDER=xgboost).',
          )}
        </p>
      )}
      <div className="segmented planner-tabs" role="tablist">
        {tabs.map(([k, Icon, label]) => (
          <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? 'active' : ''} onClick={() => setTab(k)}>
            <Icon size={14} />
            {label}
          </button>
        ))}
      </div>
      {catalog && tab === 'crops' && <CropEditor t={t} lang={lang} catalog={catalog} crops={crops} setCrops={setCrops} />}
      {catalog && tab === 'library' && (
        <LibraryBrowser t={t} lang={lang} catalog={catalog} crops={crops} setCrops={setCrops} />
      )}
      {catalog && tab === 'suggest' && (
        <div className="tool">
          {analysed && (
            <p className="hint">
              {t('Son analiz edilen tarla', 'Last analysed field')}: <b>{a?.fieldName || field?.properties.name}</b>
            </p>
          )}
          <CropSuggestions
            t={t}
            lang={lang}
            sid={b.sid}
            analysisKey={analysed ? (a?.fieldId ?? '') + (a?.date ?? '') : null}
            catalog={catalog}
            crops={crops}
            setCrops={setCrops}
          />
        </div>
      )}
      {catalog && tab === 'rotations' && (
        <>
          <RotationBuilder
            t={t}
            lang={lang}
            catalog={catalog}
            crops={crops}
            rotations={rotations}
            setRotations={setRotations}
            busy={b.job?.status === 'running' || !analysed}
            onEvaluate={(seq) =>
              b
                .rescore({
                  chosen_rotation: seq,
                  existing_rotation: field?.properties.existing_rotation || undefined,
                  ...farmerOptions(catalog, crops, rotations),
                })
                .then(goAI)
                .catch(() => {})
            }
          />
          {!analysed && (
            <p className="hint">
              {t(
                '“Hemen değerlendir” için önce yapay zekâ sayfasında bir analiz yapın. Kaydettiğiniz rotasyonlar bir sonraki analizde otomatik puanlanır.',
                '“Evaluate now” needs an analysis on the AI page first. Saved rotations are scored automatically in the next analysis.',
              )}
            </p>
          )}
        </>
      )}
    </article>
  )
}
