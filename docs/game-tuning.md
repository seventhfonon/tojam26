# Game tuning reference

Authoritative numbers live in [`app/constants.py`](../app/constants.py). Random gameplay events add tunables in [`app/events.py`](../app/events.py) on each `GameEventSpec`. Simulation math is mostly in [`app/jobs.py`](../app/jobs.py) (`game_tick` and helpers) and [`app/inner_circle.py`](../app/inner_circle.py).

## Where values apply

- **`game_tick`** runs on an interval of **`DECAY_TICK_SECONDS` (1 s)**; each step scales effects by **wall-clock `elapsed_seconds`** since the last radiation sample (can exceed 1 s if the server catches up).
- **Gating rule:** radiation threshold checks for **population departures** use the **pre-tick** radiation reading (`readings.latest_radiation_level`), not the newly decayed value written in the same tick. Doubt growth uses **`new_radiation_truth`** after decay for that tick.

## Systems and subsystem IDs

| Constant | Starting / fixed value | Role |
|----------|------------------------|------|
| `GAME_SYSTEM_*` ids | `environment`, `power`, `farming`, `social` | Labels map to Grafana-facing names. |
| `INVESTIGATION_DISPATCH_BY_SYSTEM` | team **5**, duration **10 s** (all four systems) | Routine sweep: pulls idle workers to Investigation for `duration_seconds`. |

## Radiation

| Constant | Value | Dynamics and links |
|----------|-------|-------------------|
| `INITIAL_RADIATION` | **100** | Session baseline for doubt scaling. |
| `DECAY_HALF_LIFE_SECONDS` | **600** | Truth decay: `truth *= 0.5^(Δt / half_life)`. |
| `RADIATION_DISPLAY_NOISE_MAX` | **10** | Display jitter half-range scales **linearly** with truth vs `INITIAL_RADIATION`: `noise_half_range = NOISE_MAX × truth / INITIAL_RADIATION`. |
| `RADIATION_SAFE_THRESHOLD` | **50** | If **pre-tick** truth **&lt; 50**, normal emigration can run (see departures). |

## Population, loyalty, departures

| Constant | Value | Dynamics and links |
|----------|-------|-------------------|
| `INITIAL_POPULATION` | **100** | — |
| `INITIAL_LOYALTY` | **100** | Crank overwork can reduce **effective** loyalty before departures (see power). |
| `BASE_DEPARTURE_RATE` | **0.05** | When safe threshold fails: `round(pop × (1 − loyalty/100) × rate)`; then add **forced** rumor exits (below). |
| `GEIGER_RUMOR_CRISIS_DURATION_SECONDS` | **60** | One-shot crisis window. |
| `GEIGER_RUMOR_EMIGRATION_FRACTION` | **0.2** | Quota `max(1, round(pop × 0.2))`; exits spread **linearly** over the 60 s (`geiger_rumor_forced_departures_this_tick`). |

**Trigger:** First time **`new_radiation_truth < working_doubt`** (after doubt updates / Frank fireside / Inner Circle tasks): enqueue crisis; clears `social.last_fireside_chat_at`. Completing **Fireside** calls **`halt_geiger_rumor_exodus`**.

## Power and energy

| Constant | Value | Dynamics and links |
|----------|-------|-------------------|
| `INITIAL_ENERGY` | **100** | — |
| `LIGHTS_POWER_DRAW` | **0.01**/s | Draw when lights on. |
| `CRANK_POWER_PER_WORKER` | **0.002**/worker/s | Generation; **5 workers ≈ lights**. |
| `MANUAL_CRANK_ENERGY` | **1.0** | Per manual crank button press (routes). |
| `CRANK_WORKERS_LOYALTY_THRESHOLD` | **10** | Above this, crank workers subtract loyalty **per worker over threshold**: `excess × CRANK_WORKERS_LOYALTY_PENALTY` (**0.5** each). That adjusted loyalty feeds **boredom/movie drags**, then **departures**. |
| `THEATRE_POWER_DRAW_PER_ACTOR` | **0.003**/s × actors | Added to lights when theatre active; theatre progression stalls if energy **`≤ 0`** (`handle_theatre_tick`). |

Net energy per tick: `(crank_workers × crank_rate − lights − theatre_draw) × Δt`.

## Farming and rats

| Constant | Value | Dynamics and links |
|----------|-------|-------------------|
| `INITIAL_FOOD` | **10000** | Default if no reserve row. |
| `INITIAL_FARM_WORKERS` | **10** | Seed only. |
| `FOOD_PER_CAPITA_PER_SECOND` | **0.01** | Human consumption × **`food_consumption_multiplier`** from active events (product of all active specs). |
| `FOOD_PER_WORKER_PER_SECOND` | **0** | Passive farm worker food line is off; harvest uses growth window + `FARM_HARVEST_YIELD` (**50**) scaled by mean workers / **`FARM_HARVEST_YIELD_REF_AVG_WORKERS` (10)** via `harvest_yield_from_avg_farm_workers`. |
| `FARM_PLANT_GROWTH_SECONDS` | **300** | Crop timer. |
| `RAT_TRAPPER_PRODUCTION_PER_TRAP_PRESSURE_UNIT` | **0.1** | Production/s = **trapper_count × 0.1 × combined_rat_pressure** (background drain + swarm marginal spike when `rats_silo` active). |
| Rat background | initial **0.1**/s, drift step **0.006** × scaled elapsed (cap **3** s), clamp **[0.004, 0.055]** | After silo rats intro; swarm spawn suppressed if trapper output covers marginal spike (`events.py`). |

Food net per tick:

`Δfood = (farm_workers × 0 + trapper_production − (pop × per_capita × food_mult + rat_background)) × Δt`.

### Registered events (`events.py`)

- **`rats_silo_intro`:** spawn chance **0.01**/tick, gates on food ≥ **12**, pop ≥ **8**; sets `rat_background_consumption_ps` to **0.1**.
- **`rats_silo`:** spawn **0.008**, duration **60 s**, **`food_consumption_multiplier = 3`**, gates include food ≥ **15**, pop ≥ **10**, silo introduced.

## Social: boredom, doubt, movies, theatre, basket weaving, sermon, fireside

| Constant | Value | Dynamics and links |
|----------|-------|-------------------|
| `INITIAL_BOREDOM` / `INITIAL_DOUBT` | **0** | — |
| `BOREDOM_PER_SECOND` | **0.02** | Boredom rises, capped **100**; relief subtracts after (sermon, screening completion, theatre `ready`, etc.). |
| `BOREDOM_LOYALTY_DRAIN_PER_SECOND_AT_FULL` | **0.08** | Loyalty drain × `(boredom/100) × Δt`. |
| `DOUBT_GROWTH_MAX_PER_SECOND` | **0.025** | Doubt increase × `(1 − new_truth/INITIAL_RADIATION)` × Δt (caps **100**). |
| `SOCIAL_MOVIE_DIMINISH_K` | **0.45** | On screening complete: boredom relief `base/(1 + k×screenings_completed)`, same for doubt relief base off **`MOVIES`** catalog. |
| `MOVIE_SCREENING_DURATION_SECONDS` | **60** | Timer before completion effects. |
| `MOVIE_EXHAUSTION_*` | decay **0.04**/s per title; loyalty drain when sum **≥ 50**: scale `(exhaustion−50)/50 × 0.06 × Δt`; **+18** exhaustion per play | Interacts with boredom loyalty drag. |
| Theatre phases | write/rehearse **60** s each | Loyalty **0.12**/s whenever progressing; boredom relief **0.25**/s only in **`ready`**; cycles plays via `THEATRE_PLAY_TITLES`. |
| `SERMON_DURATION_SECONDS` | **120** | On completion: **`SERMON_COMPLETION_LOYALTY_GAIN` 15**, **`SERMON_COMPLETION_BOREDOM_RELIEF` 25**. |
| `SOCIAL_MOVIE_COOLDOWN_SECONDS` / `SOCIAL_SPEECH_COOLDOWN_SECONDS` | **300** | Route cooldowns. |
| `SOCIAL_SPEECH_*` | gain base **10**, doubt relief **12**, **`SOCIAL_SPEECH_DIMINISH_K` 0.5** | Instant gains divided by `1 + k×uses` (routes). |
| `SOCIAL_COUNCIL_COOLDOWN_SECONDS` | **600** | Cooldown. |
| Fireside (`FIRESIDE_*`) | duration **30** s; reassuring **+4.5** loyalty; frank **+7.5** loyalty **+6** doubt; fearmonger **+13** loyalty; on complete fearmonger **38%** backlash event OR **+2.75** doubt | Loyalty/doubt applied proportional to **overlap fraction** of tick with broadcast window; completing Fireside **stops** Geiger rumor exodus. Backlash adds **`FIRESIDE_BACKLASH_DOUBT_DELTA` 24** over **`FIRESIDE_RHETORIC_BACKLASH_DURATION_SECONDS` 78**. |
| Basket weaving | hours **0–4**; loyalty/sec `(0, 0.09, 0.065, 0.045, 0.028)`; cash/person/s `(0, 0.001…0.004)` | Loyalty added to final bunker loyalty; cash **`× post-departure population × Δt`** to `inner_circle_cash`. |

The **`MOVIES`** catalog entries each set **`energy_cost`**, **`boredom_relief_base`**, **`doubt_relief_base`** (see `constants.py`).

## Inner Circle

Per-member seeds: five names with loyalty **58–62**, popularity **48–55**, frustration **34–40**, disposition **52–76**.

| Constant | Value | Dynamics and links |
|----------|-------|-------------------|
| Pressure | `0.55×(100−bunker_loyalty) + 0.45×doubt` | Drives frustration toward pressure at **`INNER_CIRCLE_FRUSTRATION_DRIFT_PER_SECOND` 0.065**. |
| Unpopular | **0.004** per point below **50** popularity | Extra frustration/s. |
| Loyalty drift | **0.04**/s toward `100−frustration`, scaled by disposition via **`INNER_CIRCLE_DISPOSITION_LOYALTY_SLOW_MIN` 0.22** | Members skip drift while task busy. |
| Tasks | Stage incident, buy groceries, temp job, grant luxuries | Durations and deltas as in `constants.py` (food/energy/cash costs, RNG doubt bumps, etc.). |

Aggregate **`inner_circle_loyalty`** = rounded mean member loyalty.

## Display / Grafana-only

Environment pixel grid **48×48**, backfill **15 min** / **48** samples, reference noise half-range **0.06**, **`BAD_APPLE_FRAME_COUNT` 20**. Social movie pixel stack mirrors span; sequence **60** frames; idle heatmap id **`__idle_noise__`**. These are primarily visual; see [`heatmap_display.md`](heatmap_display.md) where applicable.

## Logging

| Constant | Value |
|----------|-------|
| `GAMESTATE_LOG_INTERVAL_SECONDS` | **10** (0 = off) |

## Related docs

- [`events.md`](events.md) — random event registry and spawn behavior.
- [`README.md`](../README.md) — notes that tuning lives in `constants.py` and events in `events.py`.
