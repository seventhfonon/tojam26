"""App configuration, populated from environment variables."""

from __future__ import annotations

import os


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw else default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw else default


class Config:
    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")

    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg://silo:silo@localhost:5432/silo",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    GRAFANA_URL = os.environ.get("GRAFANA_URL", "http://localhost:3000")
    GRAFANA_DASHBOARD_UID = os.environ.get("GRAFANA_DASHBOARD_UID", "silo-main")

    # Game tuning. Half-life of 600s with a 30s tick gives a visible decay curve
    # over the course of a play session while still being slow enough to feel
    # like an environmental measurement rather than a countdown.
    INITIAL_RADIATION = _env_float("INITIAL_RADIATION", 100.0)
    DECAY_TICK_SECONDS = _env_int("DECAY_TICK_SECONDS", 30)
    DECAY_HALF_LIFE_SECONDS = _env_int("DECAY_HALF_LIFE_SECONDS", 600)

    USER_COOKIE_NAME = "silo_user_id"
    USER_COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # 1 year
