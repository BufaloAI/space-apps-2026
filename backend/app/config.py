"""Ayarlar: ortam değişkenlerinden (backend/.env) okunur."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# backend/.env dosyasını yükle (yoksa sessizce geç; ortam değişkenleri yine okunur)
load_dotenv(Path(__file__).resolve().parents[1] / ".env")


@dataclass(frozen=True)
class Settings:
    vllm_base_url: str
    vllm_api_key: str
    vllm_model: str
    idle_timeout_seconds: float
    queue_ttl_seconds: float
    max_tokens: int
    temperature: float
    history_max_messages: int
    cors_origins: list[str]
    max_area_ha: float
    agents_file: Path
    ml_fake_delay_seconds: float
    ml_provider: str  # "fake" | "xgboost"
    ml_model_dir: str | None  # boşsa ml/runs/LATEST
    ml_cache_dir: str | None  # boşsa ml/cache/parcels
    ml_ndvi_timeout_seconds: float
    ml_ndvi_retries: int


def cors_origins() -> list[str]:
    """CORS adresleri; API key olmadan da okunabilir (uygulama oluşturulurken gerekir)."""
    return [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]


def load_settings() -> Settings:
    key = os.environ.get("VLLM_API_KEY", "").strip()
    if not key or key == "buraya-api-key":
        raise RuntimeError(
            "VLLM_API_KEY tanımlı değil. backend/.env.example dosyasını backend/.env olarak "
            "kopyalayıp API key'i doldurun."
        )
    return Settings(
        vllm_base_url=os.environ.get("VLLM_BASE_URL", "http://100.96.165.43:8000/v1"),
        vllm_api_key=key,
        vllm_model=os.environ.get("VLLM_MODEL", "Qwen/Qwen3-8B-FP8"),
        idle_timeout_seconds=float(os.environ.get("IDLE_TIMEOUT_SECONDS", "300")),
        queue_ttl_seconds=float(os.environ.get("QUEUE_TTL_SECONDS", "30")),
        max_tokens=int(os.environ.get("MAX_TOKENS", "800")),
        temperature=float(os.environ.get("TEMPERATURE", "0.4")),
        history_max_messages=int(os.environ.get("HISTORY_MAX_MESSAGES", "20")),
        cors_origins=cors_origins(),
        max_area_ha=float(os.environ.get("MAX_AREA_HA", "5000")),
        agents_file=Path(os.environ.get("AGENTS_FILE", str(Path(__file__).resolve().parents[1] / "agents.toml"))),
        ml_fake_delay_seconds=float(os.environ.get("ML_FAKE_DELAY_SECONDS", "2")),
        ml_provider=os.environ.get("ML_PROVIDER", "fake").strip().lower(),
        ml_model_dir=os.environ.get("ML_MODEL_DIR", "").strip() or None,
        ml_cache_dir=os.environ.get("ML_CACHE_DIR", "").strip() or None,
        ml_ndvi_timeout_seconds=float(os.environ.get("ML_NDVI_TIMEOUT_SECONDS", "60")),
        ml_ndvi_retries=int(os.environ.get("ML_NDVI_RETRIES", "3")),
    )
