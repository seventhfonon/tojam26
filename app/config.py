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

    # --- Radiation ---
    # Half-life of 600s with a 30s tick gives a visible decay curve over the
    # course of a play session while still feeling like an environmental
    # measurement rather than a countdown.
    INITIAL_RADIATION = _env_float("INITIAL_RADIATION", 100.0)
    DECAY_TICK_SECONDS = _env_int("DECAY_TICK_SECONDS", 30)
    DECAY_HALF_LIFE_SECONDS = _env_int("DECAY_HALF_LIFE_SECONDS", 600)

    # --- Population & loyalty ---
    INITIAL_POPULATION = _env_int("INITIAL_POPULATION", 50)
    # 0 = everyone wants out; 100 = unquestioning faith in the bunker.
    INITIAL_LOYALTY = _env_float("INITIAL_LOYALTY", 75.0)

    # Below this radiation reading (rads), people can decide to leave.
    # At 100-rad start with a 600s half-life, this threshold is crossed around
    # the 10-minute mark of a session — giving the player some breathing room
    # before the population clock starts ticking.
    RADIATION_SAFE_THRESHOLD = _env_float("RADIATION_SAFE_THRESHOLD", 50.0)

    # Each tick, each unit of "effective disloyalty" contributes this fraction
    # of the population as departures:
    #   departures = round(population × (1 − loyalty/100) × rate)
    # With defaults: round(50 × 0.25 × 0.05) = 1 person per tick once the
    # threshold is crossed. Loyalty 100 → 0 departures regardless.
    BASE_DEPARTURE_RATE = _env_float("BASE_DEPARTURE_RATE", 0.05)

    # --- Energy ---
    INITIAL_ENERGY = _env_float("INITIAL_ENERGY", 100.0)

    # Power draw of the lights system in energy/second. Other systems will
    # follow the same pattern when added (HVAC_POWER_DRAW, etc.).
    LIGHTS_POWER_DRAW = _env_float("LIGHTS_POWER_DRAW", 0.01)

    # Each worker assigned to the crank generates this much energy per second.
    # At 0.002/s, 5 workers exactly offsets the lights; 10 workers generate
    # double the lights draw, giving headroom for future systems.
    CRANK_POWER_PER_WORKER = _env_float("CRANK_POWER_PER_WORKER", 0.002)

    # Workers above this count start reducing loyalty each tick.
    CRANK_WORKERS_LOYALTY_THRESHOLD = _env_int("CRANK_WORKERS_LOYALTY_THRESHOLD", 10)

    # Loyalty lost per tick per worker above the threshold.
    # With defaults: 11 workers → -0.5/tick; 20 workers → -5/tick.
    CRANK_WORKERS_LOYALTY_PENALTY = _env_float("CRANK_WORKERS_LOYALTY_PENALTY", 0.5)

    # Energy added by a single manual crank button press.
    MANUAL_CRANK_ENERGY = _env_float("MANUAL_CRANK_ENERGY", 1.0)

    USER_COOKIE_NAME = "silo_user_id"
    USER_COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # 1 year
