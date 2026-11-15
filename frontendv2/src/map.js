import L from "leaflet";
import "leaflet/dist/leaflet.css";
import satellite from "../assets/cumra.jpg";
import { geometry } from "./selection.js";

const byId = (id) => document.getElementById(id);
const west = 3643690.6845,
  south = 4522482.3483,
  width = 3600,
  height = 2304;
function onImage(x, y) {
  return L.CRS.EPSG3857.unproject(
    L.point(west + x * width, south + (1 - y) * height),
  );
}
export const vertices = [
  [0.482, 0.218],
  [0.581, 0.198],
  [0.665, 0.139],
  [0.679, 0.554],
  [0.519, 0.624],
  [0.494, 0.618],
];
const sample = vertices.map(([x, y]) => onImage(x, y));

export function createParcelMap(onReady, activity) {
  const map = L.map("map", {
    scrollWheelZoom: false,
    zoomSnap: 0.25,
    maxZoom: 19,
  });
  const imagery = L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    {
      attribution:
        "Imagery © Esri, Maxar, Earthstar Geographics and contributors",
      maxZoom: 19,
    },
  ).addTo(map);
  L.imageOverlay(satellite, L.latLngBounds(onImage(0, 1), onImage(1, 0)), {
    pane: "tilePane",
    zIndex: 2,
  }).addTo(map);
  map.attributionControl.setPrefix(false);
  L.control.scale({ imperial: false }).addTo(map);
  const shapes = L.layerGroup().addTo(map);
  let points = sample.slice(),
    mode = "view",
    example = true,
    locked = false,
    previous;

  function hint(text = "") {
    byId("map-hint").textContent = text;
    byId("map-hint").hidden = !text;
  }
  imagery.on("tileerror", () => {
    if (
      mode === "view" &&
      !L.latLngBounds(onImage(0, 1), onImage(1, 0)).contains(map.getBounds())
    )
      hint(
        "Some satellite tiles are unavailable. The sample field is available offline.",
      );
  });
  function refresh() {
    const shape = geometry(points);
    const ready = mode === "view" && !!shape;
    byId("field-name").textContent = example
      ? "Çumra, Konya"
      : points.length
        ? "Selected field"
        : "Select a field";
    byId("area").textContent = shape
      ? shape.area.toLocaleString("en", { maximumFractionDigits: 2 }) + " ha"
      : "—";
    byId("centre").textContent = shape
      ? shape.lat.toFixed(4) + "°, " + shape.lon.toFixed(4) + "°"
      : "—";
    byId("draw").textContent =
      mode === "draw" ? "Finish drawing" : "Draw boundary";
    byId("draw").disabled =
      locked || mode === "edit" || (mode === "draw" && points.length < 3);
    byId("edit").textContent =
      mode === "edit" ? "Save boundary" : "Edit vertices";
    byId("edit").disabled = locked || mode === "draw" || points.length < 3;
    byId("clear").disabled = locked || (!points.length && mode === "view");
    byId("map").classList.toggle("drawing", mode === "draw");
    onReady(ready);
  }
  function render() {
    shapes.clearLayers();
    const polygon = L.polygon(points, {
      color: "#ff9a69",
      weight: 3,
      fillColor: "#ff8957",
      fillOpacity: 0.16,
      interactive: false,
    }).addTo(shapes);
    points.forEach((p, i) => {
      const editing = mode === "edit" && !locked;
      const marker = L.marker(p, {
        icon: L.divIcon({ className: "vertex", iconSize: [9, 9] }),
        draggable: editing,
        interactive: editing || (mode === "draw" && i === 0),
        keyboard: editing,
        title: "Vertex " + (i + 1),
      }).addTo(shapes);
      marker.on("drag", () => {
        points[i] = marker.getLatLng().wrap();
        example = false;
        polygon.setLatLngs(points);
      });
      marker.on("dragend", () => {
        refresh();
        activity();
      });
      marker.on("click", () => {
        if (mode === "draw" && points.length >= 3) finish();
      });
    });
    refresh();
  }
  function fit() {
    map.invalidateSize();
    map.fitBounds(L.latLngBounds(points.length > 1 ? points : sample), {
      padding: [42, 44],
      maxZoom: 17,
      animate: false,
    });
  }
  function finish() {
    if (points.length && !geometry(points)) {
      hint(
        "The boundary must enclose an area. Adjust the corners or start again.",
      );
      render();
      return;
    }
    mode = "view";
    previous = null;
    hint();
    render();
    activity();
  }
  function beginDraw() {
    if (locked) return;
    previous = { points: points.slice(), example };
    mode = "draw";
    points = [];
    example = false;
    hint(
      "Choose at least three corners, then finish drawing. Press Escape to undo.",
    );
    render();
  }
  byId("draw").onclick = () => (mode === "draw" ? finish() : beginDraw());
  byId("edit").onclick = () => {
    if (mode === "edit") return finish();
    previous = { points: points.slice(), example };
    mode = "edit";
    hint(
      "Drag a corner to adjust the boundary, then save. Press Escape to undo.",
    );
    render();
  };
  byId("clear").onclick = () => {
    points = [];
    example = false;
    finish();
  };
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && previous && !locked) {
      points = previous.points;
      example = previous.example;
      finish();
    }
  });
  map.on("click", (event) => {
    if (mode !== "draw" || locked) return;
    if (points.length >= 999)
      return hint(
        "The boundary has reached the 1,000-point limit including its closing point.",
      );
    points.push(event.latlng.wrap());
    render();
    activity();
  });
  map.on("moveend", activity);
  fit();
  render();
  new ResizeObserver(() => map.invalidateSize()).observe(byId("map"));
  return {
    beginDraw,
    sample() {
      if (!locked) {
        points = sample.slice();
        example = true;
        finish();
        fit();
      }
    },
    lock(value) {
      locked = value;
      refresh();
    },
    polygon() {
      const ring = points.map((p) => [p.lng, p.lat]);
      return { type: "Polygon", coordinates: [[...ring, ring[0]]] };
    },
    isExample() {
      return example;
    },
    restore(ring, isExample) {
      if (ring?.length >= 3) {
        points = ring.map(([x, y]) => L.latLng(y, x));
        example = isExample;
        finish();
        fit();
      }
    },
  };
}
