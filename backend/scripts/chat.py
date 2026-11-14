#!/usr/bin/env python3
"""Terminalden Qwen ile sohbet: `sen:` yazarsınız, `qwen:` cevap verir (akarak).

Çalıştırma:   backend/scripts/chat.py   (veya: uv run python backend/scripts/chat.py)
Doğrudan çalıştırınca proje ortamını (.venv) kendisi bulur.
Ayarlar backend/.env dosyasından okunur (VLLM_BASE_URL, VLLM_API_KEY, VLLM_MODEL).

Komutlar:  /temizle  sohbeti sıfırla   |   /cikis veya Ctrl+D  çıkış
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_VENV = ROOT / ".venv"
# Doğrudan çalıştırıldıysa (sistem Python'u) proje ortamıyla yeniden başlat; paketler orada.
if (_VENV / "bin" / "python").exists() and Path(sys.prefix).resolve() != _VENV.resolve():
    os.execv(str(_VENV / "bin" / "python"), [str(_VENV / "bin" / "python"), *sys.argv])

sys.path.insert(0, str(ROOT / "backend"))  # `app` paketi

try:
    from app.config import load_settings  # noqa: E402
    from app.llm import LLM  # noqa: E402
except ModuleNotFoundError as e:
    sys.exit(f"Eksik paket ({e.name}). Proje ortamı kurulu değil; repo kökünde `uv sync` çalıştırın.")

DEFAULT_SYSTEM = "Sen yardımsever bir asistansın. Türkçe, kısa ve net cevap ver."
MAX_MESSAGES = 40  # bağlamı şişirmemek için son N mesaj tutulur


async def main(system_prompt: str) -> int:
    try:
        settings = load_settings()
    except RuntimeError as e:
        print(f"Hata: {e}", file=sys.stderr)
        return 1
    llm = LLM(settings)
    history: list[dict] = []
    print(f"Model: {settings.vllm_model}  @ {settings.vllm_base_url}")
    print("Çıkmak için /cikis yazın veya Ctrl+D'ye basın. Sohbeti sıfırlamak için /temizle.\n")
    try:
        while True:
            try:
                text = (await asyncio.to_thread(input, "sen: ")).strip()
            except EOFError:
                print()
                break
            if not text:
                continue
            if text in ("/cikis", "/çıkış", "/exit", "/quit"):
                break
            if text == "/temizle":
                history.clear()
                print("(sohbet sıfırlandı)\n")
                continue

            history.append({"role": "user", "content": text})
            del history[:-MAX_MESSAGES]
            messages = [{"role": "system", "content": system_prompt}, *history]

            print("qwen: ", end="", flush=True)
            started = time.monotonic()
            first: float | None = None
            parts: list[str] = []
            try:
                async for delta in llm.stream(messages):
                    if first is None:
                        first = time.monotonic() - started
                    parts.append(delta)
                    print(delta, end="", flush=True)
            except Exception as e:  # bağlantı/model hatası: sohbeti bozmadan devam et
                print(f"\n[hata: {type(e).__name__}: {e}]")
                history.pop()  # cevapsız kalan mesajı geçmişten çıkar
                print()
                continue
            total = time.monotonic() - started
            print(f"\n   ({total:.1f} sn, ilk yanıt {first or 0:.1f} sn)\n")
            history.append({"role": "assistant", "content": "".join(parts)})
    finally:
        await llm.close()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Qwen ile terminal sohbeti")
    ap.add_argument("--sistem", default=DEFAULT_SYSTEM, help="sistem promptu (varsayılan: genel asistan)")
    args = ap.parse_args()
    try:
        sys.exit(asyncio.run(main(args.sistem)))
    except KeyboardInterrupt:
        print("\n(çıkıldı)")
        sys.exit(130)
