"""Background jobs run by APScheduler."""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from flask import Flask
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from .extensions import db
from . import bad_apple_frames
from . import constants
from . import inner_circle
from . import movie_pixel_frames
from .environment_pixel_reference import (
    apply_uniform_tick_noise,
    environment_pixel_cells_from_reference_image,
)
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
    BunkerSocialState,
    BunkerTheatreSystem,
    EnergyReserve,
    EnvironmentPixelNoiseSample,
    FoodReserve,
    SocialMoviePixelSample,
    PlayerMovieExhaustion,
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
    PROFESSION_THEATRE,
)
from .events import (
    EventDefinition,
    active_event_tick_effects,
    auto_resolve_if_due,
    combined_rats_consumption_per_second_for_trappers,
    enqueue_fireside_rhetoric_backlash,
    enqueue_geiger_rumor_exodus,
    finalize_investigation_if_due,
    geiger_rumor_forced_departures_this_tick,
    halt_geiger_rumor_exodus,
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
    theatre: BunkerTheatreSystem | None
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


def _theatre_worker_count(readings: GameTickReadings) -> int:
    if readings.theatre is None or readings.theatre.profession_line is None:
        return 0
    return readings.theatre.profession_line.count


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
        and readings.theatre is not None
        and readings.theatre.profession_line is not None
    )


_bad_apple_frame_seq = 0


def advance_bad_apple_frame_index() -> int:
    """Consume the next Bad Apple frame index (one step per ``game_tick``)."""
    global _bad_apple_frame_seq
    n = int(constants.BAD_APPLE_FRAME_COUNT)
    if n < 1:
        return 0
    idx = _bad_apple_frame_seq % n
    _bad_apple_frame_seq += 1
    return idx


def current_bad_apple_frame_index() -> int:
    """Frame index for the current animation position without advancing (e.g. new player seed)."""
    n = int(constants.BAD_APPLE_FRAME_COUNT)
    if n < 1:
        return 0
    return _bad_apple_frame_seq % n


def record_environment_pixel_noise_sample(
    user_id: str,
    tick_time: datetime,
    animation_frame_index: int = 0,
) -> None:
    """Replace this player's heatmap history with Bad Apple, reference-image luminance, or random noise.

    When ``app/assets/images/bad_apple`` contains ``frame_00.png`` … (see ``BAD_APPLE_FRAME_COUNT``),
    ``animation_frame_index`` selects the clip frame. Otherwise falls back to
    ``environment_pixel_reference.png``, then uniform random noise.
    ``ENVIRONMENT_PIXEL_REFERENCE_TICK_NOISE_HALF_RANGE`` jitter applies to sampled luminance frames.
    """
    cols_n = int(constants.ENVIRONMENT_PIXEL_GRID_COLS)
    rows_n = int(constants.ENVIRONMENT_PIXEL_GRID_ROWS)
    n_snapshots = int(constants.ENVIRONMENT_PIXEL_BACKFILL_SAMPLES)
    span_s = float(constants.ENVIRONMENT_PIXEL_BACKFILL_SPAN_SECONDS)
    delta_s = span_s / max(1, n_snapshots - 1) if n_snapshots > 1 else 0.0

    frame: list[list[float]]
    ba = (
        bad_apple_frames.bad_apple_cells(animation_frame_index, cols_n, rows_n)
        if bad_apple_frames.bad_apple_frames_ready()
        else None
    )
    if ba is not None:
        frame = [list(row) for row in ba]
        apply_uniform_tick_noise(
            frame, float(constants.ENVIRONMENT_PIXEL_REFERENCE_TICK_NOISE_HALF_RANGE)
        )
    else:
        ref = environment_pixel_cells_from_reference_image(cols_n, rows_n)
        if ref is None:
            frame = [[random.random() for _ in range(cols_n)] for _ in range(rows_n)]
        else:
            frame = [list(row) for row in ref]
            apply_uniform_tick_noise(
                frame, float(constants.ENVIRONMENT_PIXEL_REFERENCE_TICK_NOISE_HALF_RANGE)
            )
    db.session.execute(
        delete(EnvironmentPixelNoiseSample).where(
            EnvironmentPixelNoiseSample.user_id == user_id
        )
    )
    for i in range(n_snapshots):
        ts = tick_time - timedelta(seconds=(n_snapshots - 1 - i) * delta_s)
        cells = [frame[r][i] for r in range(rows_n)]
        db.session.add(
            EnvironmentPixelNoiseSample(
                user_id=user_id,
                timestamp=ts,
                grid_cols=1,
                grid_rows=rows_n,
                cells=cells,
            )
        )


def reset_social_movie_pixel_frame_for_screening(user_id: str) -> None:
    """Start the heatmap PNG clip from frame 0 when a screening begins."""
    social = db.session.get(BunkerSocialState, user_id)
    if social is not None:
        social.movie_pixel_frame_index = 0


def record_social_movie_pixel_sample(user_id: str, tick_time: datetime) -> None:
    """Replace movie-screen strips with **one** channel: active screening or idle noise.

    Grafana reads whichever ``movie_id`` matches ``bunker_social_state`` (or the idle sentinel).
    Only that channel is written each tick—no parallel strips for catalog titles that are not on-screen.
    """
    cols_n = int(constants.SOCIAL_MOVIE_PIXEL_GRID_COLS)
    rows_n = int(constants.SOCIAL_MOVIE_PIXEL_GRID_ROWS)
    n_snapshots = int(constants.SOCIAL_MOVIE_PIXEL_BACKFILL_SAMPLES)
    span_s = float(constants.SOCIAL_MOVIE_PIXEL_BACKFILL_SPAN_SECONDS)
    delta_s = span_s / max(1, n_snapshots - 1) if n_snapshots > 1 else 0.0
    n_frames = int(constants.SOCIAL_MOVIE_PIXEL_SEQUENCE_FRAME_COUNT)

    db.session.execute(
        delete(SocialMoviePixelSample).where(SocialMoviePixelSample.user_id == user_id)
    )

    social = db.session.scalars(
        select(BunkerSocialState).where(BunkerSocialState.user_id == user_id)
    ).first()

    screening_mid: str | None = None
    if (
        social is not None
        and social.movie_screening_movie_id is not None
        and social.movie_screening_started_at is not None
    ):
        screening_mid = social.movie_screening_movie_id

    wrote_screening = False
    if screening_mid is not None and social is not None:
        subdir = movie_pixel_frames.asset_subdir_for_movie_id(screening_mid)
        if subdir is not None:
            frame_idx = (
                int(social.movie_pixel_frame_index) % n_frames if n_frames >= 1 else 0
            )
            grid = movie_pixel_frames.movie_cells(subdir, frame_idx, cols_n, rows_n)
            if grid is None:
                frame = [[random.random() for _ in range(cols_n)] for _ in range(rows_n)]
            else:
                frame = [list(row) for row in grid]

            for i in range(n_snapshots):
                ts = tick_time - timedelta(seconds=(n_snapshots - 1 - i) * delta_s)
                cells = [frame[r][i] for r in range(rows_n)]
                db.session.add(
                    SocialMoviePixelSample(
                        user_id=user_id,
                        movie_id=screening_mid,
                        timestamp=ts,
                        grid_cols=1,
                        grid_rows=rows_n,
                        cells=cells,
                    )
                )

            if n_frames >= 1:
                social.movie_pixel_frame_index = (
                    int(social.movie_pixel_frame_index) + 1
                ) % n_frames
            wrote_screening = True

    if not wrote_screening:
        idle_mid = constants.SOCIAL_MOVIE_PIXEL_IDLE_HEATMAP_MOVIE_ID
        for i in range(n_snapshots):
            ts = tick_time - timedelta(seconds=(n_snapshots - 1 - i) * delta_s)
            cells = [random.random() for _ in range(rows_n)]
            db.session.add(
                SocialMoviePixelSample(
                    user_id=user_id,
                    movie_id=idle_mid,
                    timestamp=ts,
                    grid_cols=1,
                    grid_rows=rows_n,
                    cells=cells,
                )
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
    theatre = db.session.scalars(
        select(BunkerTheatreSystem)
        .where(BunkerTheatreSystem.user_id == user_id)
        .options(selectinload(BunkerTheatreSystem.profession_line))
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
        theatre=theatre,
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
    boredom_relief: float = 0.0,
) -> tuple[float, float]:
    new_boredom = min(
        100.0,
        latest_boredom_sample.boredom + boredom_per_second * elapsed_seconds,
    )
    new_boredom = max(0.0, new_boredom - max(0.0, boredom_relief))
    denom = max(float(initial_radiation), 1e-9)
    doubt_factor = max(0.0, 1.0 - (new_radiation_truth / denom))
    new_doubt = min(
        100.0,
        latest_doubt_sample.doubt + doubt_growth_max_per_second * doubt_factor * elapsed_seconds,
    )
    boredom_sample = BunkerBoredom(
        user_id=user_id, boredom=new_boredom, timestamp=tick_time
    )
    db.session.add(boredom_sample)
    db.session.add(
        BunkerDoubt(user_id=user_id, doubt=new_doubt, timestamp=tick_time)
    )
    return (new_boredom, new_doubt, boredom_sample)


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


def boredom_loyalty_drag(boredom: float, elapsed_seconds: float) -> float:
    """Loyalty lost this tick from boredom (scaled 0 at boredom 0, full rate at 100)."""
    scale = max(0.0, min(100.0, boredom)) / 100.0
    return scale * float(constants.BOREDOM_LOYALTY_DRAIN_PER_SECOND_AT_FULL) * elapsed_seconds


def movie_exhaustion_loyalty_drag(total_exhaustion: float, elapsed_seconds: float) -> float:
    """Loyalty lost from screening fatigue (combined exhaustion; no drain until sum reaches 50)."""
    capped = max(0.0, min(100.0, total_exhaustion))
    if capped < 50.0:
        return 0.0
    scale = (capped - 50.0) / 50.0
    return scale * float(constants.MOVIE_EXHAUSTION_LOYALTY_DRAIN_PER_SECOND_AT_FULL) * elapsed_seconds


def complete_due_movie_screenings_for_user(user_id: str, tick_time: datetime) -> None:
    """Apply boredom, doubt relief + exhaustion when a screening has finished its runtime."""
    social = db.session.get(BunkerSocialState, user_id)
    if social is None or social.movie_screening_movie_id is None:
        return
    started = social.movie_screening_started_at
    if started is None:
        return
    duration = float(constants.MOVIE_SCREENING_DURATION_SECONDS)
    age = (tick_time - started).total_seconds()
    if age + 1e-9 < duration:
        return

    movie_id = social.movie_screening_movie_id
    spec = constants.MOVIES_BY_ID.get(movie_id)
    if spec is None:
        social.movie_screening_movie_id = None
        social.movie_screening_started_at = None
        return

    latest = db.session.scalars(
        select(BunkerBoredom)
        .where(BunkerBoredom.user_id == user_id)
        .order_by(BunkerBoredom.timestamp.desc())
        .limit(1)
    ).first()
    if latest is None:
        social.movie_screening_movie_id = None
        social.movie_screening_started_at = None
        return

    exh_row = db.session.get(PlayerMovieExhaustion, (user_id, movie_id))
    if exh_row is None:
        exh_row = PlayerMovieExhaustion(
            user_id=user_id,
            movie_id=movie_id,
            exhaustion=0.0,
            screenings_completed=0,
            updated_at=tick_time,
        )
        db.session.add(exh_row)

    k = float(constants.SOCIAL_MOVIE_DIMINISH_K)
    uses = int(exh_row.screenings_completed)
    relief = float(spec.boredom_relief_base) / (1.0 + k * uses)
    new_boredom = max(0.0, float(latest.boredom) - relief)

    latest_doubt = db.session.scalars(
        select(BunkerDoubt)
        .where(BunkerDoubt.user_id == user_id)
        .order_by(BunkerDoubt.timestamp.desc())
        .limit(1)
    ).first()
    doubt_written = False
    if latest_doubt is not None:
        doubt_relief_amt = float(spec.doubt_relief_base) / (1.0 + k * uses)
        if doubt_relief_amt > 1e-12:
            new_doubt = max(0.0, float(latest_doubt.doubt) - doubt_relief_amt)
            db.session.add(
                BunkerDoubt(user_id=user_id, doubt=new_doubt, timestamp=tick_time)
            )
            doubt_written = True

    gain = float(constants.MOVIE_EXHAUSTION_GAIN_PER_PLAY)
    exh_row.exhaustion = float(exh_row.exhaustion) + gain
    exh_row.screenings_completed = uses + 1
    exh_row.updated_at = tick_time

    social.movie_screening_movie_id = None
    social.movie_screening_started_at = None

    db.session.add(BunkerBoredom(user_id=user_id, boredom=new_boredom, timestamp=tick_time))
    log.info(
        "movie screening complete: user=%s movie=%s boredom %.2f→%.2f doubt_written=%s ex_gain=%.1f",
        user_id,
        movie_id,
        latest.boredom,
        new_boredom,
        doubt_written,
        gain,
    )


def decay_player_movie_exhaustion(
    user_id: str, elapsed_seconds: float, tick_time: datetime
) -> float:
    """Decay each title's exhaustion; drop negligible rows. Returns sum after decay."""
    rows = db.session.scalars(
        select(PlayerMovieExhaustion).where(PlayerMovieExhaustion.user_id == user_id)
    ).all()
    decay_rate = float(constants.MOVIE_EXHAUSTION_DECAY_PER_SECOND)
    total = 0.0
    for row in rows:
        row.exhaustion = max(0.0, row.exhaustion - decay_rate * elapsed_seconds)
        row.updated_at = tick_time
        if row.exhaustion < 1e-6:
            if int(row.screenings_completed) > 0:
                row.exhaustion = 0.0
                row.updated_at = tick_time
            else:
                db.session.delete(row)
        else:
            total += row.exhaustion
    return total


def handle_theatre_tick(
    theatre: BunkerTheatreSystem,
    actor_count: int,
    tick_time: datetime,
    energy_level: float,
    elapsed_seconds: float,
) -> tuple[float, float, float]:
    """Returns (power_draw_per_second, loyalty_gain_this_tick, boredom_relief_this_tick).

    Loyalty accrues during ``writing``, ``rehearsing``, and ``ready`` (showing).
    Boredom relief applies only during ``ready``. Energy draw applies whenever actors > 0.
    """
    tw = float(constants.THEATRE_WRITE_SECONDS)
    tr = float(constants.THEATRE_REHEARSE_SECONDS)
    interval = float(constants.THEATRE_PERFORMANCE_INTERVAL_SECONDS)
    draw_per = float(constants.THEATRE_POWER_DRAW_PER_ACTOR)

    if actor_count <= 0:
        theatre.phase = constants.THEATRE_PHASE_IDLE
        theatre.next_performance_at = None
        theatre.phase_entered_at = tick_time
        theatre.updated_at = tick_time
        return (0.0, 0.0, 0.0)

    draw_ps = draw_per * float(actor_count)
    can_progress = energy_level > 1e-9

    if theatre.phase == constants.THEATRE_PHASE_IDLE:
        theatre.phase = constants.THEATRE_PHASE_WRITING
        theatre.phase_entered_at = tick_time
        theatre.next_performance_at = None

    loyalty_bonus = 0.0
    boredom_bonus = 0.0
    n_plays = len(constants.THEATRE_PLAY_TITLES)
    lp_sec = float(constants.THEATRE_LOYALTY_PER_SECOND)

    if can_progress:
        phase_age = (tick_time - theatre.phase_entered_at).total_seconds()

        if theatre.phase == constants.THEATRE_PHASE_WRITING:
            loyalty_bonus += lp_sec * elapsed_seconds
            if phase_age >= tw:
                theatre.phase = constants.THEATRE_PHASE_REHEARSING
                theatre.phase_entered_at = tick_time
        elif theatre.phase == constants.THEATRE_PHASE_REHEARSING:
            loyalty_bonus += lp_sec * elapsed_seconds
            if phase_age >= tr:
                theatre.phase = constants.THEATRE_PHASE_READY
                theatre.phase_entered_at = tick_time
                theatre.next_performance_at = tick_time + timedelta(seconds=interval)
        elif theatre.phase == constants.THEATRE_PHASE_READY:
            loyalty_bonus += lp_sec * elapsed_seconds
            boredom_bonus += (
                float(constants.THEATRE_BOREDOM_RELIEF_PER_SECOND) * elapsed_seconds
            )
            nxt = theatre.next_performance_at
            if nxt is not None and tick_time >= nxt:
                if n_plays > 0:
                    theatre.play_index = (int(theatre.play_index) + 1) % n_plays
                theatre.phase = constants.THEATRE_PHASE_WRITING
                theatre.phase_entered_at = tick_time
                theatre.next_performance_at = None

    theatre.updated_at = tick_time
    return (draw_ps, loyalty_bonus, boredom_bonus)


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
    forced_extra_departures: int = 0,
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

    post_after_normal = current_population - departed_count
    extra = max(0, min(int(forced_extra_departures), post_after_normal))
    total_departed = departed_count + extra
    final_count = current_population - total_departed

    db.session.add(
        BunkerPopulation(
            user_id=user_id,
            count=final_count,
            departed=total_departed,
            timestamp=tick_time,
        )
    )
    return total_departed


def normalize_worker_assignments(
    crank_line: BunkerProfession | None,
    farm_line: BunkerProfession | None,
    rat_trapper_line: BunkerProfession | None,
    theatre_line: BunkerProfession | None,
    idle_line: BunkerProfession | None,
    investigation_line: BunkerProfession | None,
    population_cap: int,
    tick_time: datetime,
) -> None:
    """Clamp crank, farm, rat trappers, theater to shared pool minus Investigation; refresh Idle."""
    inv_n = investigation_line.count if investigation_line is not None else 0
    inv_n = max(0, inv_n)
    pool = max(0, population_cap - inv_n)
    if crank_line is None or farm_line is None or rat_trapper_line is None or theatre_line is None:
        return
    crank_line.count = max(0, min(crank_line.count, pool))
    farm_line.count = max(0, min(farm_line.count, pool))
    rat_trapper_line.count = max(0, min(rat_trapper_line.count, pool))
    theatre_line.count = max(0, min(theatre_line.count, pool))
    while (
        crank_line.count
        + farm_line.count
        + rat_trapper_line.count
        + theatre_line.count
        > pool
    ):
        if farm_line.count > 0:
            farm_line.count -= 1
        elif rat_trapper_line.count > 0:
            rat_trapper_line.count -= 1
        elif theatre_line.count > 0:
            theatre_line.count -= 1
        else:
            crank_line.count -= 1
    crank_line.updated_at = tick_time
    farm_line.updated_at = tick_time
    rat_trapper_line.updated_at = tick_time
    theatre_line.updated_at = tick_time
    if idle_line is not None:
        idle_line.count = max(
            0,
            population_cap
            - crank_line.count
            - farm_line.count
            - rat_trapper_line.count
            - theatre_line.count
            - inv_n,
        )
        idle_line.updated_at = tick_time


def record_bunker_profession_snapshots(
    user_id: str,
    population: int,
    readings: GameTickReadings,
    tick_time: datetime,
) -> None:
    """Append profession rows (crank, farming, theater, investigation, idle) for Grafana."""
    crank = _crank_worker_count(readings)
    farm = _farm_worker_count(readings)
    rat_n = _rat_trapper_count(readings)
    theatre_n = _theatre_worker_count(readings)
    inv = _investigation_worker_count(readings)
    idle_count = (
        readings.idle_profession.count
        if readings.idle_profession is not None
        else max(0, population - crank - farm - rat_n - theatre_n - inv)
    )
    for profession, count in (
        (PROFESSION_POWER_CRANK, crank),
        (PROFESSION_FARMING, farm),
        (PROFESSION_RAT_TRAPPING, rat_n),
        (PROFESSION_THEATRE, theatre_n),
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
    theatre_draw_per_second: float,
    tick_time: datetime,
) -> None:
    power_draw = (
        lights_power_draw_per_second if lights_on else 0.0
    ) + max(0.0, theatre_draw_per_second)
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


def _fireside_broadcast_overlap_fraction(
    elapsed_seconds: float,
    tick_time: datetime,
    broadcast_end: datetime,
    broadcast_duration_seconds: float,
) -> float:
    """Fraction of the Fireside window overlapped by this tick's simulation slice."""
    dur = max(1e-9, float(broadcast_duration_seconds))
    broadcast_start = broadcast_end - timedelta(seconds=int(broadcast_duration_seconds))
    t_prev = tick_time - timedelta(seconds=max(0.0, float(elapsed_seconds)))
    seg_lo = max(broadcast_start, t_prev)
    seg_hi = min(tick_time, broadcast_end)
    if seg_hi <= seg_lo:
        return 0.0
    return max(0.0, min(1.0, (seg_hi - seg_lo).total_seconds() / dur))


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

        bad_apple_frame_idx = advance_bad_apple_frame_index()

        processed_user_count = 0
        gamestate_snapshots: list[dict[str, object]] = []
        gs_interval = float(constants.GAMESTATE_LOG_INTERVAL_SECONDS)
        for user in users:
            user_id = user.id

            complete_due_movie_screenings_for_user(user_id, tick_time)

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
            exhaust_sum = decay_player_movie_exhaustion(
                user_id, elapsed_seconds, tick_time
            )

            usr = readings.user
            sermon_loyalty_bonus = 0.0
            sermon_boredom_relief = 0.0
            pending_fireside_completion_kind: str | None = None
            fireside_loyalty_tick = 0.0
            fireside_frank_doubt_tick = 0.0
            if (
                usr.sermon_reward_pending
                and usr.sermon_busy_until is not None
                and tick_time >= usr.sermon_busy_until
            ):
                sermon_loyalty_bonus = float(constants.SERMON_COMPLETION_LOYALTY_GAIN)
                sermon_boredom_relief = float(constants.SERMON_COMPLETION_BOREDOM_RELIEF)
                usr.sermon_reward_pending = False
                usr.sermon_busy_until = None

            bu_fs = usr.fireside_busy_until
            kind_fs = usr.fireside_pending_kind
            dur_fs = float(constants.FIRESIDE_CHAT_DURATION_SECONDS)
            if bu_fs is not None:
                completing_fs = tick_time >= bu_fs
                if kind_fs:
                    frac_delta = _fireside_broadcast_overlap_fraction(
                        elapsed_seconds, tick_time, bu_fs, dur_fs
                    )
                    loyalty_total = 0.0
                    frank_doubt_total = 0.0
                    if kind_fs == constants.FIRESIDE_KIND_REASSURING:
                        loyalty_total = float(constants.FIRESIDE_REASSURING_LOYALTY_DELTA)
                    elif kind_fs == constants.FIRESIDE_KIND_FRANK:
                        loyalty_total = float(constants.FIRESIDE_FRANK_LOYALTY_DELTA)
                        frank_doubt_total = float(constants.FIRESIDE_FRANK_DOUBT_DELTA)
                    elif kind_fs == constants.FIRESIDE_KIND_FEARMONGERING:
                        loyalty_total = float(constants.FIRESIDE_FEARMONGER_LOYALTY_DELTA)

                    fireside_loyalty_tick += loyalty_total * frac_delta
                    fireside_frank_doubt_tick += frank_doubt_total * frac_delta
                    new_accrued = float(usr.fireside_effect_fraction_accrued) + frac_delta

                    if completing_fs:
                        rem = max(0.0, 1.0 - new_accrued)
                        fireside_loyalty_tick += loyalty_total * rem
                        fireside_frank_doubt_tick += frank_doubt_total * rem
                        pending_fireside_completion_kind = kind_fs
                        usr.fireside_effect_fraction_accrued = 0.0
                    else:
                        usr.fireside_effect_fraction_accrued = min(new_accrued, 1.0)

                if completing_fs:
                    usr.fireside_busy_until = None
                    usr.fireside_pending_kind = None
                    if not kind_fs:
                        usr.fireside_effect_fraction_accrued = 0.0
                    halt_geiger_rumor_exodus(user_id)

            new_boredom, new_doubt, boredom_row = handle_boredom_and_doubt_tick(
                user_id,
                readings.latest_boredom_sample,
                readings.latest_doubt_sample,
                new_radiation_truth,
                elapsed_seconds,
                boredom_per_second,
                doubt_growth_max_per_second,
                initial_radiation,
                tick_time,
                boredom_relief=sermon_boredom_relief,
            )

            working_doubt = float(new_doubt)
            if fireside_frank_doubt_tick > 1e-12:
                working_doubt = min(100.0, working_doubt + fireside_frank_doubt_tick)
                db.session.add(
                    BunkerDoubt(user_id=user_id, doubt=working_doubt, timestamp=tick_time)
                )

            if pending_fireside_completion_kind == constants.FIRESIDE_KIND_FEARMONGERING:
                if random.random() < float(constants.FIRESIDE_FEARMONGER_BACKFIRE_CHANCE):
                    enqueue_fireside_rhetoric_backlash(user_id, tick_time)
                else:
                    working_doubt = min(
                        100.0,
                        working_doubt
                        + float(constants.FIRESIDE_FEARMONGER_DOUBT_DELTA_SOFT),
                    )
                    db.session.add(
                        BunkerDoubt(user_id=user_id, doubt=working_doubt, timestamp=tick_time)
                    )

            working_doubt, inner_circle_loyalty_bonus = inner_circle.complete_due_tasks(
                user_id, tick_time, working_doubt
            )

            if (
                not usr.geiger_rumor_crisis_triggered
                and new_radiation_truth < working_doubt
            ):
                enqueue_geiger_rumor_exodus(
                    user_id,
                    tick_time,
                    readings.latest_population_sample.count,
                )

            adjusted_loyalty = handle_loyalty_change(
                readings.latest_loyalty_sample,
                _crank_worker_count(readings),
                crank_workers_loyalty_threshold,
                loyalty_penalty_per_excess_crank_worker,
            )
            boredom_drag_amt = boredom_loyalty_drag(new_boredom, elapsed_seconds)
            movie_drag_amt = movie_exhaustion_loyalty_drag(exhaust_sum, elapsed_seconds)
            base_pop_loyalty = max(
                0.0,
                adjusted_loyalty - boredom_drag_amt - movie_drag_amt,
            )
            adjusted_loyalty = base_pop_loyalty

            had_prior_departure_event = user_had_prior_departure_event(user_id)
            had_prior_welcome_message = user_had_prior_welcome_message(user_id)

            rumor_chunk = geiger_rumor_forced_departures_this_tick(user_id, tick_time)
            departed_this_tick = handle_population_departures(
                user_id,
                readings.latest_radiation_level,
                readings.latest_population_sample,
                adjusted_loyalty,
                radiation_safe_threshold,
                base_departure_rate,
                tick_time,
                forced_extra_departures=rumor_chunk,
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
            theatre_line = (
                readings.theatre.profession_line
                if readings.theatre is not None
                else None
            )
            theatre_draw_ps = 0.0
            theatre_loyalty_bonus = 0.0
            theatre_boredom_bonus = 0.0
            basket_weaving_loyalty_bonus = 0.0
            if _facilities_ready(readings):
                normalize_worker_assignments(
                    crank_line,
                    farm_line,
                    rat_line,
                    theatre_line,
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
                if readings.theatre is not None:
                    en_lvl = (
                        readings.latest_energy_reserve.level
                        if readings.latest_energy_reserve is not None
                        else 0.0
                    )
                    theatre_draw_ps, theatre_loyalty_bonus, theatre_boredom_bonus = (
                        handle_theatre_tick(
                            readings.theatre,
                            _theatre_worker_count(readings),
                            tick_time,
                            en_lvl,
                            elapsed_seconds,
                        )
                    )
                if theatre_boredom_bonus > 1e-9:
                    boredom_row.boredom = max(
                        0.0,
                        float(boredom_row.boredom) - theatre_boredom_bonus,
                    )
            social_bw = db.session.get(BunkerSocialState, user_id)
            if social_bw is not None:
                social_bw.basket_weaving_hours = constants.basket_weaving_hours_clamped(
                    social_bw.basket_weaving_hours
                )
                if post_pop > 0:
                    bh = social_bw.basket_weaving_hours
                    basket_weaving_loyalty_bonus = (
                        constants.basket_weaving_loyalty_per_second(bh) * elapsed_seconds
                    )
                    cash_tick = (
                        constants.basket_weaving_cash_per_second(bh, post_pop)
                        * elapsed_seconds
                    )
                    social_bw.inner_circle_cash += cash_tick
            record_bunker_profession_snapshots(
                user_id, post_pop, readings, tick_time
            )
            record_environment_pixel_noise_sample(user_id, tick_time, bad_apple_frame_idx)
            record_social_movie_pixel_sample(user_id, tick_time)

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
                    theatre_draw_ps,
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

            final_loyalty = max(
                0.0,
                min(
                    100.0,
                    adjusted_loyalty
                    + auto_loyalty_adj
                    + sermon_loyalty_bonus
                    + theatre_loyalty_bonus
                    + basket_weaving_loyalty_bonus
                    + fireside_loyalty_tick
                    + inner_circle_loyalty_bonus,
                ),
            )
            record_loyalty_sample(user_id, final_loyalty, tick_time)
            inner_circle.tick_member_psyche(
                user_id,
                final_loyalty,
                working_doubt,
                elapsed_seconds,
                tick_time,
            )
            inner_circle.sync_aggregate_inner_circle_loyalty(user_id)
            inner_circle.record_cash_sample(user_id, tick_time)

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
                channel=constants.MESSAGE_CHANNEL_BULLETIN,
            ))
        db.session.commit()
        log.debug("posted test message to %d user(s)", len(users))
