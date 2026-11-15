import {
  useEffect,
  useRef,
  useState,
  type Dispatch,
  type SetStateAction,
  type RefObject,
} from 'react'
import maplibregl, { type Map as GLMap, type GeoJSONSource } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import {
  TerraDraw,
  TerraDrawPolygonMode,
  TerraDrawRectangleMode,
  TerraDrawSelectMode,
} from 'terra-draw'
import { TerraDrawMapLibreGLAdapter } from 'terra-draw-maplibre-gl-adapter'
import {
  Search,
  Layers3,
  LocateFixed,
  MousePointer2,
  MapPin,
  Pentagon,
  Square,
  Plus,
  Minus,
  X,
  Upload,
  Download,
  Pencil,
  Trash2,
  Check,
  ChartNoAxesCombined,
  Focus,
  ChevronDown,
  LandPlot,
  Globe2,
} from 'lucide-react'
import {
  blankInfo,
  collection,
  coords,
  download,
  globalView,
  hectares,
  importFields,
  locate,
  read,
  save,
  type Field,
  type Page,
  type Translate,
  type View,
} from './model'
import {
  atlas,
  styleFor,
  layers,
  defaultOverlays,
  applyOverlays,
  supportsOverlays,
  overlayGroups,
  type OverlayState,
  type Base,
} from './basemaps'
import { FieldForm, crops } from './FieldForm'
import { Dialog } from './ui'
type Mode = 'explore' | 'point' | 'polygon' | 'rectangle' | 'edit'
type Place = { name: string; center: [number, number] }
type Props = {
  t: Translate
  fields: Field[]
  setFields: Dispatch<SetStateAction<Field[]>>
  selected: string
  select: (id: string) => void
  page: Page
  go: (p: Page) => void
  initial: View | null
  surfaceRef: RefObject<HTMLDivElement | null>
  onMapReady: () => void
}
function focus(map: GLMap | undefined, f: Field) {
  if (!map) return
  const b = new maplibregl.LngLatBounds()
  f.geometry.coordinates.flat().forEach((p) => b.extend([p[0], p[1]]))
  map.fitBounds(b, {
    padding: 60,
    maxZoom: 17,
    duration: matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 450,
  })
}
export default function MapWorkspace(props: Props) {
  const { t, fields, setFields, selected, select, page, go, initial } = props
  const container = props.surfaceRef,
    map = useRef<GLMap>(undefined),
    draw = useRef<TerraDraw>(undefined),
    file = useRef<HTMLInputElement>(null),
    marker = useRef<maplibregl.Marker>(undefined),
    searchAbort = useRef<AbortController>(undefined)
  const [base, setBase] = useState<Base>(() =>
      initial ? 'satellite' : read('base', 'standard'),
    ),
    [menu, setMenu] = useState(false),
    [overrides, setOverrides] = useState(() =>
      read<Partial<Record<Base, OverlayState>>>('overlays', {}),
    ),
    [unavailable, setUnavailable] = useState<Base[]>([]),
    [mode, setMode] = useState<Mode>('explore'),
    [ready, setReady] = useState(false),
    [details, setDetails] = useState<Field | null>(null),
    [deleting, setDeleting] = useState(false),
    [point, setPoint] = useState<number[] | null>(null),
    [message, setMessage] = useState(''),
    [query, setQuery] = useState(''),
    [places, setPlaces] = useState<Place[]>([]),
    [searching, setSearching] = useState(false),
    [fallback, setFallback] = useState(false),
    [geoLoading, setGeoLoading] = useState(false)
  const overlays = overrides[base] ?? defaultOverlays(base)
  const active = fields.find((f) => f.id === selected)
  const latest = useRef({ props, mode })
  latest.current = { props, mode }
  const lastView = useRef<View>(initial || read('view', globalView))
  useEffect(() => {
    const controller = new AbortController()
    let disposed = false
    let m: GLMap
    let td: TerraDraw | undefined
    let readyTimer: ReturnType<typeof setTimeout> | undefined
    setReady(false)
    setFallback(false)
    setMessage('')
    try {
      m = new maplibregl.Map({
        container: container.current!,
        style: atlas,
        center: lastView.current.center,
        zoom: lastView.current.zoom,
        maxPitch: 0,
        dragRotate: false,
        pitchWithRotate: false,
        touchPitch: false,
        attributionControl: { compact: true },
        canvasContextAttributes: { antialias: true },
      })
      m.touchZoomRotate.disableRotation()
      m.keyboard.disableRotation()
      map.current = m
    } catch {
      latest.current.props.onMapReady()
      setMessage(
        latest.current.props.t(
          'WebGL kullanılamıyor. Harita için tarayıcınızda grafik hızlandırmayı açın.',
          'WebGL unavailable. Enable browser graphics acceleration to view the map.',
        ),
      )
      return
    }
    m.on('moveend', () => {
      if (disposed) return
      lastView.current = {
        center: m.getCenter().toArray() as [number, number],
        zoom: m.getZoom(),
      }
      save('view', lastView.current)
    })
    m.on('click', (e) => {
      const { props: p, mode: current } = latest.current
      if (current === 'point') {
        setPoint(e.lngLat.toArray())
        marker.current?.remove()
        marker.current = new maplibregl.Marker({ color: '#769747' })
          .setLngLat(e.lngLat)
          .addTo(m)
      }
      if (current === 'explore' && m.getLayer('field-fill')) {
        const hit = m.queryRenderedFeatures(e.point, {
          layers: ['field-fill'],
        })[0]
        if (hit) p.select(String(hit.properties.id))
      }
    })
    function attach() {
      if (disposed) return
      const p = latest.current.props
      m.addSource('fields', {
        type: 'geojson',
        data: collection(
          p.fields.map((f) => ({
            ...f,
            properties: { ...f.properties, id: f.id },
          })),
        ),
      })
      m.addLayer({
        id: 'field-fill',
        type: 'fill',
        source: 'fields',
        paint: { 'fill-color': '#89b44d', 'fill-opacity': 0.38 },
      })
      m.addLayer({
        id: 'field-border',
        type: 'line',
        source: 'fields',
        paint: { 'line-color': '#3b581d', 'line-width': 2 },
      })
      m.addLayer({
        id: 'selected-border',
        type: 'line',
        source: 'fields',
        filter: ['==', ['get', 'id'], p.selected],
        paint: { 'line-color': '#f2cc55', 'line-width': 4 },
      })
      const styles = {
        fillColor: '#abc96e' as const,
        outlineColor: '#658a28' as const,
      }
      td = new TerraDraw({
        adapter: new TerraDrawMapLibreGLAdapter({
          map: m,
          coordinatePrecision: 7,
        }),
        modes: [
          new TerraDrawPolygonMode({ styles }),
          new TerraDrawRectangleMode({ styles }),
          new TerraDrawSelectMode({
            flags: {
              polygon: {
                feature: {
                  draggable: true,
                  coordinates: {
                    draggable: true,
                    deletable: true,
                    midpoints: true,
                  },
                },
              },
            },
          }),
        ],
      })
      td.start()
      draw.current = td
      td.on('finish', (id, ctx) => {
        if (ctx.mode === 'select') return
        const shape = td?.getSnapshotFeature(id)
        if (shape?.geometry.type !== 'Polygon') return
        const p = latest.current.props
        setDetails({
          type: 'Feature',
          id: crypto.randomUUID(),
          geometry: shape.geometry,
          properties: {
            ...blankInfo,
            name: p.t('Tarla', 'Field') + ' ' + (p.fields.length + 1),
          },
        })
        setMode('explore')
      })
      setReady(true)
      // Wait for cartography to paint; a slow remote tile must not trap the user in orbit.
      const show = () => {
        if (!disposed) {
          clearTimeout(readyTimer)
          latest.current.props.onMapReady()
        }
      }
      m.once('idle', show)
      readyTimer = setTimeout(show, 5500)
    }
    let failed = false
    function loadFallback() {
      if (disposed || failed) return
      failed = true
      setUnavailable((prev) => (prev.includes(base) ? prev : [...prev, base]))
      setFallback(true)
      td?.stop()
      draw.current = undefined
      setReady(false)
      setMode('explore')
      m.off('style.load', attach)
      m.once('style.load', attach)
      m.setStyle(structuredClone(atlas), { diff: false })
    }
    ;(async () => {
      try {
        const style = await styleFor(base, controller.signal)
        if (disposed) return
        m.once('style.load', attach)
        m.setStyle(style, { diff: false })
        let errors = 0
        m.on('error', (e) => {
          if (
            !disposed &&
            'sourceId' in e &&
            e.sourceId !== 'atlas-overlays' &&
            e.sourceId !== 'fields' &&
            ++errors >= 3
          )
            loadFallback()
        })
      } catch {
        loadFallback()
      }
    })()
    return () => {
      disposed = true
      clearTimeout(readyTimer)
      lastView.current = {
        center: m.getCenter().toArray() as [number, number],
        zoom: m.getZoom(),
      }
      save('view', lastView.current)
      controller.abort()
      searchAbort.current?.abort()
      marker.current?.remove()
      marker.current = undefined
      td?.stop()
      m.remove()
      map.current = undefined
      draw.current = undefined
    }
  }, [base])
  useEffect(() => {
    const m = map.current
    if (ready && m?.getSource('fields') && !fallback && supportsOverlays(base))
      applyOverlays(m, overlays)
  }, [ready, base, overrides, fallback])
  useEffect(() => {
    const m = map.current
    if (!ready || !m?.getSource('fields')) return
    ;(m.getSource('fields') as GeoJSONSource).setData(
      collection(
        fields.map((f) => ({
          ...f,
          properties: { ...f.properties, id: f.id },
        })),
      ),
    )
    m.setFilter('selected-border', ['==', ['get', 'id'], selected])
  }, [fields, selected, ready])
  useEffect(() => {
    const d = draw.current
    if (!ready || !d) return
    d.setMode('static')
    d.clear()
    if (mode === 'edit' && active) {
      const id = d.getFeatureId()
      d.addFeatures([
        {
          type: 'Feature',
          id,
          geometry: structuredClone(active.geometry),
          properties: { mode: 'polygon' },
        },
      ])
      d.setMode('select')
      d.selectFeature(id)
    } else if (mode === 'polygon' || mode === 'rectangle') d.setMode(mode)
    if (map.current)
      map.current.getCanvas().style.cursor = mode === 'point' ? 'crosshair' : ''
  }, [mode, ready, selected])
  useEffect(() => {
    if (!message) return
    const id = setTimeout(() => setMessage(''), 8500)
    return () => clearTimeout(id)
  }, [message])
  const pick = (f: Field) => {
    select(f.id)
    setMode('explore')
    focus(map.current, f)
  }
  const choosePlace = (p: Place) => {
    setPlaces([])
    setQuery(p.name)
    map.current?.easeTo({ center: p.center, zoom: 11 })
  }
  async function search() {
    const q = query.trim()
    if (!q) return
    searchAbort.current?.abort()
    const controller = new AbortController()
    searchAbort.current = controller
    setPlaces([])
    setSearching(true)
    try {
      const pair = q.split(',').map((x) => Number(x.trim()))
      if (
        pair.length === 2 &&
        pair.every(Number.isFinite) &&
        Math.abs(pair[0]) <= 90 &&
        Math.abs(pair[1]) <= 180
      ) {
        choosePlace({ name: q, center: [pair[1], pair[0]] })
        return
      }
      const res = await fetch(
        'https://photon.komoot.io/api/?limit=5&q=' + encodeURIComponent(q),
        { signal: controller.signal },
      )
      if (!res.ok) throw Error()
      const data = await res.json()
      const found: Place[] = data.features.map(
        (f: {
          geometry: { coordinates: [number, number] }
          properties: { name: string; city?: string; country?: string }
        }) => ({
          name: [f.properties.name, f.properties.city, f.properties.country]
            .filter(Boolean)
            .join(', '),
          center: f.geometry.coordinates,
        }),
      )
      setPlaces(found)
      if (!found.length)
        setMessage(t('Konum bulunamadı.', 'No matching location.'))
    } catch {
      if (!controller.signal.aborted)
        setMessage(
          t(
            'Arama servisine ulaşılamadı. Enlem, boylam girebilirsiniz.',
            'Search unavailable. Enter latitude, longitude instead.',
          ),
        )
    } finally {
      if (!controller.signal.aborted) setSearching(false)
    }
  }
  const locked = ['polygon', 'rectangle', 'edit'].includes(mode)
  const buttons = [
    ['explore', MousePointer2, t('Keşfet', 'Explore')],
    ['point', MapPin, t('Nokta', 'Point')],
    ['polygon', Pentagon, t('Çokgen', 'Polygon')],
    ['rectangle', Square, t('Dikdörtgen', 'Rectangle')],
  ] as const
  async function loadFile(f?: File) {
    if (!f) return
    try {
      const added = importFields(
        JSON.parse(await f.text()),
        t('Tarla', 'Field'),
      )
      setFields((prev) => [...prev, ...added])
      pick(added[0])
      setMessage(t('Tarlalar içe aktarıldı.', 'Fields imported.'))
    } catch {
      setMessage(
        t(
          'Geçerli bir Polygon veya MultiPolygon GeoJSON dosyası seçin.',
          'Choose a valid Polygon or MultiPolygon GeoJSON file.',
        ),
      )
    }
    if (file.current) file.current.value = ''
  }
  const cropLabel = (code: string) => {
    const crop = crops.find((c) => c[0] === code)
    return crop ? t(crop[1], crop[2]) : code
  }
  const button = (
    label: string,
    Icon: typeof Plus,
    action: () => void,
    disabled = false,
  ) => (
    <button
      className="iconbtn"
      type="button"
      title={label}
      aria-label={label}
      onClick={action}
      disabled={disabled}
    >
      <Icon size={17} />
    </button>
  )
  return (
    <div className={'geo-layout ' + (page === 'fields' ? 'with-list' : '')}>
      <input
        ref={file}
        type="file"
        accept=".json,.geojson"
        hidden
        onChange={(e) => loadFile(e.target.files?.[0])}
      />
      {page === 'fields' && (
        <aside className="fields-panel">
          <div className="section-title">
            <h2>{t('Tarlalarım', 'My fields')}</h2>
            <span>{fields.length}</span>
          </div>
          <button
            className="primary"
            disabled={!ready}
            onClick={() => setMode('polygon')}
          >
            <Plus size={16} />
            {t('Tarla çiz', 'Draw field')}
          </button>
          <div className="fields-list">
            {fields.map((f) => (
              <button
                key={f.id}
                className={selected === f.id ? 'picked' : ''}
                onClick={() => pick(f)}
              >
                <LandPlot />
                <span>
                  {f.properties.name}
                  <small>
                    {hectares(f).toLocaleString(undefined, {
                      maximumFractionDigits: 2,
                    })}{' '}
                    ha
                  </small>
                </span>
              </button>
            ))}
            {!fields.length && (
              <p>
                {t(
                  'İlk tarlanızı haritada çizin veya bir GeoJSON dosyası açın.',
                  'Draw your first field on the map or open a GeoJSON file.',
                )}
              </p>
            )}
          </div>
          <div className="file-actions">
            <button
              aria-label={t('GeoJSON içe aktar', 'Import GeoJSON')}
              title={t('GeoJSON içe aktar', 'Import GeoJSON')}
              onClick={() => file.current?.click()}
            >
              <Upload size={15} />
              GeoJSON
            </button>
            <button
              aria-label={t('GeoJSON dışa aktar', 'Export GeoJSON')}
              title={t('GeoJSON dışa aktar', 'Export GeoJSON')}
              disabled={!fields.length}
              onClick={() =>
                download(
                  'bufaloai-fields.geojson',
                  JSON.stringify(collection(fields)),
                  'application/geo+json',
                )
              }
            >
              <Download size={15} />
              GeoJSON
            </button>
          </div>
          <small>
            {t(
              'Kullanıcı çizimleridir. Alanlar yaklaşık olarak hesaplanır.',
              'User-drawn boundaries with approximate areas.',
            )}
          </small>
        </aside>
      )}
      <section className="map-area">
        <div className="map-canvas" ref={container} />
        <div className="map-tools">
          <div className="search-wrap">
            <form
              className="search"
              onSubmit={(e) => {
                e.preventDefault()
                search()
              }}
            >
              <button aria-label={t('Ara', 'Search')}>
                <Search size={17} />
              </button>
              <input
                aria-label={t('Konum ara', 'Search locations')}
                placeholder={t(
                  'Yer veya koordinat ara',
                  'Search place or coordinates',
                )}
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value)
                  setPlaces([])
                }}
              />
              {searching && <span>…</span>}
            </form>
            {places.length > 0 && (
              <div className="places">
                {places.map((p, i) => (
                  <button key={i} onClick={() => choosePlace(p)}>
                    {p.name}
                  </button>
                ))}
              </div>
            )}
          </div>
          <div className="layer-wrap">
            <button
              className="layer-toggle"
              disabled={locked}
              aria-expanded={menu}
              onClick={() => setMenu(!menu)}
            >
              <Layers3 size={16} />
              {t('Katmanlar', 'Layers')}
              <ChevronDown size={13} />
            </button>
            {menu && (
              <div className="layer-menu atlas-layer-menu">
                <strong>{t('Harita altlığı', 'Basemap')}</strong>
                {layers.map(([k, tr, en]) => (
                  <button
                    className={base === k ? 'selected' : ''}
                    key={k}
                    disabled={unavailable.includes(k)}
                    onClick={() => {
                      setMenu(false)
                      setBase(k)
                      save('base', k)
                    }}
                  >
                    {t(tr, en)}
                    {unavailable.includes(k) ? (
                      <small>{t('Çevrimdışı', 'Offline')}</small>
                    ) : (
                      base === k && <Check size={15} />
                    )}
                  </button>
                ))}
                {supportsOverlays(base) && !fallback && (
                  <fieldset disabled={!ready}>
                    <legend>
                      {t('Sınırlar ve etiketler', 'Boundaries and labels')}
                    </legend>
                    {overlayGroups.map(([id, tr, en]) => (
                      <label key={id}>
                        <input
                          type="checkbox"
                          checked={overlays[id]}
                          onChange={(e) => {
                            const next = {
                              ...overrides,
                              [base]: { ...overlays, [id]: e.target.checked },
                            }
                            setOverrides(next)
                            save('overlays', next)
                          }}
                        />
                        {t(tr, en)}
                      </label>
                    ))}
                  </fieldset>
                )}
                {!supportsOverlays(base) && (
                  <small>
                    {t(
                      'Bu altlıkta etiketler görüntüye işlenmiştir.',
                      'Labels are embedded in this raster basemap.',
                    )}
                  </small>
                )}
              </div>
            )}
          </div>
        </div>
        <div className="mode-tools">
          {buttons.map(([key, Icon, label]) => (
            <button
              title={label}
              aria-label={label}
              aria-pressed={mode === key}
              disabled={!ready}
              key={key}
              onClick={() => {
                setMode(key)
                setMenu(false)
              }}
            >
              <Icon size={18} />
            </button>
          ))}
        </div>
        <div className="zoom-tools">
          {button(t('Yakınlaş', 'Zoom in'), Plus, () => map.current?.zoomIn())}
          {button(t('Uzaklaş', 'Zoom out'), Minus, () =>
            map.current?.zoomOut(),
          )}
          {button(
            t('Konumumu bul', 'Locate me'),
            LocateFixed,
            async () => {
              setGeoLoading(true)
              try {
                const v = await locate()
                map.current?.easeTo({ center: v.center, zoom: v.zoom })
              } catch {
                setMessage(
                  t(
                    'Konum izni alınamadı. Haritadan bir yer seçin.',
                    'Location unavailable. Choose a place on the map.',
                  ),
                )
              } finally {
                setGeoLoading(false)
              }
            },
            geoLoading,
          )}
          {button(t('Küresel görünüm', 'Global view'), Globe2, () =>
            map.current?.easeTo(globalView),
          )}
        </div>
        {fallback && (
          <span className="fallback">
            {t(
              'Yerel temel harita · Katman çevrimdışı',
              'Local basemap · Layer offline',
            )}
          </span>
        )}
        {(locked || mode === 'point') && (
          <div className="map-hint">
            <span>
              {mode === 'edit'
                ? t('Köşeleri sürükleyin.', 'Drag vertices to edit.')
                : mode === 'polygon'
                  ? t(
                      'Köşeleri işaretleyin. İlk noktaya tıklayarak bitirin.',
                      'Place vertices. Click the first point to finish.',
                    )
                  : mode === 'rectangle'
                    ? t(
                        'Karşılıklı iki köşeyi işaretleyin.',
                        'Click two opposite corners.',
                      )
                    : point
                      ? coords(point, 5)
                      : t(
                          'Koordinat için haritaya tıklayın.',
                          'Click the map for coordinates.',
                        )}
            </span>
            {mode === 'edit' &&
              button(t('Sınırı kaydet', 'Save boundary'), Check, () => {
                const geom = draw.current
                  ?.getSnapshot()
                  .find((f) => f.geometry.type === 'Polygon')?.geometry
                if (active && geom?.type === 'Polygon') {
                  setFields((prev) =>
                    prev.map((f) =>
                      f.id === active.id ? { ...f, geometry: geom } : f,
                    ),
                  )
                  setMode('explore')
                }
              })}
            {button(t('Kapat', 'Close'), X, () => {
              setMode('explore')
              setPoint(null)
              marker.current?.remove()
            })}
          </div>
        )}
        {active && mode === 'explore' && (
          <div className="field-card">
            <div className="section-title">
              <strong>{active.properties.name}</strong>
              {button(t('Seçimi kaldır', 'Clear selection'), X, () =>
                select(''),
              )}
            </div>
            <p>
              {hectares(active).toLocaleString(undefined, {
                maximumFractionDigits: 2,
              })}{' '}
              ha ·{' '}
              {cropLabel(active.properties.crop) ||
                active.properties.crop ||
                t('Ürün seçilmedi', 'No crop selected')}
            </p>
            <div className="field-tools">
              {button(t('Tarlaya git', 'Locate field'), Focus, () =>
                focus(map.current, active),
              )}
              {button(t('Bilgileri düzenle', 'Edit details'), Pencil, () =>
                setDetails(active),
              )}
              {button(
                t('Sınırı düzenle', 'Edit boundary'),
                Pentagon,
                () => setMode('edit'),
                !ready,
              )}
              {button(
                t('İklim analizi', 'Climate analysis'),
                ChartNoAxesCombined,
                () => go('climate'),
              )}
              {button(t('Tarlayı sil', 'Delete field'), Trash2, () =>
                setDeleting(true),
              )}
            </div>
          </div>
        )}
        {message && (
          <div className="map-message" role="status">
            {message}
          </div>
        )}
      </section>
      {details && (
        <FieldForm
          field={details}
          t={t}
          close={() => setDetails(null)}
          commit={(f) => {
            setFields((prev) =>
              prev.some((p) => p.id === f.id)
                ? prev.map((p) => (p.id === f.id ? f : p))
                : [...prev, f],
            )
            select(f.id)
            setDetails(null)
          }}
        />
      )}
      {deleting && active && (
        <Dialog
          t={t}
          title={t('Tarla silinsin mi?', 'Delete field?')}
          close={() => setDeleting(false)}
        >
          <p>{active.properties.name}</p>
          <button
            className="primary"
            onClick={() => {
              setFields((prev) => prev.filter((f) => f.id !== selected))
              select('')
              setDeleting(false)
            }}
          >
            {t('Tarlayı sil', 'Delete field')}
          </button>
        </Dialog>
      )}
    </div>
  )
}
