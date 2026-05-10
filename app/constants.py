"""Game tuning — single source of truth. Import ``app.constants`` in routes, jobs, etc.

Infrastructure (DB URL, secrets, Grafana base URL) lives in ``app.config.Config``.
Random subsystem events are defined as ``GameEventSpec`` rows in ``app.events``
(callable spawn gates and outcomes plus numeric odds/durations/multipliers).
"""

from __future__ import annotations

from dataclasses import dataclass

# --- Game systems (Grafana subsystem dashboards) ---
GAME_SYSTEM_ENVIRONMENT = "environment"
GAME_SYSTEM_POWER = "power"
GAME_SYSTEM_FARMING = "farming"
GAME_SYSTEM_SOCIAL = "social"

GAME_SYSTEM_IDS: frozenset[str] = frozenset(
    {
        GAME_SYSTEM_ENVIRONMENT,
        GAME_SYSTEM_POWER,
        GAME_SYSTEM_FARMING,
        GAME_SYSTEM_SOCIAL,
    }
)

GAME_SYSTEM_LABELS: dict[str, str] = {
    GAME_SYSTEM_ENVIRONMENT: "Environment sensors",
    GAME_SYSTEM_POWER: "Power systems",
    GAME_SYSTEM_FARMING: "Farming & silos",
    GAME_SYSTEM_SOCIAL: "Social programs",
}


def normalize_game_system_id(raw: str | None) -> str | None:
    """Return canonical subsystem id, or ``None`` if unknown."""
    if not raw:
        return None
    s = raw.strip().lower()
    return s if s in GAME_SYSTEM_IDS else None


@dataclass(frozen=True)
class InvestigationDispatchConfig:
    """Residents and timer for a routine sweep of one bunker subsystem."""

    team_size: int
    duration_seconds: int


# Tunables per subsystem (not tied to random events).
INVESTIGATION_DISPATCH_BY_SYSTEM: dict[str, InvestigationDispatchConfig] = {
    GAME_SYSTEM_ENVIRONMENT: InvestigationDispatchConfig(team_size=5, duration_seconds=10),
    GAME_SYSTEM_POWER: InvestigationDispatchConfig(team_size=5, duration_seconds=10),
    GAME_SYSTEM_FARMING: InvestigationDispatchConfig(team_size=5, duration_seconds=10),
    GAME_SYSTEM_SOCIAL: InvestigationDispatchConfig(team_size=5, duration_seconds=10),
}


# --- Radiation ---
# Half-life of 600s with a 30s tick gives a visible decay curve over the
# course of a play session while still feeling like an environmental
# measurement rather than a countdown.
INITIAL_RADIATION = 100.0
# Uniform jitter ± this many rads on ``level_display`` each sample (truth stays in ``level``).
RADIATION_DISPLAY_NOISE_MAX = 10.0
DECAY_TICK_SECONDS = 1
DECAY_HALF_LIFE_SECONDS = 600

# --- Population & loyalty ---
INITIAL_POPULATION = 100
# 0 = everyone wants out; 100 = unquestioning faith in the bunker.
INITIAL_LOYALTY = 100.0

# Below this radiation reading (rads), people can decide to leave.
# At 100-rad start with a 600s half-life, this threshold is crossed around
# the 10-minute mark of a session — giving the player some breathing room
# before the population clock starts ticking.
RADIATION_SAFE_THRESHOLD = 50.0

# Each tick, each unit of "effective disloyalty" contributes this fraction
# of the population as departures:
#   departures = round(population × (1 − loyalty/100) × rate)
# With defaults: round(50 × 0.25 × 0.05) = 1 person per tick once the
# threshold is crossed. Loyalty 100 → 0 departures regardless.
BASE_DEPARTURE_RATE = 0.05

# --- Energy ---
INITIAL_ENERGY = 100.0

# Power draw of the lights system in energy/second. Other systems will
# follow the same pattern when added (HVAC_POWER_DRAW, etc.).
LIGHTS_POWER_DRAW = 0.01

# Each worker assigned to the crank generates this much energy per second.
# At 0.002/s, 5 workers exactly offsets the lights; 10 workers generate
# double the lights draw, giving headroom for future systems.
CRANK_POWER_PER_WORKER = 0.002

# Workers above this count start reducing loyalty each tick.
CRANK_WORKERS_LOYALTY_THRESHOLD = 10

# Loyalty lost per tick per worker above the threshold.
# With defaults: 11 workers → -0.5/tick; 20 workers → -5/tick.
CRANK_WORKERS_LOYALTY_PENALTY = 0.5

# Energy added by a single manual crank button press.
MANUAL_CRANK_ENERGY = 1.0

# --- Farming ---
INITIAL_FOOD = 10000.0
INITIAL_FARM_WORKERS = 10
FOOD_PER_CAPITA_PER_SECOND = 0.01
# Farm workers no longer add passive food each tick; they only affect per-plot harvest size.
FOOD_PER_WORKER_PER_SECOND = 0.0
# Continuous trapper output (/sec) = trapper_count × this × combined_rat_pressure_ps.
# Combined pressure = fluctuating silo rat drain + swarm spike marginal while ``rats_silo`` is active.
RAT_TRAPPER_PRODUCTION_PER_TRAP_PRESSURE_UNIT = 0.1
# After ``rats_silo_intro``: resident rats add ongoing drain (food units/sec), drifted each tick.
RAT_BACKGROUND_INITIAL_DRAIN_PS = 0.1
# Uniform random drift amplitude per second of tick elapsed (scaled below ~3 s catch-up).
RAT_BACKGROUND_DRIFT_STEP_PS = 0.006
RAT_BACKGROUND_DRAIN_MIN_PS = 0.004
RAT_BACKGROUND_DRAIN_MAX_PS = 0.055
FARM_PLANT_GROWTH_SECONDS = 300
# Harvest food when average assigned farm workers during the growth window equals this reference.
FARM_HARVEST_YIELD = 50.0
FARM_HARVEST_YIELD_REF_AVG_WORKERS = 10.0
# Independent hydroponic bays on the farming dashboard (columns × rows).
FARM_PLOT_GRID_COLUMNS = 2
FARM_PLOT_GRID_ROWS = 2
FARM_PLOT_COUNT = FARM_PLOT_GRID_COLUMNS * FARM_PLOT_GRID_ROWS

# Environment dashboard: 48×48 “pixel display” (see jobs). Horizontal axis is **time**:
# ``ENVIRONMENT_PIXEL_TIME_COLUMNS`` distinct timestamps map to heatmap columns; each row stores
# one vertical strip (``ENVIRONMENT_PIXEL_GRID_ROWS`` values). ``ENVIRONMENT_PIXEL_GRID_COLS`` is
# the spatial width of the generated frame before slicing columns onto timestamps (typically 48).
ENVIRONMENT_PIXEL_GRID_COLS = 48
ENVIRONMENT_PIXEL_GRID_ROWS = 48
ENVIRONMENT_PIXEL_TIME_COLUMNS = 48
# Synthetic history span (seconds) from oldest strip to ``tick_time``.
# Keep ``heatmap_history_seconds`` on the Environment Grafana dashboard equal to this value.
ENVIRONMENT_PIXEL_BACKFILL_SPAN_SECONDS = 15 * 60
ENVIRONMENT_PIXEL_BACKFILL_SAMPLES = ENVIRONMENT_PIXEL_TIME_COLUMNS
# Uniform noise overlaid each tick on ``environment_pixel_reference.png`` luminance (clamped to [0, 1]).
# Half-range per pixel; e.g. 0.06 adds +/-0.06 independent jitter while keeping the image readable.
ENVIRONMENT_PIXEL_REFERENCE_TICK_NOISE_HALF_RANGE = 0.06
# Bad Apple clip: PNG sequence under ``app/assets/images/bad_apple`` (``frame_00.png`` …).
# When all frames exist, the heatmap uses one frame per scheduler tick (see ``game_tick``).
BAD_APPLE_FRAME_COUNT = 20

# Social dashboard theatre heatmap: same strip geometry as environment pixels.
# Keep ``social_movie_heatmap_history_seconds`` on the Social Grafana dashboard equal to
# ``SOCIAL_MOVIE_PIXEL_BACKFILL_SPAN_SECONDS``.
SOCIAL_MOVIE_PIXEL_GRID_COLS = ENVIRONMENT_PIXEL_GRID_COLS
SOCIAL_MOVIE_PIXEL_GRID_ROWS = ENVIRONMENT_PIXEL_GRID_ROWS
SOCIAL_MOVIE_PIXEL_BACKFILL_SPAN_SECONDS = ENVIRONMENT_PIXEL_BACKFILL_SPAN_SECONDS
SOCIAL_MOVIE_PIXEL_BACKFILL_SAMPLES = ENVIRONMENT_PIXEL_BACKFILL_SAMPLES
# PNG sequences under ``app/assets/images/{atomic_cafe,barts_comet,day_after,mad_max}/`` (see ``movie_pixel_frames``).
SOCIAL_MOVIE_PIXEL_SEQUENCE_FRAME_COUNT = 60
# Synthetic ``movie_id`` written when no screening is active; Grafana heatmap COALESCE fallback.
SOCIAL_MOVIE_PIXEL_IDLE_HEATMAP_MOVIE_ID = "__idle_noise__"


def harvest_yield_from_avg_farm_workers(avg_workers: float) -> float:
    """Food from one harvest given mean farm-worker headcount over that crop's growth window."""
    ref = FARM_HARVEST_YIELD_REF_AVG_WORKERS
    if ref <= 0:
        return 0.0
    return max(0.0, FARM_HARVEST_YIELD * (float(avg_workers) / ref))


# Periodic INFO summaries from ``game_tick`` (seconds between logs); 0 = off.
GAMESTATE_LOG_INTERVAL_SECONDS = 10

# --- Social (boredom / doubt / inner circle) ---
INITIAL_BOREDOM = 0.0
INITIAL_DOUBT = 0.0
# Hidden stat: trust among the inner council (0–100).
INITIAL_INNER_CIRCLE_LOYALTY = 50

# Boredom rises slowly while nothing entertains the population.
BOREDOM_PER_SECOND = 0.02

# Loyalty lost per second when boredom is at 100 (scaled linearly: boredom/100).
BOREDOM_LOYALTY_DRAIN_PER_SECOND_AT_FULL = 0.08

# Doubt rises faster when outdoor truth radiation is far below session start (100 rads).
DOUBT_GROWTH_MAX_PER_SECOND = 0.025

SOCIAL_MOVIE_COOLDOWN_SECONDS = 300
SOCIAL_SPEECH_COOLDOWN_SECONDS = 300
SOCIAL_COUNCIL_COOLDOWN_SECONDS = 600

# Per-title screenings: boredom relief scales down via 1/(1 + k * that_title's completed count).
SOCIAL_MOVIE_DIMINISH_K = 0.45
SOCIAL_SPEECH_LOYALTY_GAIN_BASE = 10.0
SOCIAL_SPEECH_DOUBT_RELIEF_BASE = 12.0
SOCIAL_SPEECH_DIMINISH_K = 0.5

# --- Movies (catalog below) ---
# Runtime of one screening; boredom relief and exhaustion apply when this elapses (see ``game_tick``).
MOVIE_SCREENING_DURATION_SECONDS = 60
# Linear decay of exhaustion units per second (applied per title row in ``player_movie_exhaustion``).
MOVIE_EXHAUSTION_DECAY_PER_SECOND = 0.04
# Extra loyalty drain per second when combined movie exhaustion reaches 100 (no drain below 50 combined).
MOVIE_EXHAUSTION_LOYALTY_DRAIN_PER_SECOND_AT_FULL = 0.06
# Fatigue added per title when any screening completes (same for every catalog movie).
MOVIE_EXHAUSTION_GAIN_PER_PLAY = 18.0

# --- Sermon (global action lock) ---
SERMON_DURATION_SECONDS = 120
SERMON_COMPLETION_LOYALTY_GAIN = 15.0
SERMON_COMPLETION_BOREDOM_RELIEF = 25.0

# --- Theatre ---
THEATRE_WRITE_SECONDS = 180
THEATRE_REHEARSE_SECONDS = 180
THEATRE_PERFORMANCE_INTERVAL_SECONDS = 120
THEATRE_LOYALTY_PER_PERFORMANCE = 8.0
# Continuous draw (energy/s) per actor while any theatre programme is active (actors > 0).
THEATRE_POWER_DRAW_PER_ACTOR = 0.003

THEATRE_PHASE_IDLE = "idle"
THEATRE_PHASE_WRITING = "writing"
THEATRE_PHASE_REHEARSING = "rehearsing"
THEATRE_PHASE_READY = "ready"


@dataclass(frozen=True)
class MovieSpec:
    """Fixed catalog entry for bunker screenings."""

    id: str
    title: str
    energy_cost: float
    boredom_relief_base: float
    doubt_relief_base: float


MOVIES: tuple[MovieSpec, ...] = (
    MovieSpec(
        id="atomic_cafe",
        title="The Atomic Cafe",
        energy_cost=4.0,
        boredom_relief_base=11.0,
        doubt_relief_base=38.0,
    ),
    MovieSpec(
        id="the_day_after",
        title="The Day After",
        energy_cost=4.0,
        boredom_relief_base=21.0,
        doubt_relief_base=30.0,
    ),
    MovieSpec(
        id="mad_max",
        title="Mad Max",
        energy_cost=7.5,
        boredom_relief_base=32.0,
        doubt_relief_base=17.0,
    ),
    MovieSpec(
        id="simpsons_s06e14_barts_comet",
        title='Simpsons S6E14 "Bart\'s Comet"',
        energy_cost=1.2,
        boredom_relief_base=9.0,
        doubt_relief_base=0.0,
    ),
)

MOVIES_BY_ID: dict[str, MovieSpec] = {m.id: m for m in MOVIES}
