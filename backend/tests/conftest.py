"""Testler sahte ML + eski (yer tutucu) botlarla çalışır; gerçek agents.toml/ML değişse de testler sabit kalır."""
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _fake_ml_env(monkeypatch):
    monkeypatch.setenv("AGENTS_FILE", str(Path(__file__).with_name("agents_fake.toml")))
    monkeypatch.setenv("ML_PROVIDER", "fake")
