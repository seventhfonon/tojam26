"""Flask application factory."""

from __future__ import annotations

import atexit
import logging
import os

from flask import Flask

from .config import Config
from .extensions import db, scheduler
from .jobs import game_tick
from .routes import bp as main_bp


log = logging.getLogger(__name__)


def create_app(config_class: type[Config] = Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)
    app.register_blueprint(main_bp)

    with app.app_context():
        db.create_all()

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
