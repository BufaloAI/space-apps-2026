"""İki dilli metin: Türkçe `str` gibi davranır (botlar, önbellek, JSON aynı kalır) ve `.en` İngilizcesini taşır.

Kullanıcıya giden her cümle üretildiği yerde iki dilde yazılır; çıktıya `*_en` alanları `en_list` / `with_en`
ile eklenir. Frontend İngilizce modda `*_en` alanını, yoksa Türkçesini gösterir.
"""
from __future__ import annotations


class Bi(str):
    en: str

    def __new__(cls, tr: str, en: str) -> "Bi":
        obj = super().__new__(cls, tr)
        obj.en = en
        return obj

    def __reduce__(self):  # kopyalama/pickle'da İngilizce kaybolmasın
        return (Bi, (str(self), self.en))


def en(x) -> str:
    return getattr(x, "en", x)


def en_list(xs) -> list[str]:
    return [en(x) for x in xs]


def with_en(d: dict, keys: tuple[str, ...]) -> dict:
    """d[k] iki dilliyse d[k + '_en'] ekler."""
    for k in keys:
        if k in d and isinstance(d[k], Bi):
            d[k + "_en"] = d[k].en
    return d


SEVERITY_EN = {"yüksek": "high", "orta": "medium", "düşük": "low", "bilinmiyor": "unknown"}
