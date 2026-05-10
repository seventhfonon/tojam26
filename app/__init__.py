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

    from .constants import FARM_PLOT_COUNT
    from .models import (
        BunkerCropPlot,
        BunkerFarmingSystem,
        BunkerLightingSystem,
        BunkerPopulation,
        BunkerPowerCrankSystem,
        BunkerProfession,
    )
    from .professions import PROFESSION_FARMING, PROFESSION_IDLE, PROFESSION_POWER_CRANK, PROFESSION_RAT_TRAPPING

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
        rat_line = BunkerProfession(
            user_id=uid,
            profession=PROFESSION_RAT_TRAPPING,
            count=0,
            updated_at=now,
        )
        db.session.add_all([crank_line, farm_line, rat_line, idle_line])
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
                rat_trapper_line_id=rat_line.id,
                updated_at=updated_legacy,
            )
        )

        for plot_i in range(FARM_PLOT_COUNT):
            db.session.add(
                BunkerCropPlot(
                    user_id=uid,
                    plot_index=plot_i,
                    crop_ready_at=row["crop_ready_at"] if plot_i == 0 else None,
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


def _ensure_bunker_social_screening_columns() -> None:
    """Add in-flight movie screening columns (nullable)."""
    if "bunker_social_state" not in inspect(db.engine).get_table_names():
        return
    with db.engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE bunker_social_state "
                "ADD COLUMN IF NOT EXISTS movie_screening_movie_id character varying(64)"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE bunker_social_state "
                "ADD COLUMN IF NOT EXISTS movie_screening_started_at "
                "timestamp with time zone"
            )
        )


def _ensure_player_movie_exhaustion_screenings_completed() -> None:
    """Per-title completed screenings (diminishing boredom relief per movie)."""
    if "player_movie_exhaustion" not in inspect(db.engine).get_table_names():
        return
    with db.engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE player_movie_exhaustion "
                "ADD COLUMN IF NOT EXISTS screenings_completed integer "
                "NOT NULL DEFAULT 0"
            )
        )


def _ensure_bunker_social_seed_data() -> None:
    """Backfill social tables for users created before social simulation existed."""
    tables = inspect(db.engine).get_table_names()
    if "users" not in tables:
        return
    if "bunker_social_state" not in tables:
        return
    cols = {c["name"] for c in inspect(db.engine).get_columns("bunker_social_state")}
    insert_cols = [
        "user_id",
        "inner_circle_loyalty",
        "movie_action_count",
        "speech_action_count",
    ]
    select_exprs = ["u.id", "50", "0", "0"]
    # Include NOT NULL columns when present so INSERT matches ``db.create_all()`` schema
    # (Python ``default=`` does not always imply a PostgreSQL DEFAULT).
    optional_literals: tuple[tuple[str, str], ...] = (
        ("movie_pixel_frame_index", "0"),
        ("inner_circle_cash", "1000"),
        ("basket_weaving_hours", "0"),
        ("awaiting_post_geiger_exodus_speech", "FALSE"),
        ("fireside_chats_focus_gate_done", "FALSE"),
        ("temp_job_backfire_seen", "FALSE"),
    )
    for col_name, lit in optional_literals:
        if col_name in cols:
            insert_cols.append(col_name)
            select_exprs.append(lit)
    col_sql = ", ".join(insert_cols)
    val_sql = ", ".join(select_exprs)
    with db.engine.begin() as conn:
        conn.execute(
            text(
                f"INSERT INTO bunker_social_state ({col_sql}) "
                f"SELECT {val_sql} FROM users u "
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


def _migrate_bunker_crop_plots_schema() -> None:
    """Create per-plot crop rows and drop legacy ``crop_ready_at`` on farming facilities."""
    from .constants import FARM_PLOT_COUNT

    insp = inspect(db.engine)
    tables = set(insp.get_table_names())
    if "bunker_crop_plots" not in tables:
        return

    last_plot = FARM_PLOT_COUNT - 1
    with db.engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO bunker_crop_plots (user_id, plot_index, crop_ready_at)
                SELECT f.user_id, gs.i, NULL::timestamptz
                FROM bunker_farming_systems f
                CROSS JOIN generate_series(0, :last_plot) AS gs(i)
                ON CONFLICT (user_id, plot_index) DO NOTHING
                """
            ),
            {"last_plot": last_plot},
        )

    if "bunker_farming_systems" not in tables:
        return
    cols = {c["name"] for c in insp.get_columns("bunker_farming_systems")}
    if "crop_ready_at" not in cols:
        return

    with db.engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE bunker_crop_plots p
                SET crop_ready_at = f.crop_ready_at
                FROM bunker_farming_systems f
                WHERE p.user_id = f.user_id
                  AND p.plot_index = 0
                  AND f.crop_ready_at IS NOT NULL
                """
            )
        )
        conn.execute(
            text("ALTER TABLE bunker_farming_systems DROP COLUMN crop_ready_at")
        )


def _ensure_bunker_crop_plot_growth_tracking_columns() -> None:
    """Per-plot crop growth window + ∫(farm_workers dt) for harvest yield scaling."""
    from .constants import FARM_PLANT_GROWTH_SECONDS

    insp = inspect(db.engine)
    if "bunker_crop_plots" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("bunker_crop_plots")}
    with db.engine.begin() as conn:
        if "crop_planted_at" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE bunker_crop_plots "
                    "ADD COLUMN crop_planted_at TIMESTAMPTZ"
                )
            )
        if "growth_worker_seconds" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE bunker_crop_plots "
                    "ADD COLUMN growth_worker_seconds DOUBLE PRECISION NOT NULL DEFAULT 0"
                )
            )
        conn.execute(
            text(
                """
                UPDATE bunker_crop_plots
                SET crop_planted_at = crop_ready_at - (:sec * INTERVAL '1 second')
                WHERE crop_ready_at IS NOT NULL AND crop_planted_at IS NULL
                """
            ),
            {"sec": FARM_PLANT_GROWTH_SECONDS},
        )


def _ensure_farming_rat_trapper_lines() -> None:
    """Second farming FK + Rat trapping profession rows for legacy installs."""
    insp = inspect(db.engine)
    tables = set(insp.get_table_names())
    if "bunker_farming_systems" not in tables or "bunker_professions" not in tables:
        return
    cols_meta = insp.get_columns("bunker_farming_systems")
    cols = {c["name"] for c in cols_meta}
    if "rat_trapper_line_id" not in cols:
        with db.engine.begin() as conn:
            conn.execute(
                text(
                    "ALTER TABLE bunker_farming_systems "
                    "ADD COLUMN rat_trapper_line_id INTEGER UNIQUE "
                    "REFERENCES bunker_professions(id)"
                )
            )

    with db.engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO bunker_professions (user_id, profession, count, updated_at)
                SELECT f.user_id, 'Rat trapping', 0, now()
                FROM bunker_farming_systems f
                WHERE NOT EXISTS (
                    SELECT 1 FROM bunker_professions bp
                    WHERE bp.user_id = f.user_id AND bp.profession = 'Rat trapping'
                )
                """
            )
        )
        conn.execute(
            text(
                """
                UPDATE bunker_farming_systems f
                SET rat_trapper_line_id = bp.id
                FROM bunker_professions bp
                WHERE bp.user_id = f.user_id
                  AND bp.profession = 'Rat trapping'
                  AND f.rat_trapper_line_id IS NULL
                """
            )
        )

    cols_meta = inspect(db.engine).get_columns("bunker_farming_systems")
    rat_col = next((c for c in cols_meta if c["name"] == "rat_trapper_line_id"), None)
    if rat_col is None or rat_col.get("nullable") is False:
        return
    with db.engine.connect() as conn:
        remaining = conn.execute(
            text(
                "SELECT COUNT(*) FROM bunker_farming_systems WHERE rat_trapper_line_id IS NULL"
            )
        ).scalar_one()
    if remaining != 0:
        log.warning(
            "bunker_farming_systems has %s row(s) without rat_trapper_line_id; NOT NULL skipped",
            remaining,
        )
        return
    with db.engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE bunker_farming_systems "
                "ALTER COLUMN rat_trapper_line_id SET NOT NULL"
            )
        )


def _ensure_user_rat_infestation_columns() -> None:
    """Resident rat intro flag + fluctuating silo drain (units/sec)."""
    if "users" not in inspect(db.engine).get_table_names():
        return
    with db.engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS silo_rats_introduced "
                "BOOLEAN NOT NULL DEFAULT false"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS rat_background_consumption_ps "
                "DOUBLE PRECISION NOT NULL DEFAULT 0"
            )
        )


def _ensure_user_rat_trappers_unlocked_column() -> None:
    """Gate trapper hiring until farming investigation clears ``rats_silo_intro``."""
    insp = inspect(db.engine)
    if "users" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("users")}
    added = "rat_trappers_unlocked" not in cols
    with db.engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS rat_trappers_unlocked "
                "BOOLEAN NOT NULL DEFAULT false"
            )
        )
        if added:
            conn.execute(text("UPDATE users SET rat_trappers_unlocked = TRUE"))


def _ensure_user_fireside_columns() -> None:
    """Fireside Chat busy window + pending tone for ``game_tick`` completion."""
    insp = inspect(db.engine)
    if "users" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("users")}
    with db.engine.begin() as conn:
        if "fireside_busy_until" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE users ADD COLUMN fireside_busy_until "
                    "TIMESTAMP WITH TIME ZONE"
                )
            )
        if "fireside_pending_kind" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE users ADD COLUMN fireside_pending_kind VARCHAR(32)"
                )
            )
        if "fireside_effect_fraction_accrued" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE users ADD COLUMN fireside_effect_fraction_accrued "
                    "DOUBLE PRECISION NOT NULL DEFAULT 0"
                )
            )


def _ensure_system_messages_channel_column() -> None:
    """Route Silo Bulletin vs Inner Circle Group Chat (same table, filtered by ``channel``)."""
    if "system_messages" not in inspect(db.engine).get_table_names():
        return
    cols = {c["name"] for c in inspect(db.engine).get_columns("system_messages")}
    if "channel" in cols:
        return
    with db.engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE system_messages ADD COLUMN channel VARCHAR(32) "
                "NOT NULL DEFAULT 'bulletin'"
            )
        )


def _ensure_bunker_social_inner_circle_cash_column() -> None:
    """Liquid currency on ``bunker_social_state`` (Inner Circle temp jobs)."""
    insp = inspect(db.engine)
    if "bunker_social_state" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("bunker_social_state")}
    if "inner_circle_cash" in cols:
        return
    with db.engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE bunker_social_state ADD COLUMN inner_circle_cash "
                "DOUBLE PRECISION NOT NULL DEFAULT 0"
            )
        )


def _ensure_bunker_social_focus_gate_columns() -> None:
    """Social flags for Focus Tree prerequisites (Geiger aftermath speech, temp-job branch)."""
    if "bunker_social_state" not in inspect(db.engine).get_table_names():
        return
    stmts = (
        "ALTER TABLE bunker_social_state ADD COLUMN IF NOT EXISTS awaiting_post_geiger_exodus_speech "
        "BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE bunker_social_state ADD COLUMN IF NOT EXISTS fireside_chats_focus_gate_done "
        "BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE bunker_social_state ADD COLUMN IF NOT EXISTS temp_job_backfire_seen "
        "BOOLEAN NOT NULL DEFAULT FALSE",
    )
    with db.engine.begin() as conn:
        for s in stmts:
            conn.execute(text(s))


def _ensure_inner_circle_member_departed_column() -> None:
    if "inner_circle_members" not in inspect(db.engine).get_table_names():
        return
    with db.engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE inner_circle_members ADD COLUMN IF NOT EXISTS departed "
                "BOOLEAN NOT NULL DEFAULT FALSE"
            )
        )


def _ensure_bunker_social_basket_weaving_hours_column() -> None:
    """Mandatory basket-weaving hours per resident (Community team-building silo class)."""
    insp = inspect(db.engine)
    if "bunker_social_state" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("bunker_social_state")}
    if "basket_weaving_hours" in cols:
        return
    with db.engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE bunker_social_state ADD COLUMN basket_weaving_hours "
                "INTEGER NOT NULL DEFAULT 0"
            )
        )


def _ensure_inner_circle_members_psyche_columns() -> None:
    """Frustration + disposition columns on inner_circle_members (Inner Circle psyche loop)."""
    insp = inspect(db.engine)
    if "inner_circle_members" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("inner_circle_members")}
    with db.engine.begin() as conn:
        if "frustration" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE inner_circle_members ADD COLUMN frustration "
                    "DOUBLE PRECISION NOT NULL DEFAULT 40"
                )
            )
        if "disposition" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE inner_circle_members ADD COLUMN disposition "
                    "DOUBLE PRECISION NOT NULL DEFAULT 65"
                )
            )


def _ensure_inner_circle_members_seed() -> None:
    """Create default Inner Circle member rows for every player."""
    from . import inner_circle
    from .models import User

    tables = inspect(db.engine).get_table_names()
    if "users" not in tables or "inner_circle_members" not in tables:
        return
    user_ids = db.session.scalars(select(User.id)).all()
    for uid in user_ids:
        inner_circle.seed_members_for_user_if_needed(uid)
    db.session.commit()


def _ensure_bunker_social_last_fireside_chat_at_column() -> None:
    """Cooldown anchor between Fireside Chat starts."""
    insp = inspect(db.engine)
    if "bunker_social_state" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("bunker_social_state")}
    if "last_fireside_chat_at" in cols:
        return
    with db.engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE bunker_social_state ADD COLUMN last_fireside_chat_at "
                "TIMESTAMP WITH TIME ZONE"
            )
        )


def _ensure_user_geiger_rumor_exodus_columns() -> None:
    """One-shot radiation-vs-doubt crisis + rumor exit quotas while ``geiger_rumor_exodus`` runs."""
    insp = inspect(db.engine)
    if "users" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("users")}
    with db.engine.begin() as conn:
        if "geiger_rumor_crisis_triggered" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE users ADD COLUMN geiger_rumor_crisis_triggered "
                    "BOOLEAN NOT NULL DEFAULT false"
                )
            )
        if "rumor_exodus_quota_initial" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE users ADD COLUMN rumor_exodus_quota_initial "
                    "INTEGER NOT NULL DEFAULT 0"
                )
            )
        if "rumor_exodus_quota_remaining" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE users ADD COLUMN rumor_exodus_quota_remaining "
                    "INTEGER NOT NULL DEFAULT 0"
                )
            )


def _ensure_bunker_theatre_play_index_column() -> None:
    """Catalog rotation index on ``bunker_theatre_systems`` (King Lear / Tempest / Mr. Burns)."""
    insp = inspect(db.engine)
    if "bunker_theatre_systems" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("bunker_theatre_systems")}
    if "play_index" in cols:
        return
    with db.engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE bunker_theatre_systems ADD COLUMN play_index "
                "INTEGER NOT NULL DEFAULT 0"
            )
        )


def _ensure_player_active_events_auto_resolve_nullable() -> None:
    """Allow indefinite events (no timer auto-resolve)."""
    insp = inspect(db.engine)
    if "player_active_events" not in insp.get_table_names():
        return
    col = next(
        (c for c in insp.get_columns("player_active_events") if c["name"] == "auto_resolve_at"),
        None,
    )
    if col is None or col.get("nullable") is True:
        return
    with db.engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE player_active_events "
                "ALTER COLUMN auto_resolve_at DROP NOT NULL"
            )
        )


def _ensure_social_movie_pixel_samples_movie_id_column() -> None:
    """Tag theater strips by catalog ``movie_id`` (one advancing clip per ``MOVIES`` row)."""
    insp = inspect(db.engine)
    tables = insp.get_table_names()
    if "social_movie_pixel_samples" not in tables:
        return
    cols = {c["name"] for c in insp.get_columns("social_movie_pixel_samples")}
    if "movie_id" in cols:
        return
    with db.engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE social_movie_pixel_samples "
                "ADD COLUMN movie_id character varying(64)"
            )
        )
        conn.execute(
            text(
                "UPDATE social_movie_pixel_samples SET movie_id = 'atomic_cafe' "
                "WHERE movie_id IS NULL"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE social_movie_pixel_samples ALTER COLUMN movie_id SET NOT NULL"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_social_movie_pixel_samples_movie_id "
                "ON social_movie_pixel_samples (movie_id)"
            )
        )


def _ensure_bunker_social_movie_pixel_frame_index_column() -> None:
    """Frame cursor for the single-channel movie heatmap while a screening runs."""
    insp = inspect(db.engine)
    if "bunker_social_state" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("bunker_social_state")}
    if "movie_pixel_frame_index" in cols:
        return
    with db.engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE bunker_social_state ADD COLUMN movie_pixel_frame_index "
                "INTEGER NOT NULL DEFAULT 0"
            )
        )


def _drop_legacy_social_movie_pixel_animations_table_if_exists() -> None:
    """Removed in favor of ``BunkerSocialState.movie_pixel_frame_index``."""
    insp = inspect(db.engine)
    if "social_movie_pixel_animations" not in insp.get_table_names():
        return
    with db.engine.begin() as conn:
        conn.execute(text("DROP TABLE social_movie_pixel_animations CASCADE"))


def create_app(config_class: type[Config] = Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)
    app.register_blueprint(main_bp)

    with app.app_context():
        _migrate_legacy_profession_history_if_needed()
        db.create_all()
        _ensure_system_messages_channel_column()
        _ensure_radiation_level_display_column()
        _ensure_bunker_systems_farming_columns()
        _ensure_food_reserve_rate_columns()
        _ensure_bunker_social_seed_data()
        _ensure_bunker_social_screening_columns()
        _ensure_bunker_social_movie_pixel_frame_index_column()
        _ensure_player_movie_exhaustion_screenings_completed()
        _ensure_social_movie_pixel_samples_movie_id_column()
        _drop_legacy_social_movie_pixel_animations_table_if_exists()
        _migrate_legacy_bunker_systems_table()
        _migrate_bunker_crop_plots_schema()
        _ensure_bunker_crop_plot_growth_tracking_columns()
        _ensure_player_active_events_system_column()
        _ensure_users_investigation_target_system_column()
        _migrate_investigation_timer_to_users()
        _ensure_investigation_profession_lines()
        _ensure_farming_rat_trapper_lines()
        _ensure_user_rat_infestation_columns()
        _ensure_user_rat_trappers_unlocked_column()
        _ensure_user_fireside_columns()
        _ensure_user_geiger_rumor_exodus_columns()
        _ensure_bunker_social_inner_circle_cash_column()
        _ensure_bunker_social_focus_gate_columns()
        _ensure_inner_circle_member_departed_column()
        _ensure_bunker_social_basket_weaving_hours_column()
        _ensure_inner_circle_members_psyche_columns()
        _ensure_inner_circle_members_seed()
        _ensure_bunker_social_last_fireside_chat_at_column()
        _ensure_bunker_theatre_play_index_column()
        _ensure_player_active_events_auto_resolve_nullable()

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
