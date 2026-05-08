"""Background jobs run by APScheduler."""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from flask import Flask
from sqlalchemy import select

from .extensions import db
from .models import (
    BunkerLoyalty,
    BunkerPopulation,
    BunkerSystems,
    EnergyReserve,
    FoodReserve,
    RadiationLevel,
    SystemMessage,
    User,
    UserNarrativeDelivery,
)
from .events import (
    EventSpawnContext,
    active_event_food_multiplier,
    active_event_row,
    auto_resolve_if_due,
    try_spawn_event,
)
from .narrative import NarrativeContext, deliver_pending_narrative_messages


log = logging.getLogger(__name__)

_last_gamestate_log_mono = 0.0


def _maybe_log_gamestate_snapshots(
    snapshots: list[dict[str, object]],
    interval_s: float,
) -> None:
    """Emit one INFO line per user every ``interval_s`` wall-clock seconds."""
    global _last_gamestate_log_mono
    if not snapshots or interval_s <= 0:
        return
    now_m = time.monotonic()
    if now_m - _last_gamestate_log_mono < interval_s:
        return
    _last_gamestate_log_mono = now_m
    for s in snapshots:
        ev = s.get("active_event")
        ev_s = ev if isinstance(ev, str) else "-"
        log.info(
            "gamestate user=%s pop=%d food=%.1f loyalty=%.1f rad_truth=%.2f "
            "energy=%s departed_tick=%s event=%s food_mult=%.2f "
            "crank_workers=%d food_workers=%d",
            s["user_id"],
            s["population"],
            s["food"],
            s["loyalty_final"],
            s["radiation_truth"],
            s["energy"],
            s["departed_tick"],
            ev_s,
            s["food_mult"],
            s["crank_workers"],
            s["food_workers"],
        )


@dataclass(frozen=True)
class GameTickReadings:
    """Latest samples and control state needed for one player tick."""

    latest_radiation_level: RadiationLevel
    latest_population_sample: BunkerPopulation
    latest_loyalty_sample: BunkerLoyalty
    latest_energy_reserve: EnergyReserve | None
    latest_food_reserve: FoodReserve | None
    bunker_systems: BunkerSystems | None


def fetch_game_tick_readings_for_user(user_id: str) -> GameTickReadings | None:
    latest_radiation_level = db.session.scalars(
        select(RadiationLevel)
        .where(RadiationLevel.user_id == user_id)
        .order_by(RadiationLevel.timestamp.desc())
        .limit(1)
    ).first()

    latest_population_sample = db.session.scalars(
        select(BunkerPopulation)
        .where(BunkerPopulation.user_id == user_id)
        .order_by(BunkerPopulation.timestamp.desc())
        .limit(1)
    ).first()

    latest_loyalty_sample = db.session.scalars(
        select(BunkerLoyalty)
        .where(BunkerLoyalty.user_id == user_id)
        .order_by(BunkerLoyalty.timestamp.desc())
        .limit(1)
    ).first()

    latest_energy_reserve = db.session.scalars(
        select(EnergyReserve)
        .where(EnergyReserve.user_id == user_id)
        .order_by(EnergyReserve.timestamp.desc())
        .limit(1)
    ).first()

    latest_food_reserve = db.session.scalars(
        select(FoodReserve)
        .where(FoodReserve.user_id == user_id)
        .order_by(FoodReserve.timestamp.desc())
        .limit(1)
    ).first()

    bunker_systems = db.session.get(BunkerSystems, user_id)

    if (
        latest_radiation_level is None
        or latest_population_sample is None
        or latest_loyalty_sample is None
    ):
        return None

    return GameTickReadings(
        latest_radiation_level=latest_radiation_level,
        latest_population_sample=latest_population_sample,
        latest_loyalty_sample=latest_loyalty_sample,
        latest_energy_reserve=latest_energy_reserve,
        latest_food_reserve=latest_food_reserve,
        bunker_systems=bunker_systems,
    )


def elapsed_seconds_for_game_tick(
    tick_time: datetime,
    latest_radiation_level: RadiationLevel,
) -> float | None:
    elapsed_seconds = (tick_time - latest_radiation_level.timestamp).total_seconds()
    if elapsed_seconds <= 0:
        return None
    return elapsed_seconds


def noisy_radiation_display(true_level: float, noise_max: float) -> float:
    """Player-facing reading: truth plus uniform jitter in ``[-noise_max, noise_max]``."""
    offset = random.uniform(-noise_max, noise_max)
    return max(0.0, true_level + offset)


def handle_radiation_decay(
    user_id: str,
    latest_radiation_level: RadiationLevel,
    elapsed_seconds: float,
    decay_half_life_seconds: float,
    display_noise_max: float,
    tick_time: datetime,
) -> None:
    new_radiation_level = latest_radiation_level.level * (
        0.5 ** (elapsed_seconds / decay_half_life_seconds)
    )
    new_display = noisy_radiation_display(new_radiation_level, display_noise_max)
    db.session.add(
        RadiationLevel(
            user_id=user_id,
            level=new_radiation_level,
            level_display=new_display,
            timestamp=tick_time,
        )
    )


def handle_loyalty_change(
    latest_loyalty_sample: BunkerLoyalty,
    bunker_systems: BunkerSystems | None,
    crank_workers_loyalty_threshold: int,
    loyalty_penalty_per_excess_crank_worker: float,
) -> float:
    new_loyalty = latest_loyalty_sample.loyalty
    if (
        bunker_systems is not None
        and bunker_systems.crank_workers > crank_workers_loyalty_threshold
    ):
        excess_crank_workers = bunker_systems.crank_workers - crank_workers_loyalty_threshold
        new_loyalty = max(
            0.0,
            new_loyalty - excess_crank_workers * loyalty_penalty_per_excess_crank_worker,
        )
    return new_loyalty


def user_had_prior_departure_event(user_id: str) -> bool:
    """True if any population sample before this tick recorded someone leaving."""
    row = db.session.scalars(
        select(BunkerPopulation.id)
        .where(BunkerPopulation.user_id == user_id, BunkerPopulation.departed > 0)
        .limit(1)
    ).first()
    return row is not None

def user_had_prior_welcome_message(user_id: str) -> bool:
    """True if any narrative delivery before this tick recorded the welcome message."""
    row = db.session.scalars(
        select(UserNarrativeDelivery.id)
        .where(UserNarrativeDelivery.user_id == user_id, UserNarrativeDelivery.message_id == "welcome_message")
        .limit(1)
    ).first()
    return row is not None

def handle_population_departures(
    user_id: str,
    latest_radiation_level: RadiationLevel,
    latest_population_sample: BunkerPopulation,
    adjusted_loyalty: float,
    radiation_safe_threshold: float,
    base_departure_rate: float,
    tick_time: datetime,
) -> int:
    current_population = latest_population_sample.count
    if latest_radiation_level.level < radiation_safe_threshold and current_population > 0:
        disloyalty = max(0.0, 1.0 - (adjusted_loyalty / 100.0))
        departed_count = min(
            current_population,
            max(
                0,
                round(current_population * disloyalty * base_departure_rate),
            ),
        )
    else:
        departed_count = 0

    db.session.add(
        BunkerPopulation(
            user_id=user_id,
            count=current_population - departed_count,
            departed=departed_count,
            timestamp=tick_time,
        )
    )
    return departed_count


def normalize_worker_assignments(bunker_systems: BunkerSystems, population_cap: int) -> None:
    """Clamp crank + food workers to ``population_cap`` (shared pool)."""
    bunker_systems.crank_workers = max(0, min(bunker_systems.crank_workers, population_cap))
    bunker_systems.food_workers = max(0, min(bunker_systems.food_workers, population_cap))
    while bunker_systems.crank_workers + bunker_systems.food_workers > population_cap:
        if bunker_systems.food_workers > 0:
            bunker_systems.food_workers -= 1
        else:
            bunker_systems.crank_workers -= 1


def handle_food_reserve_change(
    user_id: str,
    current_food_level: float,
    bunker_systems: BunkerSystems,
    population_for_consumption: int,
    elapsed_seconds: float,
    food_per_capita_per_second: float,
    food_per_worker_per_second: float,
    tick_time: datetime,
    food_consumption_multiplier: float = 1.0,
) -> None:
    """Net food change from farm workers vs population consumption (pre-tick headcount)."""
    consumption_ps = (
        population_for_consumption * food_per_capita_per_second * food_consumption_multiplier
    )
    production_ps = bunker_systems.food_workers * food_per_worker_per_second
    new_level = max(
        0.0,
        current_food_level + (production_ps - consumption_ps) * elapsed_seconds,
    )
    db.session.add(
        FoodReserve(
            user_id=user_id,
            level=new_level,
            consumption_per_second=consumption_ps,
            production_per_second=production_ps,
            timestamp=tick_time,
        )
    )


def handle_energy_reserve_change(
    user_id: str,
    latest_energy_reserve: EnergyReserve,
    bunker_systems: BunkerSystems,
    elapsed_seconds: float,
    lights_power_draw_per_second: float,
    crank_power_per_worker_per_second: float,
    tick_time: datetime,
) -> None:
    power_draw = lights_power_draw_per_second if bunker_systems.lights_on else 0.0
    generation_power = bunker_systems.crank_workers * crank_power_per_worker_per_second
    new_energy_level = max(
        0.0,
        latest_energy_reserve.level + (generation_power - power_draw) * elapsed_seconds,
    )
    db.session.add(
        EnergyReserve(user_id=user_id, level=new_energy_level, timestamp=tick_time)
    )


def record_loyalty_sample(
    user_id: str,
    loyalty: float,
    tick_time: datetime,
) -> None:
    db.session.add(BunkerLoyalty(user_id=user_id, loyalty=loyalty, timestamp=tick_time))


def game_tick(app: Flask) -> None:
    """One game tick: update all time-varying game systems for every player.

    All writes share one DB commit per tick so timestamps are aligned and the
    state used to gate decisions (radiation vs safe threshold, loyalty vs
    departure rate) is always the *pre-tick* reading.

    Ordering within each tick
    -------------------------
    0. Random events        — auto-resolve expired rows; maybe spawn one new
                              event; stash food consumption multiplier.
    1. Radiation decay      — based on wall-clock elapsed time.
    2. Loyalty changes      — crank overwork penalty applied first so that
                              a badly overworked crank already shows the
                              disloyalty hit this same tick.
    3. Population departures— uses post-penalty loyalty so that overworking
                              has an immediate effect on resident willingness
                              to leave.
    4. Narrative beats       — one-shot scripted lines when triggers fire
                              (logged in ``user_narrative_deliveries``).
    5. Energy net change    — draw from active systems, generation from crank
                              workers, both scaled by elapsed time.
    6. Food net change      — farm workers produce; consumption uses *pre-tick*
                              population headcount (same gating window as other
                              systems). Worker assignments are normalized to
                              post-departure population before energy/food.
                              Active random events may multiply consumption only.
    7. Loyalty sample       — records crank-adjusted loyalty plus any auto-resolve
                              loyalty delta from step 0.

    Time series and BunkerSystems reads share one app_context transaction.
    """
    with app.app_context():
        decay_half_life_seconds = app.config["DECAY_HALF_LIFE_SECONDS"]
        radiation_display_noise_max = app.config["RADIATION_DISPLAY_NOISE_MAX"]
        radiation_safe_threshold = app.config["RADIATION_SAFE_THRESHOLD"]
        base_departure_rate = app.config["BASE_DEPARTURE_RATE"]
        crank_workers_loyalty_threshold = app.config["CRANK_WORKERS_LOYALTY_THRESHOLD"]
        loyalty_penalty_per_excess_crank_worker = app.config["CRANK_WORKERS_LOYALTY_PENALTY"]
        lights_power_draw_per_second = app.config["LIGHTS_POWER_DRAW"]
        crank_power_per_worker_per_second = app.config["CRANK_POWER_PER_WORKER"]
        food_per_capita_per_second = app.config["FOOD_PER_CAPITA_PER_SECOND"]
        food_per_worker_per_second = app.config["FOOD_PER_WORKER_PER_SECOND"]
        tick_time = datetime.now(timezone.utc)

        users = db.session.scalars(select(User)).all()
        if not users:
            return

        processed_user_count = 0
        gamestate_snapshots: list[dict[str, object]] = []
        gs_interval = float(app.config["GAMESTATE_LOG_INTERVAL_SECONDS"])
        for user in users:
            user_id = user.id

            readings = fetch_game_tick_readings_for_user(user_id)
            if readings is None:
                log.warning("user %s is missing seed data, skipping tick", user_id)
                continue

            elapsed_seconds = elapsed_seconds_for_game_tick(
                tick_time, readings.latest_radiation_level
            )
            if elapsed_seconds is None:
                # Clock skew or duplicate run; skip rather than regress any value.
                continue

            auto_loyalty_adj = auto_resolve_if_due(user_id, tick_time)

            if readings.latest_food_reserve is not None:
                spawn_food = readings.latest_food_reserve.level
            else:
                spawn_food = app.config["INITIAL_FOOD"]
            try_spawn_event(
                user_id,
                EventSpawnContext(
                    latest_food_level=spawn_food,
                    population_count=readings.latest_population_sample.count,
                ),
                tick_time,
            )
            food_mult = active_event_food_multiplier(user_id)

            handle_radiation_decay(
                user_id,
                readings.latest_radiation_level,
                elapsed_seconds,
                decay_half_life_seconds,
                radiation_display_noise_max,
                tick_time,
            )

            adjusted_loyalty = handle_loyalty_change(
                readings.latest_loyalty_sample,
                readings.bunker_systems,
                crank_workers_loyalty_threshold,
                loyalty_penalty_per_excess_crank_worker,
            )

            had_prior_departure_event = user_had_prior_departure_event(user_id)
            had_prior_welcome_message = user_had_prior_welcome_message(user_id)

            departed_this_tick = handle_population_departures(
                user_id,
                readings.latest_radiation_level,
                readings.latest_population_sample,
                adjusted_loyalty,
                radiation_safe_threshold,
                base_departure_rate,
                tick_time,
            )

            if readings.bunker_systems is not None:
                post_pop = readings.latest_population_sample.count - departed_this_tick
                normalize_worker_assignments(readings.bunker_systems, post_pop)

            deliver_pending_narrative_messages(
                NarrativeContext(
                    user_id=user_id,
                    tick_time=tick_time,
                    departed_this_tick=departed_this_tick,
                    had_prior_departure_event=had_prior_departure_event,
                    had_prior_welcome_message=had_prior_welcome_message,
                )
            )

            if (
                readings.latest_energy_reserve is not None
                and readings.bunker_systems is not None
            ):
                handle_energy_reserve_change(
                    user_id,
                    readings.latest_energy_reserve,
                    readings.bunker_systems,
                    elapsed_seconds,
                    lights_power_draw_per_second,
                    crank_power_per_worker_per_second,
                    tick_time,
                )

            if readings.latest_food_reserve is not None:
                food_start_level = readings.latest_food_reserve.level
            else:
                food_start_level = app.config["INITIAL_FOOD"]

            if readings.bunker_systems is not None:
                handle_food_reserve_change(
                    user_id,
                    food_start_level,
                    readings.bunker_systems,
                    readings.latest_population_sample.count,
                    elapsed_seconds,
                    food_per_capita_per_second,
                    food_per_worker_per_second,
                    tick_time,
                    food_consumption_multiplier=food_mult,
                )

            final_loyalty = max(0.0, min(100.0, adjusted_loyalty + auto_loyalty_adj))
            record_loyalty_sample(user_id, final_loyalty, tick_time)

            ev_row = active_event_row(user_id)
            energy_val = readings.latest_energy_reserve
            gamestate_snapshots.append(
                {
                    "user_id": user_id,
                    "population": readings.latest_population_sample.count,
                    "food": food_start_level,
                    "loyalty_final": final_loyalty,
                    "radiation_truth": readings.latest_radiation_level.level,
                    "energy": (
                        f"{energy_val.level:.2f}"
                        if energy_val is not None
                        else "na"
                    ),
                    "departed_tick": departed_this_tick,
                    "active_event": ev_row.kind if ev_row is not None else None,
                    "food_mult": food_mult,
                    "crank_workers": (
                        readings.bunker_systems.crank_workers
                        if readings.bunker_systems is not None
                        else 0
                    ),
                    "food_workers": (
                        readings.bunker_systems.food_workers
                        if readings.bunker_systems is not None
                        else 0
                    ),
                }
            )

            processed_user_count += 1

        _maybe_log_gamestate_snapshots(gamestate_snapshots, gs_interval)

        if processed_user_count:
            db.session.commit()
            log.debug("game tick: processed %d user(s)", processed_user_count)


def post_test_message(app: Flask) -> None:
    """Write a test system message to every active player once per minute.

    Replace this job (or add siblings) to deliver real scripted events.
    The body must be plain text — no HTML.
    """
    with app.app_context():
        users = db.session.scalars(select(User)).all()
        if not users:
            return
        now = datetime.now(timezone.utc)
        for user in users:
            db.session.add(SystemMessage(
                user_id=user.id,
                body="All systems normal.",
                timestamp=now,
            ))
        db.session.commit()
        log.debug("posted test message to %d user(s)", len(users))
