"""Başka bilgisayardan (WhatsApp, USB...) gelen ham veri dosyalarını mevcut veriye ekler; tekrarları atar.

    uv run python -m ml.data_load.merge ~/İndirilenler/MOD13Q1.parquet ~/İndirilenler/MYD13Q1.parquet
    uv run python -m ml.data_load.merge ~/İndirilenler/veri_klasoru --out ml/cache/raw

Her dosyadaki `source` sütununa göre `<out>/<source>.parquet` dosyasına eklenir (download.write_raw ile aynı
kural: kaynak + tarla + konum + zaman aynı olan satır bir kez tutulur). Var olan veri silinmez.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from .download import KEY_COLS, write_raw


def merge(paths: list[str], out: str = "ml/cache/raw") -> dict[str, dict]:
    files: list[Path] = []
    for p in map(Path, paths):
        files += sorted(p.expanduser().glob("*.parquet")) if p.expanduser().is_dir() else [p.expanduser()]
    report: dict[str, dict] = {}
    for f in files:
        d = pd.read_parquet(f)
        missing = [c for c in KEY_COLS if c not in d.columns]
        if missing:
            print(f"ATLANDI {f.name}: ham veri dosyası değil (eksik sütun: {missing})")
            continue
        for src, g in d.groupby("source"):
            target = Path(out) / f"{src}.parquet"
            before = len(pd.read_parquet(target)) if target.exists() else 0
            write_raw(g, out)
            after = pd.read_parquet(target)
            report[str(src)] = {"tarla": int(after["area_id"].nunique()), "satır": len(after),
                                "eklenen": len(after) - before}
            print(f"{f.name} -> {target}: +{len(after) - before} satır "
                  f"(toplam {len(after)}, {after['area_id'].nunique()} tarla)")
    return report


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", help="parquet dosyaları veya klasörler")
    ap.add_argument("--out", default="ml/cache/raw")
    a = ap.parse_args(argv)
    merge(a.paths, a.out)


if __name__ == "__main__":
    main()
