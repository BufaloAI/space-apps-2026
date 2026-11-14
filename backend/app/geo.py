"""Parsel (tarla) poligonu doğrulama. Girdi: GeoJSON, koordinatlar [boylam, enlem] (WGS84)."""
from __future__ import annotations

import math
from dataclasses import dataclass

from shapely.geometry import Polygon
from shapely.validation import explain_validity


class PolygonError(ValueError):
    """Kullanıcıya gösterilebilecek (Türkçe) poligon hatası."""


@dataclass(frozen=True)
class Parcel:
    ring: tuple[tuple[float, float], ...]  # dış halka, kapalı: [(boylam, enlem), ...]
    area_ha: float
    centroid: tuple[float, float]  # (boylam, enlem)

    def to_geojson(self) -> dict:
        return {"type": "Polygon", "coordinates": [[list(p) for p in self.ring]]}


def _is_number(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _area_ha(poly: Polygon) -> float:
    """Küçük alanlar için yaklaşık alan (hektar): merkeze göre yerel düzlem izdüşümü."""
    lon0, lat0 = poly.centroid.x, poly.centroid.y
    kx = 111_320.0 * math.cos(math.radians(lat0))
    ky = 110_540.0
    projected = Polygon([((x - lon0) * kx, (y - lat0) * ky) for x, y in poly.exterior.coords])
    return projected.area / 10_000.0


def parse_polygon(
    geojson: object,
    *,
    max_area_ha: float = 5000.0,
    min_area_ha: float = 0.01,
    max_vertices: int = 1000,
) -> Parcel:
    if not isinstance(geojson, dict):
        raise PolygonError("Poligon bir GeoJSON nesnesi olmalı.")
    geom = geojson
    if geom.get("type") == "Feature":
        geom = geom.get("geometry") or {}
    if geom.get("type") != "Polygon":
        raise PolygonError("Yalnızca tek bir Polygon desteklenir (MultiPolygon veya başka tür olmaz).")

    rings = geom.get("coordinates")
    if not isinstance(rings, list) or not rings or not isinstance(rings[0], list):
        raise PolygonError("Poligon koordinatları eksik.")
    if len(rings) > 1:
        raise PolygonError("İçinde delik olan poligon desteklenmiyor.")

    ring: list[tuple[float, float]] = []
    for pt in rings[0]:
        if not isinstance(pt, (list, tuple)) or len(pt) < 2 or not (_is_number(pt[0]) and _is_number(pt[1])):
            raise PolygonError("Koordinatlar [boylam, enlem] sayı çiftleri olmalı.")
        lon, lat = float(pt[0]), float(pt[1])
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            raise PolygonError("Koordinat aralık dışında (boylam -180..180, enlem -90..90).")
        ring.append((lon, lat))

    if len(ring) > max_vertices:
        raise PolygonError(f"Poligonda en fazla {max_vertices} nokta olabilir.")
    if len(ring) >= 2 and ring[0] != ring[-1]:
        ring.append(ring[0])  # açık çizilmişse kapat
    if len(set(ring)) < 3:
        raise PolygonError("Poligon en az 3 farklı noktadan oluşmalı.")

    poly = Polygon(ring)
    if not poly.is_valid:
        reason = explain_validity(poly)
        if "Self-intersection" in reason:
            raise PolygonError("Poligon kendini kesiyor; sınırları kesişmeyecek şekilde çizin.")
        raise PolygonError("Geçersiz poligon.")

    area = _area_ha(poly)
    if area < min_area_ha:
        raise PolygonError("Parsel çok küçük.")
    if area > max_area_ha:
        raise PolygonError(f"Parsel çok büyük (en fazla {max_area_ha:g} hektar).")

    return Parcel(ring=tuple(ring), area_ha=area, centroid=(poly.centroid.x, poly.centroid.y))
