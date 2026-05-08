"""Game tuning — single source of truth (not environment-driven)."""

from __future__ import annotations

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
INITIAL_FOOD = 1000.0
INITIAL_FARM_WORKERS = 10
FOOD_PER_CAPITA_PER_SECOND = 0.1
FOOD_PER_WORKER_PER_SECOND = 1
FARM_PLANT_GROWTH_SECONDS = 300
FARM_HARVEST_YIELD = 50.0

# Periodic INFO summaries from ``game_tick`` (seconds between logs); 0 = off.
GAMESTATE_LOG_INTERVAL_SECONDS = 10

# --- Social (boredom / doubt / inner circle) ---
INITIAL_BOREDOM = 0.0
INITIAL_DOUBT = 0.0
# Hidden stat: trust among the inner council (0–100).
INITIAL_INNER_CIRCLE_LOYALTY = 50

# Boredom rises slowly while nothing entertains the population.
BOREDOM_PER_SECOND = 0.02

# Doubt rises faster when outdoor truth radiation is far below session start (100 rads).
DOUBT_GROWTH_MAX_PER_SECOND = 0.025

SOCIAL_MOVIE_COOLDOWN_SECONDS = 300
SOCIAL_SPEECH_COOLDOWN_SECONDS = 300
SOCIAL_COUNCIL_COOLDOWN_SECONDS = 600

# First movie / speech use full effect; later uses scale down via 1/(1 + k * use_count).
SOCIAL_MOVIE_BOREDOM_RELIEF_BASE = 18.0
SOCIAL_MOVIE_DIMINISH_K = 0.45
SOCIAL_SPEECH_LOYALTY_GAIN_BASE = 10.0
SOCIAL_SPEECH_DOUBT_RELIEF_BASE = 12.0
SOCIAL_SPEECH_DIMINISH_K = 0.5
