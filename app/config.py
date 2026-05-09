"""Infrastructure configuration: secrets, database, external URLs, session cookie.

Gameplay tuning lives in ``constants.py`` (import that module directly).
"""

from __future__ import annotations

import os


class Config:
    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")

    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg://silo:silo@localhost:5432/silo",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    GRAFANA_URL = os.environ.get("GRAFANA_URL", "http://localhost:3000")
    GRAFANA_DASHBOARD_UID = os.environ.get("GRAFANA_DASHBOARD_UID", "silo-environment")

    USER_COOKIE_NAME = "silo_user_id"
    USER_COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # 1 year
