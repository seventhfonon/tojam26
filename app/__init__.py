"""Flask application factory."""

from __future__ import annotations

import atexit
import logging
import os
from datetime import datetime, timezone

from flask import Flask
from sqlalchemy import inspect, select, text

from .config import Config
from . import constants
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


def _migrate_legacy_profession_history_if_needed() -> None:
    """Rename append-only ``bunker_professions`` to ``bunker_profession_snapshots``.

    Older installs used ``bunker_professions`` for tick snapshots; mutable assignment
    rows now live in a new ``bunker_professions`` table created by ``create_all``.
    """
    insp = inspect(db.engine)
    tables = set(insp.get_table_names())
    if "bunker_professions" not in tables:
        return
    cols = {c["name"] for c in insp.get_columns("bunker_professions")}
    if "timestamp" in cols and "updated_at" not in cols:
        with db.engine.begin() as conn:
            conn.execute(
                text("ALTER TABLE bunker_professions RENAME TO bunker_profession_snapshots")
            )
        log.info(
            "renamed legacy bunker_professions time-series table to bunker_profession_snapshots"
        )


def _migrate_legacy_bunker_systems_table() -> None:
    """Copy ``bunker_systems`` into split facility tables, then drop the legacy table."""
    insp = inspect(db.engine)
    if "bunker_systems" not in insp.get_table_names():
        return

    from .models import (
        BunkerFarmingSystem,
        BunkerLightingSystem,
        BunkerPopulation,
        BunkerPowerCrankSystem,
        BunkerProfession,
    )
    from .professions import PROFESSION_FARMING, PROFESSION_IDLE, PROFESSION_POWER_CRANK

    with db.engine.connect() as conn:
        legacy_rows = conn.execute(
            text(
                "SELECT user_id, lights_on, crank_workers, food_workers, "
                "crop_ready_at, updated_at FROM bunker_systems"
            )
        ).mappings().all()

    now = datetime.now(timezone.utc)

    for row in legacy_rows:
        uid = str(row["user_id"])
        if db.session.get(BunkerLightingSystem, uid) is not None:
            continue

        latest_pop = db.session.scalars(
            select(BunkerPopulation)
            .where(BunkerPopulation.user_id == uid)
            .order_by(BunkerPopulation.timestamp.desc())
            .limit(1)
        ).first()
        pop = latest_pop.count if latest_pop is not None else 0
        crank_n = int(row["crank_workers"] or 0)
        farm_n = int(row["food_workers"] or 0)
        idle_n = max(0, pop - crank_n - farm_n)

        crank_line = BunkerProfession(
            user_id=uid,
            profession=PROFESSION_POWER_CRANK,
            count=crank_n,
            updated_at=now,
        )
        farm_line = BunkerProfession(
            user_id=uid,
            profession=PROFESSION_FARMING,
            count=farm_n,
            updated_at=now,
        )
        idle_line = BunkerProfession(
            user_id=uid,
            profession=PROFESSION_IDLE,
            count=idle_n,
            updated_at=now,
        )
        db.session.add_all([crank_line, farm_line, idle_line])
        db.session.flush()

        updated_legacy = row["updated_at"] or now
        db.session.add(
            BunkerLightingSystem(
                user_id=uid,
                lights_on=bool(row["lights_on"]),
                updated_at=updated_legacy,
            )
        )
        db.session.add(
            BunkerPowerCrankSystem(
                user_id=uid,
                profession_line_id=crank_line.id,
                updated_at=updated_legacy,
            )
        )
        db.session.add(
            BunkerFarmingSystem(
                user_id=uid,
                profession_line_id=farm_line.id,
                crop_ready_at=row["crop_ready_at"],
                updated_at=updated_legacy,
            )
        )

    db.session.commit()

    with db.engine.begin() as conn:
        conn.execute(text("DELETE FROM bunker_systems"))
        conn.execute(text("DROP TABLE bunker_systems"))
    log.info("migrated legacy bunker_systems into split facility tables")


def _ensure_bunker_systems_farming_columns() -> None:
    """Upgrade ``bunker_systems`` for farm workers and crop timers (pre-split DBs)."""
    insp = inspect(db.engine)
    if "bunker_systems" not in insp.get_table_names():
        return
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


def _ensure_bunker_social_seed_data() -> None:
    """Backfill social tables for users created before social simulation existed."""
    tables = inspect(db.engine).get_table_names()
    if "users" not in tables:
        return
    if "bunker_social_state" not in tables:
        return
    with db.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO bunker_social_state "
                "(user_id, inner_circle_loyalty, movie_action_count, speech_action_count) "
                "SELECT u.id, 50, 0, 0 FROM users u "
                "WHERE NOT EXISTS ("
                "  SELECT 1 FROM bunker_social_state s WHERE s.user_id = u.id"
                ")"
            )
        )
    if "bunker_boredom" in tables:
        with db.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO bunker_boredom (user_id, boredom, timestamp) "
                    "SELECT u.id, 0, now() FROM users u "
                    "WHERE NOT EXISTS ("
                    "  SELECT 1 FROM bunker_boredom b WHERE b.user_id = u.id"
                    ")"
                )
            )
    if "bunker_doubt" in tables:
        with db.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO bunker_doubt (user_id, doubt, timestamp) "
                    "SELECT u.id, 0, now() FROM users u "
                    "WHERE NOT EXISTS ("
                    "  SELECT 1 FROM bunker_doubt d WHERE d.user_id = u.id"
                    ")"
                )
            )


def _ensure_player_active_events_system_column() -> None:
    """Add optional subsystem tag for active events (investigation routing)."""
    insp = inspect(db.engine)
    tables = set(insp.get_table_names())
    if "player_active_events" not in tables:
        return
    cols = {c["name"] for c in insp.get_columns("player_active_events")}
    if "system" in cols:
        return
    with db.engine.begin() as conn:
        conn.execute(text("ALTER TABLE player_active_events ADD COLUMN system VARCHAR(64)"))
        conn.execute(
            text(
                "UPDATE player_active_events SET system = 'farming' "
                "WHERE kind = 'rats_silo' AND system IS NULL"
            )
        )


def _ensure_users_investigation_target_system_column() -> None:
    """Subsystem targeted by the in-flight sweep (paired with ``investigation_busy_until``)."""
    if "users" not in inspect(db.engine).get_table_names():
        return
    with db.engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS investigation_target_system VARCHAR(64)"
            )
        )


def _migrate_investigation_timer_to_users() -> None:
    """Move investigation deployment timer from ``player_active_events`` to ``users``."""
    insp = inspect(db.engine)
    tables = set(insp.get_table_names())
    if "users" not in tables:
        return
    with db.engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS investigation_busy_until "
                "TIMESTAMP WITH TIME ZONE"
            )
        )
    if "player_active_events" not in tables:
        return
    cols = {c["name"] for c in insp.get_columns("player_active_events")}
    if "investigation_busy_until" not in cols:
        return
    with db.engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE users u SET investigation_busy_until = p.investigation_busy_until "
                "FROM player_active_events p WHERE u.id = p.user_id "
                "AND p.investigation_busy_until IS NOT NULL"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE player_active_events DROP COLUMN IF EXISTS investigation_busy_until"
            )
        )


def _ensure_investigation_profession_lines() -> None:
    """Backfill Investigation profession rows for players created before this feature."""
    tables = inspect(db.engine).get_table_names()
    if "users" not in tables or "bunker_professions" not in tables:
        return
    with db.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO bunker_professions (user_id, profession, count, updated_at) "
                "SELECT u.id, 'Investigation', 0, now() FROM users u "
                "WHERE NOT EXISTS ("
                "  SELECT 1 FROM bunker_professions bp "
                "  WHERE bp.user_id = u.id AND bp.profession = 'Investigation'"
                ")"
            )
        )


def create_app(config_class: type[Config] = Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)
    app.register_blueprint(main_bp)

    with app.app_context():
        _migrate_legacy_profession_history_if_needed()
        db.create_all()
        _ensure_radiation_level_display_column()
        _ensure_bunker_systems_farming_columns()
        _ensure_food_reserve_rate_columns()
        _ensure_bunker_social_seed_data()
        _migrate_legacy_bunker_systems_table()
        _ensure_player_active_events_system_column()
        _ensure_users_investigation_target_system_column()
        _migrate_investigation_timer_to_users()
        _ensure_investigation_profession_lines()

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
        seconds=constants.DECAY_TICK_SECONDS,
        id="game_tick",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    log.info(
        "game tick scheduler started (tick=%ss, half-life=%ss, safe-threshold=%.1f)",
        constants.DECAY_TICK_SECONDS,
        constants.DECAY_HALF_LIFE_SECONDS,
        constants.RADIATION_SAFE_THRESHOLD,
    )

    atexit.register(lambda: scheduler.shutdown(wait=False))
