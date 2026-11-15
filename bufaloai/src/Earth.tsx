import { useEffect, useRef, useState, type RefObject } from 'react'
import type * as Cesium from 'cesium'
import 'cesium/Build/Cesium/Widgets/CesiumWidget/CesiumWidget.css'
import { landsat, read, type Translate, type View } from './model'

declare global {
  interface Window {
    Cesium: typeof Cesium
    CESIUM_BASE_URL: string
  }
}

let library: Promise<typeof Cesium> | undefined
function loadCesium() {
  if (window.Cesium) return Promise.resolve(window.Cesium)
  if (!library) {
    window.CESIUM_BASE_URL = '/cesium/'
    library = new Promise((resolve, reject) => {
      const script = document.createElement('script')
      script.src = '/cesium/Cesium.js'
      script.onload = () => resolve(window.Cesium)
      script.onerror = () => {
        library = undefined
        reject(Error('Cesium'))
      }
      document.head.append(script)
    })
  }
  return library
}

export type OrbitControls = {
  zoom: (closer: boolean) => void
  reset: () => void
}
type Props = {
  t: Translate
  controls: RefObject<OrbitControls | null>
  viewport: RefObject<HTMLDivElement | null>
  onPick: (view: View) => void
  onInspect: (center: [number, number]) => void
  onReady: () => void
  onArrive: () => void
  onPolar: () => void
  onApproach: () => void
}
const radius = 6378137
const nadir = { heading: 0, pitch: -Math.PI / 2, roll: 0 }

export default function Earth(props: Props) {
  const host = useRef<HTMLDivElement>(null),
    credits = useRef<HTMLDivElement>(null)
  const latest = useRef(props)
  latest.current = props
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading')
  useEffect(() => {
    let widget: Cesium.CesiumWidget | undefined,
      disposed = false,
      flying = false
    function fail() {
      if (disposed) return
      setStatus('error')
      if (flying) latest.current.onArrive()
    }
    async function start() {
      const C = await loadCesium()
      if (disposed || !host.current) return
      const w = new C.CesiumWidget(host.current, {
        baseLayer: false,
        scene3DOnly: true,
        shouldAnimate: false,
        requestRenderMode: true,
        maximumRenderTimeChange: Infinity,
        showRenderLoopErrors: false,
        creditContainer: credits.current!,
        contextOptions: { webgl: { alpha: true, antialias: true } },
      })
      widget = w
      const scene = w.scene,
        camera = w.camera,
        ctl = scene.screenSpaceCameraController
      scene.backgroundColor = C.Color.fromCssColorString('#0b100f')
      if (scene.sun) scene.sun.show = false
      if (scene.moon) scene.moon.show = false
      scene.globe.baseColor = C.Color.fromCssColorString('#24352e')
      scene.globe.enableLighting = true
      scene.globe.vertexShadowDarkness = 0.22
      scene.globe.lightingFadeInDistance = radius + 1800000
      scene.globe.lightingFadeOutDistance = radius + 220000
      scene.globe.dynamicAtmosphereLighting = true
      scene.globe.dynamicAtmosphereLightingFromSun = true
      scene.globe.atmosphereSaturationShift = -0.35
      if (scene.skyAtmosphere) {
        scene.skyAtmosphere.brightnessShift = -0.08
        scene.skyAtmosphere.saturationShift = -0.28
      }
      scene.fog.enabled = false
      ctl.enableTilt = false
      ctl.enableLook = false
      ctl.minimumZoomDistance = 3500
      ctl.maximumZoomDistance = 85000000
      ctl.inertiaSpin = 0.72
      ctl.inertiaZoom = 0.65
      // Resolve clicks against the visible globe, including terrain when enabled.
      const pick = (point: Cesium.Cartesian2) => {
        const ray = camera.getPickRay(point)
        const xyz = ray && scene.globe.pick(ray, scene)
        if (!xyz) return
        const geo = C.Cartographic.fromCartesian(xyz)
        return [
          C.Math.toDegrees(geo.longitude),
          C.Math.toDegrees(geo.latitude),
        ] as [number, number]
      }
      const centerPixel = () =>
        new C.Cartesian2(w.canvas.clientWidth / 2, w.canvas.clientHeight / 2)
      const fovy = () =>
        (camera.frustum as Cesium.PerspectiveFrustum).fovy ?? Math.PI / 3
      const home = () => {
        const center = read<View | null>('view', null)?.center ?? [24, 18]
        const pixelRadius = Math.min(
          w.canvas.clientWidth * 0.43,
          w.canvas.clientHeight * 0.405,
        )
        const focal = w.canvas.clientHeight / (2 * Math.tan(fovy() / 2))
        const altitude = radius * (Math.hypot(1, focal / pixelRadius) - 1)
        camera.setView({
          destination: C.Cartesian3.fromDegrees(center[0], center[1], altitude),
          orientation: nadir,
        })
        latest.current.onInspect([center[0], center[1]])
      }
      w.clock.currentTime = C.JulianDate.fromIso8601('2024-03-20T14:35:00Z')
      w.resize()
      home()
      const credit = { credit: 'NASA / GSFC · NOAA Science On a Sphere' }
      const texture = await C.SingleTileImageryProvider.fromUrl(
        '/assets/blue-marble.jpg',
        credit,
      ).catch(() =>
        C.SingleTileImageryProvider.fromUrl(
          '/assets/blue-marble-small.jpg',
          credit,
        ),
      )
      if (disposed) return
      const base = w.imageryLayers.addImageryProvider(texture)
      base.brightness = 0.97
      base.saturation = 0.88
      base.contrast = 1.1
      const detail = w.imageryLayers.addImageryProvider(
        new C.UrlTemplateImageryProvider({
          url: landsat,
          tilingScheme: new C.WebMercatorTilingScheme(),
          maximumLevel: 12,
          credit: 'Landsat WELD / NASA GIBS',
        }),
      )
      detail.show = false
      scene.preRender.addEventListener(() => {
        const near = camera.positionCartographic.height < 900000
        if (detail.show !== near) detail.show = near
      })
      if (import.meta.env.VITE_CESIUM_ION_TOKEN) {
        C.Ion.defaultAccessToken = import.meta.env.VITE_CESIUM_ION_TOKEN
        C.createWorldTerrainAsync()
          .then((terrain) => {
            if (!disposed) w.terrainProvider = terrain
          })
          .catch(() => {})
      }
      const zoom = (closer: boolean) => {
        if (flying) return
        const amount = camera.positionCartographic.height * 0.32
        if (closer)
          camera.zoomIn(
            Math.min(
              amount,
              Math.max(
                0,
                camera.positionCartographic.height - ctl.minimumZoomDistance,
              ),
            ),
          )
        else
          camera.zoomOut(
            Math.min(
              amount,
              Math.max(
                0,
                ctl.maximumZoomDistance - camera.positionCartographic.height,
              ),
            ),
          )
        scene.requestRender()
      }
      latest.current.controls.current = {
        zoom,
        reset: () => {
          if (!flying) {
            home()
            scene.requestRender()
          }
        },
      }
      function travel(position: Cesium.Cartesian2) {
        if (flying) return
        const center = pick(position)
        if (!center) return
        // Web Mercator cannot display the polar caps. Never substitute a different latitude.
        if (Math.abs(center[1]) > 85) {
          latest.current.onPolar()
          return
        }
        flying = true
        ctl.enableInputs = false
        const startHeight = camera.positionCartographic.height
        const startResolution =
          (2 * startHeight * Math.tan(fovy() / 2)) / w.canvas.clientHeight
        const worldMetres =
          2 * Math.PI * radius * Math.cos(C.Math.toRadians(center[1]))
        const currentZoom = Math.log2(worldMetres / (512 * startResolution))
        const target: View = {
          center,
          zoom: Math.min(18, Math.max(11, Math.ceil(currentZoom) + 1)),
        }
        latest.current.onPick(target)
        const [lon, lat] = center
        const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches
        const approach = () => {
          if (disposed) return
          latest.current.onApproach()
          // Fit the Cesium viewport to the actual, already-mounted MapLibre canvas.
          const rect = latest.current.viewport.current?.getBoundingClientRect()
          if (rect && host.current) {
            const frame = host.current.parentElement!
            frame.style.left = rect.left + 'px'
            frame.style.top = rect.top + 'px'
            frame.style.width = rect.width + 'px'
            frame.style.height = rect.height + 'px'
          }
          const width = rect?.width ?? w.canvas.clientWidth,
            height = rect?.height ?? w.canvas.clientHeight
          const fov =
            (camera.frustum as Cesium.PerspectiveFrustum).fov ?? Math.PI / 3
          const finalFovy =
            width > height
              ? 2 * Math.atan((Math.tan(fov / 2) * height) / width)
              : fov
          // MapLibre uses a 512-pixel world at zoom zero. Both cameras finish north-up.
          const metresPerPixel =
            (2 * Math.PI * radius * Math.cos(C.Math.toRadians(lat))) /
            (512 * 2 ** target.zoom)
          const terrain =
            scene.globe.getHeight(C.Cartographic.fromDegrees(lon, lat)) ?? 0
          const altitude =
            terrain + (metresPerPixel * height) / (2 * Math.tan(finalFovy / 2))
          camera.flyTo({
            destination: C.Cartesian3.fromDegrees(lon, lat, altitude),
            orientation: nadir,
            duration: reduced ? 0 : 2.6,
            easingFunction: C.EasingFunction.SINUSOIDAL_IN_OUT,
            complete: latest.current.onArrive,
          })
        }
        camera.flyTo({
          destination: C.Cartesian3.fromDegrees(
            lon,
            lat,
            Math.min(startHeight, 650000),
          ),
          orientation: nadir,
          duration: reduced ? 0 : 1.6,
          easingFunction: C.EasingFunction.SINUSOIDAL_IN_OUT,
          complete: () => {
            if (!disposed)
              requestAnimationFrame(() => {
                if (!disposed) approach()
              })
          },
        })
      }
      let down: Cesium.Cartesian2 | undefined,
        dragged = false,
        lastInspect = 0
      const events = w.screenSpaceEventHandler
      events.setInputAction((e: { position: Cesium.Cartesian2 }) => {
        down = C.Cartesian2.clone(e.position)
        dragged = false
      }, C.ScreenSpaceEventType.LEFT_DOWN)
      events.setInputAction((e: { endPosition: Cesium.Cartesian2 }) => {
        if (flying) return
        if (down && C.Cartesian2.distance(down, e.endPosition) > 5)
          dragged = true
        if (performance.now() - lastInspect < 70) return
        lastInspect = performance.now()
        const point = pick(e.endPosition)
        if (point) latest.current.onInspect(point)
      }, C.ScreenSpaceEventType.MOUSE_MOVE)
      events.setInputAction((e: { position: Cesium.Cartesian2 }) => {
        if (!dragged) travel(e.position)
        down = undefined
      }, C.ScreenSpaceEventType.LEFT_CLICK)
      const key = (e: KeyboardEvent) => {
        if (flying) return
        const step = 0.11
        switch (e.key) {
          case 'Enter':
            travel(centerPixel())
            break
          case 'ArrowLeft':
            camera.rotateLeft(step)
            break
          case 'ArrowRight':
            camera.rotateRight(step)
            break
          case 'ArrowUp':
            camera.rotateUp(step)
            break
          case 'ArrowDown':
            camera.rotateDown(step)
            break
          case '+':
          case '=':
            zoom(true)
            break
          case '-':
            zoom(false)
            break
          default:
            return
        }
        e.preventDefault()
        scene.requestRender()
        const p = pick(centerPixel())
        if (p && !flying) latest.current.onInspect(p)
      }
      w.canvas.tabIndex = 0
      w.canvas.setAttribute('aria-labelledby', 'earth-title')
      w.canvas.setAttribute('aria-describedby', 'earth-instructions')
      w.canvas.onkeydown = key
      scene.renderError.addEventListener(fail)
      setStatus('ready')
      latest.current.onReady()
      scene.requestRender()
    }
    start().catch(fail)
    return () => {
      disposed = true
      latest.current.controls.current = null
      if (widget && !widget.isDestroyed()) widget.destroy()
    }
  }, [])
  return (
    <>
      <div ref={host} className="cesium-host" />
      <div ref={credits} className="earth-credits" />
      {status !== 'ready' && (
        <div className="earth-status" role="status">
          {status === 'loading'
            ? props.t('Dünya yükleniyor…', 'Loading Earth…')
            : props.t(
                '3B görünüm açılamadı. Haritayı açarak devam edebilirsiniz.',
                '3D view unavailable. Open the map to continue.',
              )}
        </div>
      )}
    </>
  )
}
