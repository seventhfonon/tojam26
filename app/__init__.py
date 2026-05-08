"""Flask application factory."""

from __future__ import annotations

import atexit
import logging
import os

from flask import Flask
from sqlalchemy import inspect, text

from .config import Config
from .extensions import db, scheduler
from .jobs import game_tick, post_test_message
from .routes import bp as main_bp


log = logging.getLogger(__name__)


def _ensure_radiation_level_display_column() -> None:
    """Upgrade existing DB volumes that predate ``radiation_levels.level_display``."""
    with db.engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE radiation_levels "
                "ADD COLUMN IF NOT EXISTS level_display double precision"
            )
        )
    with db.engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE radiation_levels SET level_display = level "
                "WHERE level_display IS NULL"
            )
        )


def _ensure_bunker_systems_farming_columns() -> None:
    """Upgrade ``bunker_systems`` for farm workers and crop timers."""
    with db.engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE bunker_systems "
                "ADD COLUMN IF NOT EXISTS food_workers integer NOT NULL DEFAULT 0"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE bunker_systems "
                "ADD COLUMN IF NOT EXISTS crop_ready_at timestamp with time zone"
            )
        )


def _ensure_food_reserve_rate_columns() -> None:
    """Upgrade ``food_reserves`` for per-tick rate columns used by Grafana."""
    if "food_reserves" not in inspect(db.engine).get_table_names():
        return
    with db.engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE food_reserves "
                "ADD COLUMN IF NOT EXISTS consumption_per_second double precision"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE food_reserves "
                "ADD COLUMN IF NOT EXISTS production_per_second double precision"
            )
        )
        conn.execute(
            text(
                "UPDATE food_reserves SET consumption_per_second = 0 "
                "WHERE consumption_per_second IS NULL"
            )
        )
        conn.execute(
            text(
                "UPDATE food_reserves SET production_per_second = 0 "
                "WHERE production_per_second IS NULL"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE food_reserves ALTER COLUMN consumption_per_second "
                "SET NOT NULL"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE food_reserves ALTER COLUMN production_per_second "
                "SET NOT NULL"
            )
        )


def create_app(config_class: type[Config] = Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)
    app.register_blueprint(main_bp)

    with app.app_context():
        db.create_all()
        _ensure_radiation_level_display_column()
        _ensure_bunker_systems_farming_columns()
        _ensure_food_reserve_rate_columns()

    _maybe_start_scheduler(app)

    return app


def _maybe_start_scheduler(app: Flask) -> None:
    """Start the game-tick job, guarding against Flask's debug reloader.

    The reloader spawns two processes; only the child (with WERKZEUG_RUN_MAIN=true)
    should own the scheduler so we don't double-tick.
    """
    is_reloader_parent = app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true"
    if is_reloader_parent:
        return

    if scheduler.running:
        return

    scheduler.add_job(
        func=game_tick,
        kwargs={"app": app},
        trigger="interval",
        seconds=app.config["DECAY_TICK_SECONDS"],
        id="game_tick",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    log.info(
        "game tick scheduler started (tick=%ss, half-life=%ss, safe-threshold=%.1f)",
        app.config["DECAY_TICK_SECONDS"],
        app.config["DECAY_HALF_LIFE_SECONDS"],
        app.config["RADIATION_SAFE_THRESHOLD"],
    )

    atexit.register(lambda: scheduler.shutdown(wait=False))
