"""planetary.py testleri (ağ ve COG okuma sahte)."""
import math

import pandas as pd

from ml.data_load import clean, planetary as P


def test_sinusoidal_known_values():
    assert P.sinusoidal(0, 0) == (0.0, 0.0)
    x, y = P.sinusoidal(42.03, -93.74)
    assert math.isclose(y, P.R_SINU * math.radians(42.03))
    assert math.isclose(x, P.R_SINU * math.radians(-93.74) * math.cos(math.radians(42.03)))
    assert -8_000_000 < x < -7_500_000  # h11 karosu civarı


def _item(src, day, tile="h11v04"):
    d = pd.Timestamp(day)
    return {"id": f"{src}.A{d.year}{d.day_of_year:03d}.{tile}.061.x",
            "properties": {"start_datetime": d.strftime("%Y-%m-%dT00:00:00Z")},
            "assets": {b: {"href": f"https://acct.blob.core.windows.net/cont/{src}/{tile}/{day}/{b}.tif"}
                       for b in P.BANDS}}


def test_fetch_area_terra_aqua_tile_edges_and_skip(monkeypatch):
    items = [_item("MOD13Q1", "2024-06-09"), _item("MYD13Q1", "2024-06-17"),
             _item("MOD13Q1", "2024-06-09", tile="h10v04"),  # komşu karo: nokta dışında
             _item("MOD13Q1", "2024-06-25")]
    monkeypatch.setattr(P, "search", lambda *a, **k: items)

    class Tok:
        def sign(self, href):
            return href

    def fake_pixel(href, x, y):
        if "h10v04" in href:
            return None
        return 0.0 if "reliability" in href else {"2024-06-09": 5038.0, "2024-06-17": 8721.0,
                                                  "2024-06-25": 9320.0}[href.split("/")[-2]]

    monkeypatch.setattr(P, "read_pixel", fake_pixel)
    df = P.fetch_area("ia", 42.03, -93.74, "2024-06-01", "2024-07-01", tokens=Tok(), workers=2,
                      skip_dates={("MOD13Q1", pd.Timestamp("2024-06-25"))})
    assert sorted(df["source"]) == ["MOD13Q1", "MYD13Q1"]  # 06-25 önceden vardı, atlandı; komşu karo yok
    assert df["time"].dtype == "datetime64[ns]" and df.loc[df.source == "MOD13Q1", "250m_16_days_NDVI"].item() == 5038
    long, _ = clean.clean_frame(df, hours=None)  # temizleyici ORNL ile aynı şekilde okur
    nd = long[long["var"].astype(str) == "ndvi"]["value"].sort_values().tolist()
    assert nd == [0.5038, 0.8721]


def test_token_cache_refreshes_once(monkeypatch):
    calls = []
    monkeypatch.setattr(P, "_json", lambda url, *a, **k: calls.append(url) or {"token": "sv=1&sig=x",
                                                                              "msft:expiry": "2099-01-01T00:00:00Z"})
    t = P.TokenCache()
    a = t.sign("https://acct.blob.core.windows.net/cont/a.tif")
    b = t.sign("https://acct.blob.core.windows.net/cont/b.tif")
    assert a.endswith("?sv=1&sig=x") and b.endswith("?sv=1&sig=x") and len(calls) == 1


def test_search_follows_pagination(monkeypatch):
    pages = [{"features": [1, 2], "links": [{"rel": "next", "href": "u2", "method": "POST", "body": {"t": 2}}]},
             {"features": [3], "links": []}]
    monkeypatch.setattr(P, "_json", lambda url, data=None, *a, **k: pages.pop(0))
    assert P.search(42, -93, "2024-01-01", "2024-12-31") == [1, 2, 3]
