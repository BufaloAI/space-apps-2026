import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import {
  Globe2,
  LandPlot,
  ChartNoAxesCombined,
  Sprout,
  Database,
  BrainCircuit,
  RotateCcw,
  Sun,
  Moon,
  Menu,
  X,
  PanelLeftClose,
  PanelLeftOpen,
} from 'lucide-react'
import Intro from './Intro'
import { Brand, LanguageSwitch } from './ui'
import {
  read,
  save,
  type Language,
  type Page,
  type Field,
  type View,
} from './model'
import './style.css'
const MapWorkspace = lazy(() => import('./MapWorkspace')),
  Analytics = lazy(() => import('./Analytics'))
const pages: Page[] = [
  'intro',
  'map',
  'fields',
  'climate',
  'indicators',
  'data',
  'ai',
]
const sessionKey = 'bufaloai.intro.completed'
function setEntry(completed: boolean) {
  try {
    if (completed) sessionStorage.setItem(sessionKey, '1')
    else sessionStorage.removeItem(sessionKey)
  } catch {}
}
function route(): Page {
  const path = location.pathname.replace(/\/+$/, '')
  const sub = location.hash.slice(1) as Page
  if (path === '/intro') return 'intro'
  if (path === '/app')
    return pages.includes(sub) && sub !== 'intro' ? sub : 'map'
  if (pages.includes(sub)) return sub
  try {
    return sessionStorage.getItem(sessionKey) === '1' ? 'map' : 'intro'
  } catch {
    return 'intro'
  }
}
function urlFor(page: Page) {
  return page === 'intro' ? '/intro' : page === 'map' ? '/app' : '/app#' + page
}
function App() {
  const [page, setPage] = useState<Page>(route),
    [lang, setLang] = useState<Language>(() => read('language', 'tr')),
    [theme, setTheme] = useState(() => read('theme', 'light')),
    [fields, setFields] = useState<Field[]>(() => read('fields', [])),
    [selected, select] = useState(() => read('selected', '')),
    [initial, setInitial] = useState<View | null>(null),
    [open, setOpen] = useState(false),
    [collapsed, setCollapsed] = useState(false),
    [storageError, setStorageError] = useState(false)
  const viewport = useRef<HTMLDivElement | null>(null)
  const [mapReady, setMapReady] = useState(false)
  const t = useCallback(
    (tr: string, en: string) => (lang === 'tr' ? tr : en),
    [lang],
  )
  const go = useCallback((p: Page, v?: View) => {
    if (v) save('view', v)
    setEntry(p !== 'intro')
    setInitial(v ?? null)
    setMapReady(false)
    setPage(p)
    setOpen(false)
    const url = urlFor(p)
    if (location.pathname + location.hash !== url)
      history.pushState(null, '', url)
  }, [])
  const prepare = useCallback((v: View) => {
    setInitial(v)
    setMapReady(false)
  }, [])
  const land = useCallback(
    (v: View) => {
      go('map', v)
      requestAnimationFrame(() =>
        viewport.current
          ?.querySelector('canvas')
          ?.focus({ preventScroll: true }),
      )
    },
    [go],
  )
  const ready = useCallback(() => setMapReady(true), [])
  useEffect(() => {
    const sync = () => {
      const p = route()
      if (p !== 'intro') setEntry(true)
      history.replaceState(null, '', urlFor(p))
      setPage(p)
      setInitial(null)
      setOpen(false)
      setMapReady(false)
    }
    sync()
    window.addEventListener('popstate', sync)
    window.addEventListener('hashchange', sync)
    return () => {
      window.removeEventListener('popstate', sync)
      window.removeEventListener('hashchange', sync)
    }
  }, [])
  useEffect(() => {
    document.documentElement.lang = lang
    document.documentElement.dataset.theme = theme
    document.title = 'BUFALOAI — ' + t('Dünya Atlası', 'Earth Atlas')
    save('language', lang)
    save('theme', theme)
  }, [lang, theme, t])
  useEffect(() => {
    setStorageError(!save('fields', fields))
  }, [fields])
  useEffect(() => {
    save('selected', selected)
  }, [selected])
  const toggleLang = () => setLang(lang === 'tr' ? 'en' : 'tr')
  const intro = page === 'intro'
  const workspacePage = intro ? 'map' : page
  const items = [
    [Globe2, 'map', t('Dünya atlası', 'Earth atlas')],
    [LandPlot, 'fields', t('Tarlalarım', 'My fields')],
    [ChartNoAxesCombined, 'climate', t('İklim analizi', 'Climate analysis')],
    [
      Sprout,
      'indicators',
      t('Tarımsal göstergeler', 'Agricultural indicators'),
    ],
    [Database, 'data', t('Veri gezgini', 'Data explorer')],
    [BrainCircuit, 'ai', t('Yapay zekâ', 'AI intelligence')],
  ] as const
  return (
    <>
      {intro && (
        <Intro
          lang={lang}
          t={t}
          toggleLang={toggleLang}
          openMap={() => go('map', initial ?? undefined)}
          onPrepare={prepare}
          onLand={land}
          mapReady={mapReady}
          viewport={viewport}
        />
      )}
      {(!intro || initial) && (
        <div
          inert={intro}
          aria-hidden={intro || undefined}
          className={
            'workspace ' +
            (open ? 'drawer-open ' : '') +
            (collapsed ? 'nav-collapsed' : '')
          }
        >
          {open && (
            <button
              className="scrim"
              aria-label={t('Menüyü kapat', 'Close menu')}
              onClick={() => setOpen(false)}
            />
          )}
          <aside className="sidebar">
            <button
              className="brandlink"
              onClick={() => go('intro')}
              aria-label="BUFALOAI"
            >
              <Brand />
            </button>
            <button
              className="close-drawer iconbtn"
              onClick={() => setOpen(false)}
              aria-label={t('Kapat', 'Close')}
            >
              <X />
            </button>
            <div className="kicker">{t('ÇALIŞMA ALANI', 'WORKSPACE')}</div>
            <nav>
              {items.map(([Icon, key, title]) => (
                <button
                  key={key}
                  className={page === key ? 'active' : ''}
                  onClick={() => go(key)}
                >
                  <Icon size={18} />
                  {title}
                </button>
              ))}
            </nav>
            <div className="side-foot">
              <button onClick={() => go('intro')}>
                <RotateCcw size={16} />
                {t('Girişi yeniden oynat', 'Replay intro')}
              </button>
            </div>
          </aside>
          <section className="work-main">
            <header className="work-header">
              <button
                className="drawer-btn iconbtn"
                aria-label={t('Menü', 'Menu')}
                onClick={() => setOpen(true)}
              >
                <Menu />
              </button>
              <button
                className="desktop-mark iconbtn"
                aria-label={t(
                  'Gezinme panelini değiştir',
                  'Toggle navigation panel',
                )}
                onClick={() => setCollapsed(!collapsed)}
              >
                {collapsed ? (
                  <PanelLeftOpen size={16} />
                ) : (
                  <PanelLeftClose size={16} />
                )}
              </button>
              <span>{items.find((i) => i[1] === page)?.[2]}</span>
              <div className="header-end">
                <LanguageSwitch lang={lang} change={toggleLang} />
                <button
                  className="iconbtn"
                  title={t('Temayı değiştir', 'Change theme')}
                  aria-label={t('Temayı değiştir', 'Change theme')}
                  onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')}
                >
                  {theme === 'light' ? <Moon size={17} /> : <Sun size={17} />}
                </button>
              </div>
            </header>
            {storageError && (
              <div role="alert">
                {t(
                  'Tarayıcı kayıt alanı dolu veya erişilemiyor. GeoJSON kopyanızı indirin.',
                  'Browser storage unavailable. Download a GeoJSON copy.',
                )}
              </div>
            )}
            <Suspense
              fallback={
                <div className="workspace-loading" role="status">
                  {t('Çalışma alanı yükleniyor…', 'Loading workspace…')}
                </div>
              }
            >
              {workspacePage === 'fields' || workspacePage === 'map' ? (
                <MapWorkspace
                  t={t}
                  page={workspacePage}
                  surfaceRef={viewport}
                  onMapReady={ready}
                  fields={fields}
                  setFields={setFields}
                  selected={selected}
                  select={select}
                  go={go}
                  initial={initial}
                />
              ) : (
                <Analytics
                  t={t}
                  lang={lang}
                  page={page}
                  fields={fields}
                  selected={selected}
                  select={select}
                />
              )}
            </Suspense>
          </section>
        </div>
      )}
    </>
  )
}
createRoot(document.getElementById('root')!).render(<App />)
