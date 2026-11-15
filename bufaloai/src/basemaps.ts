import type {
  FilterSpecification,
  LayerSpecification,
  Map as GLMap,
  RasterSourceSpecification,
  StyleSpecification,
  SymbolLayerSpecification,
  VectorSourceSpecification,
} from 'maplibre-gl'
import { demoImage, landsat } from './model'

export const layers = [
  ['standard', 'Siyasi harita', 'Political map'],
  ['satellite', 'Uydu görüntüsü', 'Satellite imagery'],
  ['hybrid', 'Hibrit uydu', 'Satellite hybrid'],
  ['topo', 'Topografik harita', 'Topographic map'],
  ['hill', 'Fiziki harita', 'Physical map'],
  ['light', 'Minimal açık', 'Minimal light'],
  ['dark', 'Minimal koyu', 'Minimal dark'],
  ['osm', 'Sokak haritası', 'Street map'],
] as const
export type Base = (typeof layers)[number][0]

export const overlayGroups = [
  ['country', 'Ülke sınırları ve adları', 'Country borders and names'],
  ['regional', 'İdari bölgeler', 'Administrative regions'],
  ['settlements', 'Yerleşim adları', 'Settlement names'],
  ['roads', 'Yollar ve yol adları', 'Roads and road names'],
  ['geographic', 'Coğrafi adlar', 'Geographic names'],
] as const
export type OverlayGroup = (typeof overlayGroups)[number][0]
export type OverlayState = Record<OverlayGroup, boolean>

export function supportsOverlays(base: Base) {
  return base !== 'topo' && base !== 'osm'
}

export function defaultOverlays(base: Base): OverlayState {
  const on = base !== 'satellite' && supportsOverlays(base)
  return {
    country: on,
    regional: on,
    settlements: on,
    roads: on,
    geographic: on,
  }
}

// Match only basemap layers; leave the user's field and drawing layers alone.
export function overlayGroupFor(
  layer: LayerSpecification,
): OverlayGroup | undefined {
  if (layer.id === 'political-countries') return 'country'
  if (!('source-layer' in layer)) return
  const sourceLayer = layer['source-layer']
  if (sourceLayer === 'boundary')
    return /regional|state|boundary_3/.test(layer.id) ? 'regional' : 'country'
  if (sourceLayer === 'place') {
    if (layer.id.includes('country')) return 'country'
    if (/state|region/.test(layer.id)) return 'regional'
    return 'settlements'
  }
  if (
    sourceLayer === 'transportation_name' ||
    (sourceLayer === 'transportation' && !/rail|ferry|pier/.test(layer.id))
  )
    return 'roads'
  if (
    layer.type === 'symbol' &&
    ['water_name', 'waterway', 'mountain_peak', 'park'].includes(
      sourceLayer ?? '',
    )
  )
    return 'geographic'
}

const tiles = 'https://tiles.openfreemap.org'
const overlaySource = 'atlas-overlays'
const osm =
  '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>'
const vectorSource: VectorSourceSpecification = {
  type: 'vector',
  url: tiles + '/planet',
  attribution:
    '<a href="https://openfreemap.org">OpenFreeMap</a> · <a href="https://openmaptiles.org">© OpenMapTiles</a> · ' +
    osm,
}
export const atlas: StyleSpecification = {
  version: 8,
  sources: {
    land: {
      type: 'geojson',
      data: '/assets/countries.json',
      attribution: 'Natural Earth',
    },
  },
  layers: [
    {
      id: 'water',
      type: 'background',
      paint: { 'background-color': '#a9c6cf' },
    },
    {
      id: 'land',
      type: 'fill',
      source: 'land',
      paint: { 'fill-color': '#dce0c9', 'fill-outline-color': '#a4afa0' },
    },
  ],
}

function label(
  id: string,
  sourceLayer: string,
  filter: FilterSpecification,
  minzoom: number,
  size: number,
  placement: 'point' | 'line' = 'point',
): SymbolLayerSpecification {
  return {
    id,
    type: 'symbol',
    source: overlaySource,
    'source-layer': sourceLayer,
    filter,
    minzoom,
    layout: {
      'symbol-placement': placement,
      'symbol-sort-key': ['coalesce', ['get', 'rank'], 100],
      'text-field': ['coalesce', ['get', 'name'], ['get', 'name_en']],
      'text-font': ['Noto Sans Regular'],
      'text-size': size,
      'text-padding': 3,
    },
    paint: {
      'text-color': '#fffdf1',
      'text-halo-color': '#24352e',
      'text-halo-width': 1.5,
    },
  }
}
const satelliteOverlays: LayerSpecification[] = [
  {
    id: 'atlas-roads',
    type: 'line',
    source: overlaySource,
    'source-layer': 'transportation',
    minzoom: 7,
    filter: [
      'match',
      ['get', 'class'],
      [
        'motorway',
        'trunk',
        'primary',
        'secondary',
        'tertiary',
        'minor',
        'service',
        'track',
      ],
      true,
      false,
    ],
    paint: {
      'line-color': '#f4deaf',
      'line-opacity': 0.78,
      'line-width': [
        'interpolate',
        ['linear'],
        ['zoom'],
        7,
        0.45,
        14,
        1.2,
        18,
        2.5,
      ],
    },
  },
  {
    id: 'atlas-regional',
    type: 'line',
    source: overlaySource,
    'source-layer': 'boundary',
    minzoom: 5,
    filter: [
      'all',
      ['>', ['get', 'admin_level'], 2],
      ['<=', ['get', 'admin_level'], 6],
      ['!=', ['get', 'maritime'], 1],
    ],
    paint: {
      'line-color': '#e6d9bf',
      'line-dasharray': [3, 3],
      'line-width': 1.1,
    },
  },
  {
    id: 'atlas-country',
    type: 'line',
    source: overlaySource,
    'source-layer': 'boundary',
    filter: [
      'all',
      ['==', ['get', 'admin_level'], 2],
      ['!=', ['get', 'maritime'], 1],
      ['!=', ['get', 'disputed'], 1],
      ['!', ['has', 'claimed_by']],
    ],
    paint: {
      'line-color': '#fff6d9',
      'line-opacity': 0.85,
      'line-width': ['interpolate', ['linear'], ['zoom'], 0, 0.65, 6, 1.5],
    },
  },
  {
    id: 'atlas-country-disputed',
    type: 'line',
    source: overlaySource,
    'source-layer': 'boundary',
    filter: [
      'all',
      ['==', ['get', 'admin_level'], 2],
      ['==', ['get', 'disputed'], 1],
      ['!=', ['get', 'maritime'], 1],
    ],
    paint: {
      'line-color': '#fff6d9',
      'line-width': 1.2,
      'line-dasharray': [2, 3],
    },
  },
  label(
    'atlas-road-names',
    'transportation_name',
    ['has', 'name'],
    12,
    11,
    'line',
  ),
  label('atlas-water-names', 'water_name', ['has', 'name'], 2, 12),
  label('atlas-stream-names', 'waterway', ['has', 'name'], 10, 11, 'line'),
  label('atlas-peak-names', 'mountain_peak', ['has', 'name'], 10, 11),
  label(
    'atlas-locality',
    'place',
    [
      'match',
      ['get', 'class'],
      ['suburb', 'quarter', 'neighbourhood', 'hamlet', 'village'],
      true,
      false,
    ],
    9,
    11,
  ),
  label(
    'atlas-city',
    'place',
    ['match', ['get', 'class'], ['city', 'town'], true, false],
    4,
    13,
  ),
  label(
    'atlas-region-names',
    'place',
    ['==', ['get', 'class'], 'state'],
    4,
    12,
  ),
  label(
    'atlas-country-names',
    'place',
    ['==', ['get', 'class'], 'country'],
    0,
    14,
  ),
]

export function applyOverlays(map: GLMap, state: OverlayState) {
  if (
    map.getSource('blue-marble') &&
    !map.getSource(overlaySource) &&
    Object.values(state).some(Boolean)
  ) {
    map.addSource(overlaySource, vectorSource)
    const drawing = map
      .getStyle()
      .layers.find(
        (layer) =>
          layer.id === 'field-fill' ||
          layer.id.startsWith('td-') ||
          layer.id.startsWith('terra-draw'),
      )
    for (const layer of satelliteOverlays)
      map.addLayer(structuredClone(layer), drawing?.id)
  }
  for (const layer of map.getStyle().layers) {
    const group = overlayGroupFor(layer)
    if (!group) continue
    const visibility = state[group] ? 'visible' : 'none'
    if (
      (map.getLayoutProperty(layer.id, 'visibility') ?? 'visible') !==
      visibility
    )
      map.setLayoutProperty(layer.id, 'visibility', visibility)
  }
}

function addRaster(
  style: StyleSpecification,
  id: string,
  url: string,
  credit: string,
  maxzoom: number,
  minzoom = 0,
) {
  const source: RasterSourceSpecification = {
    type: 'raster',
    tiles: [url],
    tileSize: 256,
    attribution: credit,
    maxzoom,
    minzoom,
  }
  style.sources[id] = source
  style.layers.push({ id, type: 'raster', source: id, minzoom })
}

function satelliteStyle(hybrid: boolean): StyleSpecification {
  const style = structuredClone(atlas)
  style.glyphs = tiles + '/fonts/{fontstack}/{range}.pbf'
  addRaster(
    style,
    'blue-marble',
    'https://gibs.earthdata.nasa.gov/wmts/epsg3857/std/BlueMarble_NextGeneration/default/GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpeg',
    '<a href="https://www.earthdata.nasa.gov/data/tools/gibs">NASA Blue Marble / GIBS</a>',
    8,
  )
  addRaster(
    style,
    'landsat',
    landsat,
    'Landsat WELD (2000–2001) / NASA GIBS',
    12,
    7,
  )
  const { url, west, south, east, north } = demoImage
  style.sources['sample-image'] = {
    type: 'image',
    url,
    coordinates: [
      [west, north],
      [east, north],
      [east, south],
      [west, south],
    ],
  }
  style.layers.push({
    id: 'sample-image',
    type: 'raster',
    source: 'sample-image',
    minzoom: 9,
  })
  if (hybrid) {
    style.sources[overlaySource] = vectorSource
    style.layers.push(...structuredClone(satelliteOverlays))
  }
  return style
}

export async function styleFor(
  base: Base,
  signal: AbortSignal,
): Promise<StyleSpecification> {
  if (base === 'satellite' || base === 'hybrid')
    return satelliteStyle(base === 'hybrid')
  if (base === 'osm' || base === 'topo') {
    const style = structuredClone(atlas)
    if (base === 'osm')
      addRaster(
        style,
        'osm',
        'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
        osm,
        19,
      )
    else
      addRaster(
        style,
        'topo',
        'https://tile.opentopomap.org/{z}/{x}/{y}.png',
        osm +
          ' · SRTM, Sonny · <a href="https://opentopomap.org">OpenTopoMap (CC BY-SA)</a>',
        15,
      )
    return style
  }

  const variant =
    base === 'dark' ? 'dark' : base === 'light' ? 'positron' : 'liberty'
  const response = await fetch(tiles + '/styles/' + variant, { signal })
  if (!response.ok) throw Error('OpenFreeMap style unavailable')
  const style: StyleSpecification = await response.json()
  style.layers = style.layers.filter((layer) => layer.type !== 'fill-extrusion')
  if (base === 'standard') {
    // Country tints fade before the generalized Natural Earth coastlines become visible.
    style.sources['country-areas'] = {
      type: 'geojson',
      data: '/assets/countries.json',
      generateId: true,
      attribution: 'Natural Earth',
    }
    style.layers = style.layers.filter((layer) => layer.id !== 'natural_earth')
    style.layers.splice(1, 0, {
      id: 'political-countries',
      type: 'fill',
      source: 'country-areas',
      maxzoom: 5,
      paint: {
        'fill-color': [
          'match',
          ['%', ['id'], 5],
          0,
          '#d5dbba',
          1,
          '#e5caac',
          2,
          '#bed4c6',
          3,
          '#d6c8cf',
          '#e7ddbd',
        ],
        'fill-opacity': [
          'interpolate',
          ['linear'],
          ['zoom'],
          0,
          0.92,
          3,
          0.8,
          5,
          0,
        ],
      },
    })
  }
  if (base === 'hill') {
    style.sources.elevation = {
      type: 'raster-dem',
      encoding: 'terrarium',
      tileSize: 256,
      maxzoom: 15,
      tiles: [
        'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png',
      ],
      attribution:
        '<a href="https://github.com/tilezen/joerd/blob/master/docs/attribution.md">Mapzen terrain sources</a>',
    }
    const before = style.layers.findIndex(
      (layer) => overlayGroupFor(layer) === 'roads',
    )
    style.layers.splice(before < 0 ? 1 : before, 0, {
      id: 'terrain-shade',
      type: 'hillshade',
      source: 'elevation',
      paint: {
        'hillshade-exaggeration': 0.64,
        'hillshade-shadow-color': '#586148',
        'hillshade-highlight-color': '#fff7df',
      },
    })
  }
  return style
}
