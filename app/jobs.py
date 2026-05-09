"""Background jobs run by APScheduler."""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from flask import Flask
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .extensions import db
from . import constants
from .models import (
    BunkerBoredom,
    BunkerCropPlot,
    BunkerDoubt,
    BunkerFarmingSystem,
    BunkerLightingSystem,
    BunkerLoyalty,
    BunkerPopulation,
    BunkerPowerCrankSystem,
    BunkerProfession,
    BunkerProfessionSnapshot,
    EnergyReserve,
    FoodReserve,
    RadiationLevel,
    SystemMessage,
    User,
    UserNarrativeDelivery,
)
from .professions import (
    PROFESSION_FARMING,
    PROFESSION_IDLE,
    PROFESSION_INVESTIGATION,
    PROFESSION_POWER_CRANK,
    PROFESSION_RAT_TRAPPING,
)
from .events import (
    EventDefinition,
    active_event_tick_effects,
    auto_resolve_if_due,
    combined_rats_consumption_per_second_for_trappers,
    finalize_investigation_if_due,
    player_has_active_event_kind,
    player_has_any_active_event,
    rat_trapper_food_production_per_second,
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
        pressure = s.get("hidden_pressure_active")
        pressure_s = "yes" if pressure else "no"
        log.info(
            "gamestate user=%s pop=%d food=%.1f loyalty=%.1f rad_truth=%.2f "
            "energy=%s departed_tick=%s pressure=%s food_mult=%.2f "
            "crank_workers=%d food_workers=%d",
            s["user_id"],
            s["population"],
            s["food"],
            s["loyalty_final"],
            s["radiation_truth"],
            s["energy"],
            s["departed_tick"],
            pressure_s,
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
    latest_boredom_sample: BunkerBoredom
    latest_doubt_sample: BunkerDoubt
    latest_energy_reserve: EnergyReserve | None
    latest_food_reserve: FoodReserve | None
    lighting: BunkerLightingSystem | None
    power_crank: BunkerPowerCrankSystem | None
    farming: BunkerFarmingSystem | None
    idle_profession: BunkerProfession | None
    investigation_profession: BunkerProfession | None
    user: User


def drift_rat_background_consumption(user: User, elapsed_seconds: float) -> None:
    """Random-walk silo rat drain after ``rats_silo_intro`` (scaled for catch-up ticks)."""
    if not user.silo_rats_introduced:
        return
    scale = min(3.0, max(0.0, elapsed_seconds))
    step = float(constants.RAT_BACKGROUND_DRIFT_STEP_PS) * scale
    user.rat_background_consumption_ps += random.uniform(-step, step)
    user.rat_background_consumption_ps = max(
        float(constants.RAT_BACKGROUND_DRAIN_MIN_PS),
        min(
            float(constants.RAT_BACKGROUND_DRAIN_MAX_PS),
            user.rat_background_consumption_ps,
        ),
    )


def _investigation_worker_count(readings: GameTickReadings) -> int:
    if readings.investigation_profession is None:
        return 0
    return readings.investigation_profession.count


def _crank_worker_count(readings: GameTickReadings) -> int:
    if readings.power_crank is None or readings.power_crank.profession_line is None:
        return 0
    return readings.power_crank.profession_line.count


def _farm_worker_count(readings: GameTickReadings) -> int:
    if readings.farming is None or readings.farming.profession_line is None:
        return 0
    return readings.farming.profession_line.count


def _rat_trapper_count(readings: GameTickReadings) -> int:
    if readings.farming is None or readings.farming.rat_trapper_line is None:
        return 0
    return readings.farming.rat_trapper_line.count


def _lights_on(readings: GameTickReadings) -> bool:
    if readings.lighting is None:
        return True
    return readings.lighting.lights_on


def _facilities_ready(readings: GameTickReadings) -> bool:
    return (
        readings.lighting is not None
        and readings.power_crank is not None
        and readings.power_crank.profession_line is not None
        and readings.farming is not None
        and readings.farming.profession_line is not None
        and readings.farming.rat_trapper_line is not None
    )


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

    latest_boredom_sample = db.session.scalars(
        select(BunkerBoredom)
        .where(BunkerBoredom.user_id == user_id)
        .order_by(BunkerBoredom.timestamp.desc())
        .limit(1)
    ).first()

    latest_doubt_sample = db.session.scalars(
        select(BunkerDoubt)
        .where(BunkerDoubt.user_id == user_id)
        .order_by(BunkerDoubt.timestamp.desc())
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

    lighting = db.session.get(BunkerLightingSystem, user_id)
    power_crank = db.session.scalars(
        select(BunkerPowerCrankSystem)
        .where(BunkerPowerCrankSystem.user_id == user_id)
        .options(selectinload(BunkerPowerCrankSystem.profession_line))
    ).first()
    farming = db.session.scalars(
        select(BunkerFarmingSystem)
        .where(BunkerFarmingSystem.user_id == user_id)
        .options(
            selectinload(BunkerFarmingSystem.profession_line),
            selectinload(BunkerFarmingSystem.rat_trapper_line),
        )
    ).first()
    idle_profession = db.session.scalars(
        select(BunkerProfession).where(
            BunkerProfession.user_id == user_id,
            BunkerProfession.profession == PROFESSION_IDLE,
        )
    ).first()
    investigation_profession = db.session.scalars(
        select(BunkerProfession).where(
            BunkerProfession.user_id == user_id,
            BunkerProfession.profession == PROFESSION_INVESTIGATION,
        )
    ).first()

    if (
        latest_radiation_level is None
        or latest_population_sample is None
        or latest_loyalty_sample is None
        or latest_boredom_sample is None
        or latest_doubt_sample is None
    ):
        return None

    user_row = db.session.get(User, user_id)
    if user_row is None:
        return None

    return GameTickReadings(
        latest_radiation_level=latest_radiation_level,
        latest_population_sample=latest_population_sample,
        latest_loyalty_sample=latest_loyalty_sample,
        latest_boredom_sample=latest_boredom_sample,
        latest_doubt_sample=latest_doubt_sample,
        latest_energy_reserve=latest_energy_reserve,
        latest_food_reserve=latest_food_reserve,
        lighting=lighting,
        power_crank=power_crank,
        farming=farming,
        idle_profession=idle_profession,
        investigation_profession=investigation_profession,
        user=user_row,
    )


def elapsed_seconds_for_game_tick(
    tick_time: datetime,
    latest_radiation_level: RadiationLevel,
) -> float | None:
    """Wall-clock delta since last radiation sample.

    Returns ``0.0`` when timestamps coincide (same-second ticks) so the tick still
    runs (random events, decay at zero step). Only negative deltas return
    ``None`` (clock skew / duplicate row ordering).
    """
    elapsed_seconds = (tick_time - latest_radiation_level.timestamp).total_seconds()
    if elapsed_seconds < 0:
        return None
    return max(0.0, elapsed_seconds)


def noisy_radiation_display(true_level: float, noise_max: float) -> float:
    """Player-facing reading: truth plus uniform jitter in ``[-noise_max, noise_max]``."""
    offset = random.uniform(-noise_max, noise_max)
    return max(0.0, true_level + offset)


def radiation_truth_after_decay(
    current_truth_level: float,
    elapsed_seconds: float,
    decay_half_life_seconds: float,
) -> float:
    """Outdoor radiation truth after exponential decay (same formula as tick writes)."""
    return current_truth_level * (0.5 ** (elapsed_seconds / decay_half_life_seconds))


def handle_radiation_decay(
    user_id: str,
    latest_radiation_level: RadiationLevel,
    elapsed_seconds: float,
    decay_half_life_seconds: float,
    display_noise_max: float,
    tick_time: datetime,
) -> None:
    new_radiation_level = radiation_truth_after_decay(
        latest_radiation_level.level,
        elapsed_seconds,
        decay_half_life_seconds,
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


def handle_boredom_and_doubt_tick(
    user_id: str,
    latest_boredom_sample: BunkerBoredom,
    latest_doubt_sample: BunkerDoubt,
    new_radiation_truth: float,
    elapsed_seconds: float,
    boredom_per_second: float,
    doubt_growth_max_per_second: float,
    initial_radiation: float,
    tick_time: datetime,
) -> None:
    new_boredom = min(
        100.0,
        latest_boredom_sample.boredom + boredom_per_second * elapsed_seconds,
    )
    denom = max(float(initial_radiation), 1e-9)
    doubt_factor = max(0.0, 1.0 - (new_radiation_truth / denom))
    new_doubt = min(
        100.0,
        latest_doubt_sample.doubt + doubt_growth_max_per_second * doubt_factor * elapsed_seconds,
    )
    db.session.add(
        BunkerBoredom(user_id=user_id, boredom=new_boredom, timestamp=tick_time)
    )
    db.session.add(
        BunkerDoubt(user_id=user_id, doubt=new_doubt, timestamp=tick_time)
    )


def handle_loyalty_change(
    latest_loyalty_sample: BunkerLoyalty,
    crank_worker_count: int,
    crank_workers_loyalty_threshold: int,
    loyalty_penalty_per_excess_crank_worker: float,
) -> float:
    new_loyalty = latest_loyalty_sample.loyalty
    if crank_worker_count > crank_workers_loyalty_threshold:
        excess_crank_workers = crank_worker_count - crank_workers_loyalty_threshold
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


def normalize_worker_assignments(
    crank_line: BunkerProfession | None,
    farm_line: BunkerProfession | None,
    rat_trapper_line: BunkerProfession | None,
    idle_line: BunkerProfession | None,
    investigation_line: BunkerProfession | None,
    population_cap: int,
    tick_time: datetime,
) -> None:
    """Clamp crank, farm, rat trappers to shared pool minus Investigation; refresh Idle."""
    inv_n = investigation_line.count if investigation_line is not None else 0
    inv_n = max(0, inv_n)
    pool = max(0, population_cap - inv_n)
    if crank_line is None or farm_line is None or rat_trapper_line is None:
        return
    crank_line.count = max(0, min(crank_line.count, pool))
    farm_line.count = max(0, min(farm_line.count, pool))
    rat_trapper_line.count = max(0, min(rat_trapper_line.count, pool))
    while crank_line.count + farm_line.count + rat_trapper_line.count > pool:
        if farm_line.count > 0:
            farm_line.count -= 1
        elif rat_trapper_line.count > 0:
            rat_trapper_line.count -= 1
        else:
            crank_line.count -= 1
    crank_line.updated_at = tick_time
    farm_line.updated_at = tick_time
    rat_trapper_line.updated_at = tick_time
    if idle_line is not None:
        idle_line.count = max(
            0,
            population_cap
            - crank_line.count
            - farm_line.count
            - rat_trapper_line.count
            - inv_n,
        )
        idle_line.updated_at = tick_time


def record_bunker_profession_snapshots(
    user_id: str,
    population: int,
    readings: GameTickReadings,
    tick_time: datetime,
) -> None:
    """Append profession rows (crank, farming, investigation, idle) for Grafana."""
    crank = _crank_worker_count(readings)
    farm = _farm_worker_count(readings)
    rat_n = _rat_trapper_count(readings)
    inv = _investigation_worker_count(readings)
    idle_count = (
        readings.idle_profession.count
        if readings.idle_profession is not None
        else max(0, population - crank - farm - rat_n - inv)
    )
    for profession, count in (
        (PROFESSION_POWER_CRANK, crank),
        (PROFESSION_FARMING, farm),
        (PROFESSION_RAT_TRAPPING, rat_n),
        (PROFESSION_INVESTIGATION, inv),
        (PROFESSION_IDLE, idle_count),
    ):
        db.session.add(
            BunkerProfessionSnapshot(
                user_id=user_id,
                profession=profession,
                count=count,
                timestamp=tick_time,
            )
        )


def accumulate_growing_crop_worker_seconds(
    user_id: str,
    farm_workers: int,
    elapsed_seconds: float,
    tick_time: datetime,
) -> None:
    """Credit farm-worker×Δt only for wall-clock overlap with each crop's growth window.

    ``elapsed_seconds`` is the full simulation step (since the last radiation sample).
    A newly planted crop must not receive credit for time before ``crop_planted_at``,
    otherwise worker-seconds include pre-plant time and inflate mean workers at harvest.
    """
    if elapsed_seconds <= 0:
        return
    plots = db.session.scalars(
        select(BunkerCropPlot).where(BunkerCropPlot.user_id == user_id)
    ).all()
    window_start = tick_time - timedelta(seconds=elapsed_seconds)
    fw = float(farm_workers)
    for plot in plots:
        if plot.crop_ready_at is None or plot.crop_planted_at is None:
            continue
        planted = plot.crop_planted_at
        ready = plot.crop_ready_at
        overlap_start = max(planted, window_start)
        overlap_end = min(tick_time, ready)
        if overlap_end <= overlap_start:
            continue
        dt = (overlap_end - overlap_start).total_seconds()
        if dt <= 0:
            continue
        plot.growth_worker_seconds += fw * dt


def handle_food_reserve_change(
    user_id: str,
    current_food_level: float,
    farm_workers: int,
    population_for_consumption: int,
    elapsed_seconds: float,
    food_per_capita_per_second: float,
    food_per_worker_per_second: float,
    tick_time: datetime,
    food_consumption_multiplier: float = 1.0,
    rat_trapper_production_ps: float = 0.0,
    rat_background_consumption_ps: float = 0.0,
) -> None:
    """Net food change: human consumption plus resident rats, minus trapper salvage."""
    consumption_ps = (
        population_for_consumption * food_per_capita_per_second * food_consumption_multiplier
        + rat_background_consumption_ps
    )
    production_ps = farm_workers * food_per_worker_per_second + rat_trapper_production_ps
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
    lights_on: bool,
    crank_workers: int,
    elapsed_seconds: float,
    lights_power_draw_per_second: float,
    crank_power_per_worker_per_second: float,
    tick_time: datetime,
) -> None:
    power_draw = lights_power_draw_per_second if lights_on else 0.0
    generation_power = crank_workers * crank_power_per_worker_per_second
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
    2. Boredom / doubt      — boredom rises with time; doubt rises as outdoor truth
                              radiation falls below session baseline.
    3. Loyalty changes      — crank overwork penalty applied first so that
                              a badly overworked crank already shows the
                              disloyalty hit this same tick.
    4. Population departures— uses post-penalty loyalty so that overworking
                              has an immediate effect on resident willingness
                              to leave.
    5. Narrative beats       — one-shot scripted lines when triggers fire
                              (logged in ``user_narrative_deliveries``).
    6. Energy net change    — draw from active systems, generation from crank
                              workers, both scaled by elapsed time.
    7. Food net change      — human consumption × optional swarm mult plus fluctuating
                              resident rat drain; trapper salvage scales with combined rat drain.
    8. Loyalty sample       — records crank-adjusted loyalty plus any auto-resolve
                              loyalty delta from step 0.

    Time series and split facility reads share one app_context transaction.
    """
    with app.app_context():
        decay_half_life_seconds = constants.DECAY_HALF_LIFE_SECONDS
        radiation_display_noise_max = constants.RADIATION_DISPLAY_NOISE_MAX
        radiation_safe_threshold = constants.RADIATION_SAFE_THRESHOLD
        base_departure_rate = constants.BASE_DEPARTURE_RATE
        crank_workers_loyalty_threshold = constants.CRANK_WORKERS_LOYALTY_THRESHOLD
        loyalty_penalty_per_excess_crank_worker = constants.CRANK_WORKERS_LOYALTY_PENALTY
        lights_power_draw_per_second = constants.LIGHTS_POWER_DRAW
        crank_power_per_worker_per_second = constants.CRANK_POWER_PER_WORKER
        food_per_capita_per_second = constants.FOOD_PER_CAPITA_PER_SECOND
        food_per_worker_per_second = constants.FOOD_PER_WORKER_PER_SECOND
        boredom_per_second = constants.BOREDOM_PER_SECOND
        doubt_growth_max_per_second = constants.DOUBT_GROWTH_MAX_PER_SECOND
        initial_radiation = constants.INITIAL_RADIATION
        tick_time = datetime.now(timezone.utc)

        users = db.session.scalars(select(User)).all()
        if not users:
            return

        processed_user_count = 0
        gamestate_snapshots: list[dict[str, object]] = []
        gs_interval = float(constants.GAMESTATE_LOG_INTERVAL_SECONDS)
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

            auto_loyalty_adj = finalize_investigation_if_due(user_id, tick_time)
            auto_loyalty_adj += auto_resolve_if_due(user_id, tick_time)

            if readings.latest_food_reserve is not None:
                spawn_food = readings.latest_food_reserve.level
            else:
                spawn_food = constants.INITIAL_FOOD
            try_spawn_event(
                user_id,
                spawn_food,
                readings.latest_population_sample.count,
                _rat_trapper_count(readings),
                tick_time,
            )
            food_mult = active_event_tick_effects(user_id, tick_time).food_consumption_multiplier
            drift_rat_background_consumption(readings.user, elapsed_seconds)

            handle_radiation_decay(
                user_id,
                readings.latest_radiation_level,
                elapsed_seconds,
                decay_half_life_seconds,
                radiation_display_noise_max,
                tick_time,
            )

            new_radiation_truth = radiation_truth_after_decay(
                readings.latest_radiation_level.level,
                elapsed_seconds,
                decay_half_life_seconds,
            )
            handle_boredom_and_doubt_tick(
                user_id,
                readings.latest_boredom_sample,
                readings.latest_doubt_sample,
                new_radiation_truth,
                elapsed_seconds,
                boredom_per_second,
                doubt_growth_max_per_second,
                initial_radiation,
                tick_time,
            )

            adjusted_loyalty = handle_loyalty_change(
                readings.latest_loyalty_sample,
                _crank_worker_count(readings),
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

            post_pop = readings.latest_population_sample.count - departed_this_tick
            crank_line = (
                readings.power_crank.profession_line
                if readings.power_crank is not None
                else None
            )
            farm_line = (
                readings.farming.profession_line
                if readings.farming is not None
                else None
            )
            rat_line = (
                readings.farming.rat_trapper_line
                if readings.farming is not None
                else None
            )
            if _facilities_ready(readings):
                normalize_worker_assignments(
                    crank_line,
                    farm_line,
                    rat_line,
                    readings.idle_profession,
                    readings.investigation_profession,
                    post_pop,
                    tick_time,
                )
                accumulate_growing_crop_worker_seconds(
                    user_id,
                    _farm_worker_count(readings),
                    elapsed_seconds,
                    tick_time,
                )
                if readings.power_crank is not None:
                    readings.power_crank.updated_at = tick_time
                if readings.farming is not None:
                    readings.farming.updated_at = tick_time
            record_bunker_profession_snapshots(
                user_id, post_pop, readings, tick_time
            )

            deliver_pending_narrative_messages(
                NarrativeContext(
                    user_id=user_id,
                    tick_time=tick_time,
                    departed_this_tick=departed_this_tick,
                    had_prior_departure_event=had_prior_departure_event,
                    had_prior_welcome_message=had_prior_welcome_message,
                )
            )

            if readings.latest_energy_reserve is not None and _facilities_ready(readings):
                handle_energy_reserve_change(
                    user_id,
                    readings.latest_energy_reserve,
                    _lights_on(readings),
                    _crank_worker_count(readings),
                    elapsed_seconds,
                    lights_power_draw_per_second,
                    crank_power_per_worker_per_second,
                    tick_time,
                )

            if readings.latest_food_reserve is not None:
                food_start_level = readings.latest_food_reserve.level
            else:
                food_start_level = constants.INITIAL_FOOD

            if _facilities_ready(readings):
                swarm_active = player_has_active_event_kind(user_id, EventDefinition.RATS_SILO)
                combined_rat = combined_rats_consumption_per_second_for_trappers(
                    readings.latest_population_sample.count,
                    readings.user.rat_background_consumption_ps,
                    swarm_active,
                )
                trap_prod = rat_trapper_food_production_per_second(
                    _rat_trapper_count(readings),
                    combined_rat,
                )
                rat_bg_consume = (
                    readings.user.rat_background_consumption_ps
                    if readings.user.silo_rats_introduced
                    else 0.0
                )
                handle_food_reserve_change(
                    user_id,
                    food_start_level,
                    _farm_worker_count(readings),
                    readings.latest_population_sample.count,
                    elapsed_seconds,
                    food_per_capita_per_second,
                    food_per_worker_per_second,
                    tick_time,
                    food_consumption_multiplier=food_mult,
                    rat_trapper_production_ps=trap_prod,
                    rat_background_consumption_ps=rat_bg_consume,
                )

            final_loyalty = max(0.0, min(100.0, adjusted_loyalty + auto_loyalty_adj))
            record_loyalty_sample(user_id, final_loyalty, tick_time)

            ev_active = player_has_any_active_event(user_id)
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
                    "hidden_pressure_active": ev_active,
                    "food_mult": food_mult,
                    "crank_workers": _crank_worker_count(readings),
                    "food_workers": _farm_worker_count(readings),
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
