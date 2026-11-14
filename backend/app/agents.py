"""Sohbet botlarının (ajanların) ayarı: backend/agents.toml dosyasından okunur."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

# Tüm botlara eklenen ortak kurallar (uydurmayı azaltmak için).
GROUNDING_RULES = (
    "Kurallar: Yalnızca sana verilen verilere dayan; verilerde olmayan sayı, tarih veya olay uydurma. "
    "Yorumunda ilgili sayıları yaz. Eminlik düşükse bunu açıkça belirt. "
    "Bir veri yoksa 'bu veri elimde yok' de. Türkçe, kısa ve net yaz."
)


@dataclass(frozen=True)
class AgentConfig:
    id: str
    name: str
    system_prompt: str
    layers: tuple[str, ...]  # bu botun göreceği ML katmanları
    optional_layers: tuple[str, ...] = ()  # varsa gösterilir, yoksa "veri yok" denmez (örn. mevcut rotasyon)
    max_sentences: int = 5

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "layers": [*self.layers, *self.optional_layers]}


def load_agents(path: Path) -> list[AgentConfig]:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    raw = data.get("agent")
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{path}: en az bir [[agent]] tanımı gerekli")
    agents: list[AgentConfig] = []
    for i, a in enumerate(raw, 1):
        try:
            cfg = AgentConfig(
                id=str(a["id"]).strip(),
                name=str(a["name"]).strip(),
                system_prompt=str(a["system_prompt"]).strip(),
                layers=tuple(str(x) for x in a["layers"]),
                optional_layers=tuple(str(x) for x in a.get("optional_layers", ())),
                max_sentences=int(a.get("max_sentences", 5)),
            )
        except KeyError as e:
            raise ValueError(f"{path}: {i}. [[agent]] içinde {e} alanı eksik") from None
        if not cfg.id or not cfg.name or not cfg.system_prompt or not cfg.layers:
            raise ValueError(f"{path}: {i}. [[agent]] boş alan içeriyor")
        agents.append(cfg)
    ids = [a.id for a in agents]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{path}: agent id'leri benzersiz olmalı")
    return agents
