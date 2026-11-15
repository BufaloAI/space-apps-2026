import { useEffect, useRef, useState, type RefObject } from 'react'
import {
  ArrowUpRight,
  Map,
  Minus,
  Plus,
  Globe2,
  MousePointer2,
  Crosshair,
  Orbit,
  Compass,
  SkipForward,
} from 'lucide-react'
import Earth, { type OrbitControls } from './Earth'
import { Brand, LanguageSwitch } from './ui'
import { coords, type View, type Translate } from './model'
import './navigator.css'

type Props = {
  t: Translate
  lang: string
  toggleLang: () => void
  openMap: () => void
  onPrepare: (v: View) => void
  onLand: (v: View) => void
  mapReady: boolean
  viewport: RefObject<HTMLDivElement | null>
}

export default function Intro({
  t,
  lang,
  toggleLang,
  openMap,
  onPrepare,
  onLand,
  mapReady,
  viewport,
}: Props) {
  const controls = useRef<OrbitControls | null>(null)
  const [target, setTarget] = useState<View | null>(null),
    [hover, inspect] = useState<[number, number] | null>(null),
    [ready, setReady] = useState(false),
    [approaching, setApproaching] = useState(false),
    [arrived, setArrived] = useState(false),
    [polar, setPolar] = useState(false)
  const leaving = !!target && arrived && mapReady
  useEffect(() => {
    if (!target || !leaving) return
    const id = setTimeout(
      () => onLand(target),
      matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 320,
    )
    return () => clearTimeout(id)
  }, [target, leaving, onLand])
  useEffect(() => {
    if (!polar) return
    const id = setTimeout(() => setPolar(false), 4500)
    return () => clearTimeout(id)
  }, [polar])
  const position = target?.center ?? hover
  return (
    <section
      className={
        'earth-navigator' +
        (target ? ' travelling' : '') +
        (approaching ? ' approaching' : '') +
        (leaving ? ' leaving' : '')
      }
      aria-label={t('Dünya gezgini', 'Earth explorer')}
    >
      <header className="nav-mast">
        <span className="brandlink">
          <Brand />
        </span>
        <div className="nav-header-actions">
          <LanguageSwitch lang={lang} change={toggleLang} />
          <button className="nav-map-button" onClick={openMap}>
            <Map size={15} />
            {t('Haritayı aç', 'Open map')}
            <ArrowUpRight size={14} />
          </button>
        </div>
      </header>
      <div className="orbital-frame">
        <Earth
          t={t}
          controls={controls}
          viewport={viewport}
          onReady={() => setReady(true)}
          onPick={(v) => {
            setTarget(v)
            onPrepare(v)
          }}
          onInspect={inspect}
          onPolar={() => setPolar(true)}
          onApproach={() => setApproaching(true)}
          onArrive={() => setArrived(true)}
        />
      </div>
      <div className="nav-heading">
        <div className="nav-kicker">
          <span />
          {t('KÜRESEL TARIM ATLASI', 'GLOBAL AGRICULTURAL ATLAS')}
        </div>
        <h1 id="earth-title">
          {t('Dünya’yı keşfet.', 'Explore Earth.')}{' '}
          <em>{t('Toprağı anla.', 'Understand the land.')}</em>
        </h1>
        <button
          className="explore-orbit"
          disabled={!ready || !!target}
          onClick={() => {
            controls.current?.zoom(true)
            document
              .querySelector<HTMLCanvasElement>('.orbital-frame canvas')
              ?.focus({ preventScroll: true })
          }}
        >
          <Compass size={15} />
          {t('Dünya’yı keşfet', 'Explore Earth')}
        </button>
      </div>
      <div
        className="orbit-buttons"
        aria-label={t('Küre denetimleri', 'Globe controls')}
      >
        {(
          [
            [Plus, t('Yaklaş', 'Zoom in'), () => controls.current?.zoom(true)],
            [
              Minus,
              t('Uzaklaş', 'Zoom out'),
              () => controls.current?.zoom(false),
            ],
            [
              Globe2,
              t('Yörünge görünümüne dön', 'Reset orbital view'),
              () => controls.current?.reset(),
            ],
          ] as const
        ).map(([Icon, label, action]) => (
          <button
            disabled={!ready || !!target}
            key={label}
            onClick={action}
            aria-label={label}
            title={label}
          >
            <Icon size={18} strokeWidth={1.5} />
          </button>
        ))}
      </div>
      <div className="nav-guide" id="earth-instructions">
        {target ? (
          <span role="status">
            <Orbit />
            {arrived
              ? t('Harita açılıyor…', 'Opening map…')
              : t('Seçilen konuma uçuluyor', 'Flying to your location')}
          </span>
        ) : (
          <span>
            <MousePointer2 />
            {t(
              'Dünya’yı döndür ve keşfetmek istediğin bölgeye tıkla.',
              'Rotate Earth and select the region you want to explore.',
            )}
          </span>
        )}
      </div>
      {polar && (
        <div className="polar-note" role="status">
          {t(
            'Mercator haritasında açmak için 85° kuzey ve güney arasında bir nokta seçin.',
            'Choose a point between 85° north and south to open it in the Mercator map.',
          )}
        </div>
      )}
      <footer className="nav-footer">
        <div>
          <Crosshair size={13} />
          <span>{position ? coords(position, 3) : '–'}</span>
        </div>
        <a
          href={
            target
              ? 'https://www.earthdata.nasa.gov/data/tools/gibs'
              : 'https://sos.noaa.gov/catalog/datasets/blue-marble/'
          }
          target="_blank"
          rel="noreferrer"
        >
          {target ? 'NASA / USGS · Landsat WELD' : 'NASA · Blue Marble'}
          <ArrowUpRight size={11} />
        </a>
        <button className="skip-orbit" onClick={openMap}>
          {t('Girişi atla', 'Skip intro')}
          <SkipForward size={13} />
        </button>
      </footer>
    </section>
  )
}
