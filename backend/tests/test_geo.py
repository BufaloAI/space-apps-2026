import pytest

from app.geo import PolygonError, parse_polygon


def square(lon=29.0, lat=41.0, size=0.01):
    return {
        "type": "Polygon",
        "coordinates": [[[lon, lat], [lon + size, lat], [lon + size, lat + size], [lon, lat + size], [lon, lat]]],
    }


def test_valid_polygon_area_and_centroid():
    p = parse_polygon(square(0.0, 0.0, 0.01))  # ekvatorda ~1.11 km x 1.11 km ~= 123 ha
    assert 120 < p.area_ha < 126
    assert p.centroid == pytest.approx((0.005, 0.005))
    assert p.ring[0] == p.ring[-1]


def test_one_by_one_km_is_about_100_ha():
    d = 1 / 111.32  # ~1 km (ekvatorda)
    p = parse_polygon({"type": "Polygon", "coordinates": [[[0, 0], [d, 0], [d, 1 / 110.54], [0, 1 / 110.54], [0, 0]]]})
    assert p.area_ha == pytest.approx(100, rel=0.02)


def test_feature_wrapper_and_auto_close():
    open_ring = square()["coordinates"][0][:-1]
    p = parse_polygon({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [open_ring]}})
    assert p.ring[0] == p.ring[-1]


@pytest.mark.parametrize(
    "bad, msg",
    [
        ("metin", "GeoJSON"),
        ({"type": "Point", "coordinates": [1, 2]}, "Polygon"),
        ({"type": "MultiPolygon", "coordinates": []}, "Polygon"),
        ({"type": "Polygon", "coordinates": []}, "eksik"),
        ({"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [0, 0]]]}, "3 farklı"),
        ({"type": "Polygon", "coordinates": [[[0, 0], [1, "a"], [1, 0], [0, 0]]]}, "sayı"),
        ({"type": "Polygon", "coordinates": [[[0, 0], [1, True], [1, 0], [0, 0]]]}, "sayı"),
        ({"type": "Polygon", "coordinates": [[[0, 0], [float("nan"), 1], [1, 0], [0, 0]]]}, "sayı"),
        ({"type": "Polygon", "coordinates": [[[0, 0], [200, 1], [1, 0], [0, 0]]]}, "aralık"),
        ({"type": "Polygon", "coordinates": [[[0, 0], [0, 95], [1, 0], [0, 0]]]}, "aralık"),
    ],
)
def test_invalid_inputs_have_turkish_message(bad, msg):
    with pytest.raises(PolygonError, match=msg):
        parse_polygon(bad)


def test_self_intersection_rejected():
    bow = {"type": "Polygon", "coordinates": [[[0, 0], [0.01, 0.01], [0.01, 0], [0, 0.01], [0, 0]]]}
    with pytest.raises(PolygonError, match="kendini kesiyor"):
        parse_polygon(bow)


def test_hole_rejected():
    outer = square()["coordinates"][0]
    hole = [[29.003, 41.003], [29.004, 41.003], [29.004, 41.004], [29.003, 41.003]]
    with pytest.raises(PolygonError, match="delik"):
        parse_polygon({"type": "Polygon", "coordinates": [outer, hole]})


def test_area_limits():
    with pytest.raises(PolygonError, match="çok büyük"):
        parse_polygon(square(size=1.0))
    with pytest.raises(PolygonError, match="çok küçük"):
        parse_polygon(square(size=0.00001))
    assert parse_polygon(square(size=1.0), max_area_ha=1e9).area_ha > 5000


def test_too_many_vertices():
    ring = [[i * 1e-6, (i % 2) * 1e-6] for i in range(10)] + [[0, 0]]
    with pytest.raises(PolygonError, match="en fazla 5 nokta"):
        parse_polygon({"type": "Polygon", "coordinates": [ring]}, max_vertices=5)
