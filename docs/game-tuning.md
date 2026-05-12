# Game tuning reference

Authoritative numbers live in [`app/constants.py`](../app/constants.py). Random gameplay events add tunables in [`app/events.py`](../app/events.py) on each `GameEventSpec`. Simulation math is mostly in [`app/jobs.py`](../app/jobs.py) (`game_tick` and helpers) and [`app/inner_circle.py`](../app/inner_circle.py). Focus Tree nodes and hooks are in [`app/focus_tree.py`](../app/focus_tree.py).

---

## Where values apply

- **`game_tick`** runs on an interval of **`DECAY_TICK_SECONDS` (1 s)**; each step scales effects by **wall-clock `elapsed_seconds`** since the last radiation sample (can exceed 1 s if the server catches up).
- **Gating rule:** radiation threshold checks for **population departures** use the **pre-tick** radiation reading (`readings.latest_radiation_level`), not the newly decayed value written in the same tick. Doubt growth uses **`new_radiation_truth`** after decay for that tick.

---

## Systems and subsystem IDs

| Constant | Starting / fixed value | Role |
|----------|------------------------|------|
| `GAME_SYSTEM_*` ids | `environment`, `power`, `farming`, `social` | Labels map to Grafana-facing names. |
| `INVESTIGATION_DISPATCH_BY_SYSTEM` | team **5**, duration **10 s** (all four systems) | Routine sweep: pulls idle workers to Investigation for `duration_seconds`. |

---

## Radiation

| Constant | Value | Dynamics and links |
|----------|-------|-------------------|
| `INITIAL_RADIATION` | **100** | Session baseline for doubt scaling. |
| `DECAY_HALF_LIFE_SECONDS` | **120** | Truth decay: `truth *= 0.5^(Δt / half_life)`. At 120 s half-life, truth crosses 50 rads (~`RADIATION_SAFE_THRESHOLD`) around the 2-minute mark. |
| `RADIATION_DISPLAY_NOISE_MAX` | **10** | Display jitter half-range scales **linearly** with truth vs `INITIAL_RADIATION`: `noise_half_range = NOISE_MAX × truth / INITIAL_RADIATION`. |
| `RADIATION_SAFE_THRESHOLD` | **50** | If **pre-tick** truth **< 50**, normal emigration can run (see Departures). |

---

## Population, loyalty, departures

| Constant | Value | Dynamics and links |
|----------|-------|-------------------|
| `INITIAL_POPULATION` | **100** | — |
| `INITIAL_LOYALTY` | **100** | Starting morale. |
| `BASE_DEPARTURE_RATE` | **0.05** | When safe threshold is crossed: `round(pop × (1 − loyalty/100) × rate)`; loyalty 100 → 0 departures regardless. |

### Loyalty compositing (each tick)

Loyalty written to DB each tick is assembled in this order:

1. **Crank overwork penalty** (applied before departures so it takes effect immediately).
2. **Boredom drag**: `(boredom / 100) × BOREDOM_LOYALTY_DRAIN_PER_SECOND_AT_FULL × Δt`.
3. **Movie exhaustion drag** (see Movies): no drag until combined exhaustion ≥ 50.
4. **Auto-resolve delta** from any event that cleared this tick.
5. **Sermon bonus** on the tick the sermon window closes.
6. **Theatre loyalty accrual** (writing + rehearsing + ready phases).
7. **Basket-weaving loyalty accrual**.
8. **Fireside loyalty** (proportional to tick overlap with broadcast window; topped up to 100 % on completion).
9. **Inner Circle task completion bonus** (`stage_incident` success: +2 bunker loyalty).

### Geiger rumor exodus

| Constant | Value | Notes |
|----------|-------|-------|
| `GEIGER_RUMOR_CRISIS_DURATION_SECONDS` | **60** | One-shot crisis window. |
| `GEIGER_RUMOR_EMIGRATION_FRACTION` | **0.34** | Quota `max(1, round(pop × 0.34))`; exits spread **linearly** over the 60 s (`geiger_rumor_forced_departures_this_tick`). Removes ~34 people at pop=100, dropping to 66. |

**Trigger:** First time **`new_radiation_truth < working_doubt`** (after doubt updates, Frank fireside adjustment, and Inner Circle task completion): enqueue crisis; clears `social.last_fireside_chat_at`. One-shot — `geiger_rumor_crisis_triggered` prevents re-trigger. Completing a **Fireside** calls `halt_geiger_rumor_exodus`.

After the exodus auto-resolves, `social.awaiting_post_geiger_exodus_speech` is set to `True`. The next **Give Speech** by the player clears it and sets `fireside_chats_focus_gate_done = True`, which unlocks the **Fireside Chats** Focus node.

---

## Power and energy

| Constant | Value | Dynamics and links |
|----------|-------|-------------------|
| `INITIAL_ENERGY` | **100** | — |
| `LIGHTS_POWER_DRAW` | **0.01** /s | Draw when lights on. |
| `CRANK_POWER_PER_WORKER` | **0.002** /worker/s | Generation; **5 workers ≈ lights**. |
| `MANUAL_CRANK_ENERGY` | **1.0** | Per manual crank button press (`/action/crank`). Blocked during sermon/fireside window. |
| `CRANK_WORKERS_LOYALTY_THRESHOLD` | **10** | Workers above this immediately reduce loyalty (same tick): `excess × CRANK_WORKERS_LOYALTY_PENALTY`. |
| `CRANK_WORKERS_LOYALTY_PENALTY` | **0.5** | Loyalty subtracted per excess crank worker per tick (not per second — applied once per tick regardless of elapsed time). |
| `THEATRE_POWER_DRAW_PER_ACTOR` | **0.003** /s × actors | Added to lights draw when any theatre actors assigned; theatre progression **stalls** if energy ≤ 0 (`handle_theatre_tick`). |

Net energy per tick: `(crank_workers × CRANK_POWER_PER_WORKER − LIGHTS_POWER_DRAW − theatre_draw_ps) × Δt`.

### Player actions (Power)

| Route | Effect | Gate |
|-------|--------|------|
| `/action/crank` | +`MANUAL_CRANK_ENERGY` (1.0) to energy immediately | sermon/fireside lock |
| `/action/toggle-lights` | Flip `lights_on` | sermon/fireside lock |
| `/action/adjust-crank?delta=±1` | Move one worker between Idle ↔ Power crank | sermon/fireside lock |

---

## Farming and rats

### Hydroponic plots

| Constant | Value | Notes |
|----------|-------|-------|
| `INITIAL_FOOD` | **500** | Default if no reserve row. ~12 min runway at pop=66 without farming. |
| `INITIAL_FARM_WORKERS` | **10** | Seed only (seeded to Farming profession line). |
| `FOOD_PER_CAPITA_PER_SECOND` | **0.01** | Human consumption × `food_consumption_multiplier` from active events. |
| `FOOD_PER_WORKER_PER_SECOND` | **0** | Passive production off; harvest is the only farm food source. |
| `FARM_PLANT_GROWTH_SECONDS` | **120** | Crop timer per plot. |
| `FARM_HARVEST_YIELD` | **50** | Harvest food when mean farm workers during growth window = `FARM_HARVEST_YIELD_REF_AVG_WORKERS`. |
| `FARM_HARVEST_YIELD_REF_AVG_WORKERS` | **10** | Reference headcount; yield scales linearly: `yield = 50 × (avg_workers / 10)`. |
| `FARM_PLOT_COUNT` | **4** (`2 × 2`) | Simultaneous independent hydroponic bays. |

Harvest is player-triggered (`/action/farming-plot?plot=N`). Yield uses the **Riemann integral** of farm-worker headcount over the growth window (`growth_worker_seconds / duration`), weighted against `FARM_HARVEST_YIELD_REF_AVG_WORKERS`.

### Resident rats (after `rats_silo_intro`)

| Constant | Value | Notes |
|----------|-------|-------|
| `RAT_BACKGROUND_INITIAL_DRAIN_PS` | **0.1** | Initial food drain/s set on `on_spawn` of `rats_silo_intro`. |
| `RAT_BACKGROUND_DRIFT_STEP_PS` | **0.006** | Uniform random drift amplitude × `min(3, elapsed_s)` per tick. |
| `RAT_BACKGROUND_DRAIN_MIN_PS` | **0.004** | Floor. |
| `RAT_BACKGROUND_DRAIN_MAX_PS` | **0.055** | Cap. |

### Rat trappers (unlocked via Focus Tree)

| Constant | Value | Notes |
|----------|-------|-------|
| `RAT_TRAPPER_PRODUCTION_PER_TRAP_PRESSURE_UNIT` | **0.1** | Production/s = `trapper_count × 0.1 × combined_rat_pressure_ps`. |

`combined_rat_pressure_ps = rat_background_ps + swarm_marginal_ps` (swarm marginal only active while `rats_silo` event is active). Swarm spawn is **suppressed** if trapper output ≥ swarm's marginal spike drain.

Food net per tick: `Δfood = (trapper_production_ps − (pop × per_capita × food_mult + rat_background_ps)) × Δt`.

### Player actions (Farming)

| Route | Effect | Gate |
|-------|--------|------|
| `/action/adjust-food?delta=±1` | Move one worker between Idle ↔ Farming | sermon/fireside lock |
| `/action/adjust-rat-trappers?delta=±1` | Move one worker to/from Rat trapping | `rat_trappers_unlocked` flag; sermon/fireside lock |
| `/action/farming-plot?plot=N` | Plant (if empty) or harvest (if ready) bay N | sermon/fireside lock |

---

## Random events

Events are defined in `app/events.py` as `GameEventSpec` rows. RNG runs each tick per `try_spawn_event` (only if that definition has no active row). Investigation dispatch extends `auto_resolve_at` on all active rows.

### `rats_silo_intro`

| Field | Value |
|-------|-------|
| `spawn_chance_per_tick` | **0.01** |
| `duration_seconds` | **None** (no auto-expire until investigation clears it) |
| `can_spawn` gates | food ≥ **12**, pop ≥ **8**, `silo_rats_introduced == False` |
| `food_consumption_multiplier` | **1.0** (no extra consumption while active) |
| `on_spawn` | Sets `User.silo_rats_introduced = True`, `rat_background_consumption_ps = 0.1` |
| Auto-resolve loyalty delta | **−2** |
| Player-resolve loyalty delta | **+3** (via Farming investigation) |
| `on_player_resolve` | No-op — rat trapper unlock is via Focus Tree `ft_explore_novel_food_sources` |
| Subsystem | `farming` |

### `rats_silo` (swarm spike)

| Field | Value |
|-------|-------|
| `spawn_chance_per_tick` | **0.008** |
| `duration_seconds` | **60** |
| `can_spawn` gates | `silo_rats_introduced == True`, food ≥ **15**, pop ≥ **10**, swarm NOT suppressed by trappers |
| `food_consumption_multiplier` | **3.0** (triples human food consumption while active) |
| Auto-resolve loyalty delta | **−5** |
| Player-resolve loyalty delta | **+4** (via Farming investigation) |
| Subsystem | `farming` |

### `fireside_rhetoric_backlash`

| Field | Value |
|-------|-------|
| `spawn_chance_per_tick` | **0** (manually enqueued only) |
| `duration_seconds` | **`FIRESIDE_RHETORIC_BACKLASH_DURATION_SECONDS` (78)** |
| Trigger | Fearmongering Fireside backfire (38 % chance on completion) |
| `on_spawn` | Immediately applies `+FIRESIDE_BACKLASH_DOUBT_DELTA (+24)` doubt |
| Auto-resolve | Loyalty delta **0**; flavor message only |
| Subsystem | None |

### `geiger_rumor_exodus`

| Field | Value |
|-------|-------|
| `spawn_chance_per_tick` | **0** (manually enqueued only) |
| `duration_seconds` | **`GEIGER_RUMOR_CRISIS_DURATION_SECONDS` (60)** |
| Trigger | First tick where `new_radiation_truth < working_doubt` |
| Effect while active | Linear forced departures each tick (see Geiger section above) |
| Auto-resolve | Sets `awaiting_post_geiger_exodus_speech = True`; loyalty delta **0** |
| Cancelled by | `halt_geiger_rumor_exodus` (any Fireside completion) |
| Subsystem | None |

---

## Social: boredom and doubt

| Constant | Value | Dynamics |
|----------|-------|----------|
| `INITIAL_BOREDOM` | **0** | — |
| `INITIAL_DOUBT` | **45** | Head-start so truth and accumulated doubt converge at ~t=107 s → Geiger triggers at ~1.8 min. |
| `BOREDOM_PER_SECOND` | **0.08** | Boredom rises each tick, capped 100; various actions relieve it. Hits 15 at ~3 min. |
| `BOREDOM_LOYALTY_DRAIN_PER_SECOND_AT_FULL` | **0.08** | Loyalty drag scales linearly with boredom: `(boredom/100) × 0.08 × Δt`. |
| `DOUBT_GROWTH_MAX_PER_SECOND` | **0.04** | Doubt increase rate = `0.04 × (1 − truth/100) × Δt`; zero when truth = 100, maxes at truth = 0. |

### Relief sources (boredom)

| Source | Relief amount |
|--------|---------------|
| Movie screening completion | `spec.boredom_relief_base / (1 + 0.45 × screenings_completed)` (diminishing per title) |
| Sermon completion | **25** (one-shot on next tick after window closes) |
| Theatre `ready` phase | **0.25 /s** × Δt continuously |
| Give Speech (indirect — loyalty + doubt only) | — |

### Relief sources (doubt)

| Source | Relief amount |
|--------|---------------|
| Movie screening completion | `spec.doubt_relief_base / (1 + 0.45 × screenings_completed)` |
| Give Speech | `SOCIAL_SPEECH_DOUBT_RELIEF_BASE / (1 + 0.5 × uses)` (**12** base, diminishing) |
| Fireside: Frank | **+`FIRESIDE_FRANK_DOUBT_DELTA` (+6)** (adds doubt, not relieves) |
| Stage Incident success | **−6** (`INNER_CIRCLE_STAGE_INCIDENT_DOUBT_RELIEF`) |

---

## Movies

### Catalog

| id | Title | Energy cost | Boredom relief (base) | Doubt relief (base) |
|----|-------|-------------|----------------------|---------------------|
| `atomic_cafe` | The Atomic Cafe | **4.0** | **11** | **38** |
| `the_day_after` | The Day After | **4.0** | **21** | **30** |
| `mad_max` | Mad Max | **7.5** | **32** | **17** |
| `simpsons_s06e14_barts_comet` | Simpsons S6E14 "Bart's Comet" | **1.2** | **9** | **0** |

### Mechanics

| Constant | Value | Notes |
|----------|-------|-------|
| `MOVIE_SCREENING_DURATION_SECONDS` | **60** | Timer before boredom relief + exhaustion apply. |
| `SOCIAL_MOVIE_DIMINISH_K` | **0.45** | Diminishing returns per title: `relief = base / (1 + 0.45 × screenings_completed)`. |
| `MOVIE_EXHAUSTION_GAIN_PER_PLAY` | **18** | Added to that title's exhaustion row on each completion. |
| `MOVIE_EXHAUSTION_DECAY_PER_SECOND` | **0.04** | Linear decay per title per second. |
| `MOVIE_EXHAUSTION_LOYALTY_DRAIN_PER_SECOND_AT_FULL` | **0.06** | Loyalty drag from combined exhaustion; no drag below sum 50; scales `(exhaustion−50)/50 × 0.06 × Δt`. |

Energy is deducted **upfront** when the screening starts (`/action/show-movie`). Only one screening can run at a time. Starting a screening resets the movie-pixel heatmap frame counter.

---

## Theatre

### Phases and timers

| Constant | Value | Loyalty accrual | Boredom relief |
|----------|-------|-----------------|----------------|
| `THEATRE_WRITE_SECONDS` | **60** | **0.12 /s** | none |
| `THEATRE_REHEARSE_SECONDS` | **60** | **0.12 /s** | none |
| `THEATRE_PERFORMANCE_INTERVAL_SECONDS` | **60** | **0.12 /s** | **0.25 /s** |

Phase sequence: idle → writing → rehearsing → ready (cycles back to writing after each performance interval). Phase advances only when `energy > 0`. Returning to 0 actors resets to idle and clears the next-performance timer.

| Constant | Value |
|----------|-------|
| `THEATRE_POWER_DRAW_PER_ACTOR` | **0.003 /s** × actor count (drawn whenever actors > 0) |
| `THEATRE_PLAY_TITLES` | King Lear, The Tempest, Mr. Burns: A Post-Electric Play |
| `THEATRE_BOREDOM_RELIEF_PER_PLAY` | **15** (= 0.25 × 60 s, useful for UI copy) |

### Player action

| Route | Effect | Gate |
|-------|--------|------|
| `/action/adjust-theatre?delta=±1` | Move one worker to/from Theatre | sermon/fireside lock; idle workers must be available |

---

## Sermon

| Constant | Value | Notes |
|----------|-------|-------|
| `SERMON_DURATION_SECONDS` | **60** | Blocks all other player actions while active. |
| `SERMON_COMPLETION_LOYALTY_GAIN` | **15** | Applied on the tick after the window closes (`sermon_reward_pending`). |
| `SERMON_COMPLETION_BOREDOM_RELIEF` | **25** | Applied same tick as loyalty gain (passed as `boredom_relief` to `handle_boredom_and_doubt_tick`). |

`/action/start-sermon` — blocked if fireside window is active; no cooldown beyond the window itself.

---

## Give Speech

| Constant | Value | Notes |
|----------|-------|-------|
| `SOCIAL_SPEECH_COOLDOWN_SECONDS` | **120** | Per-player cooldown. |
| `SOCIAL_SPEECH_LOYALTY_GAIN_BASE` | **10** | Instant loyalty: `10 / (1 + 0.5 × uses)`. |
| `SOCIAL_SPEECH_DOUBT_RELIEF_BASE` | **12** | Instant doubt relief: `12 / (1 + 0.5 × uses)`. |
| `SOCIAL_SPEECH_DIMINISH_K` | **0.5** | Diminishing factor per use (`speech_action_count`). |

After the Geiger exodus auto-resolves, the next Give Speech also clears `awaiting_post_geiger_exodus_speech` and sets `fireside_chats_focus_gate_done = True`.

---

## Meet Council

| Constant | Value | Notes |
|----------|-------|-------|
| `SOCIAL_COUNCIL_COOLDOWN_SECONDS` | **180** | Per-player cooldown. |
| Effect | Random ±1–5 to `inner_circle_loyalty` (legacy aggregate, now overwritten by psyche mean each tick) | Flavor message posted to Silo Bulletin. |

---

## Fireside chats

### Cooldowns (vary by Focus unlock)

| State | Panel title | Cooldown |
|-------|-------------|----------|
| Before `ft_fireside_chats` | "Give Speech" | `FIRESIDE_GIVE_SPEECH_COOLDOWN_SECONDS` = **120 s** |
| After `ft_fireside_chats` | "Fireside Chats" | `FIRESIDE_CHAT_COOLDOWN_SECONDS` = **120 s** |
| After `ft_fire_and_brimstone` | "Fire and Brimstone" | `FIRESIDE_BRIMSTONE_COOLDOWN_SECONDS` = **60 s** |

Before `ft_fireside_chats`, only the `reassuring` kind is enabled; `frank` and `fearmongering` are locked.

### Duration and loyalty/doubt effects

`FIRESIDE_CHAT_DURATION_SECONDS` = **30 s**. Effects accrue **proportionally** to the tick's overlap fraction with the broadcast window; the balance (up to 100 % coverage) is applied on the completing tick.

| Kind | Loyalty total | Doubt adjustment | Notes |
|------|---------------|-----------------|-------|
| Reassuring | **+4.5** (over 30 s) | — | |
| Frank | **+7.5** | **+6 doubt** (`FIRESIDE_FRANK_DOUBT_DELTA`) | Adds doubt, does not relieve it |
| Fearmongering | **+13** | **+2.75 doubt** (soft, if no backfire) OR **+24 doubt** (backfire) | **38 %** backfire chance (`FIRESIDE_FEARMONGER_BACKFIRE_CHANCE`) on completion |

### Backfire (fearmongering)

If fearmongering backfires: `enqueue_fireside_rhetoric_backlash` spawns `fireside_rhetoric_backlash` event → immediately adds **+24** doubt (`FIRESIDE_BACKLASH_DOUBT_DELTA`), then the event auto-resolves after **78 s** (`FIRESIDE_RHETORIC_BACKLASH_DURATION_SECONDS`) with a flavor message.

Completing **any** Fireside type calls `halt_geiger_rumor_exodus` (cancels active rumor exodus and resets quotas).

---

## Basket weaving

Mandatory hours per resident (0–4). Set via `/action/adjust-basket-weaving-hours?delta=±1`. Everyone attends — no worker assignment. Effects apply each tick:

| Hours | Loyalty /s | Cash /person/s |
|-------|-----------|----------------|
| 0 | 0 | 0 |
| 1 | **0.09** | **0.0001** |
| 2 | **0.065** | **0.0002** |
| 3 | **0.045** | **0.0003** |
| 4 | **0.028** | **0.0004** |

Cash added to `social.inner_circle_cash` = `cash_per_person_per_second × population × Δt`. Note: more hours → more cash but less loyalty (diminishing).

---

## Inner Circle

### Initial member seeds

| Name | Loyalty | Popularity | Frustration | Disposition |
|------|---------|-----------|-------------|-------------|
| Marnie Coldwell | 58 | 52 | 40 | 68 |
| Jace Orbin | 62 | 48 | 34 | 52 |
| Vesper Kline | 60 | 55 | 38 | 76 |
| Tamsin Greer | 59 | 50 | 39 | 58 |
| Nadia Firth | 61 | 53 | 36 | 72 |

`INITIAL_INNER_CIRCLE_CASH` = **50**.

### Per-member psyche tick (`tick_member_psyche`)

| Constant | Value | Effect |
|----------|-------|--------|
| `INNER_CIRCLE_PRESSURE_LOYALTY_WEIGHT` | **0.55** | Pressure = `0.55 × (100 − bunker_loyalty) + 0.45 × doubt` |
| `INNER_CIRCLE_PRESSURE_DOUBT_WEIGHT` | **0.45** | — |
| `INNER_CIRCLE_FRUSTRATION_DRIFT_PER_SECOND` | **0.065** | Frustration moves toward pressure: `fr += 0.065 × (pressure − fr) × Δt` |
| `INNER_CIRCLE_UNPOPULAR_FRUSTRATION_PER_POINT_PER_SECOND` | **0.004** | Extra frustration/s per popularity point below 50 |
| `INNER_CIRCLE_LOYALTY_DRIFT_PER_SECOND` | **0.04** | Loyalty eases toward `100 − frustration` at speed `0.04 × (slow_min + (1 − slow_min) × (1 − disp/100))` |
| `INNER_CIRCLE_DISPOSITION_LOYALTY_SLOW_MIN` | **0.22** | High disposition (accommodating) slows loyalty recovery toward equilibrium |

Members skip loyalty drift while a task is running (`member_is_busy`). Departed members are skipped entirely. Aggregate `inner_circle_loyalty` (on `BunkerSocialState`) is the rounded mean of non-departed members' loyalty, updated each tick.

### Tasks

All tasks require `ft_venture_out` to be completed first (except `temp_job`, which requires `ft_worse_than_being_exploited`).

#### Grant Luxuries

| Constant | Value |
|----------|-------|
| `INNER_CIRCLE_GRANT_LUXURIES_DURATION_SECONDS` | **10** |
| `INNER_CIRCLE_GRANT_LUXURIES_FOOD_COST` | **28** |
| `INNER_CIRCLE_GRANT_LUXURIES_ENERGY_COST` | **10** |
| On complete | Member frustration −**14** (`INNER_CIRCLE_GRANT_LUXURIES_FRUSTRATION_DELTA`); `sync_aggregate_inner_circle_loyalty` called |

#### Stage Incident

| Constant | Value |
|----------|-------|
| `INNER_CIRCLE_STAGE_INCIDENT_DURATION_SECONDS` | **48** |
| `INNER_CIRCLE_STAGE_INCIDENT_DISCOVER_CHANCE` | **0.34** (34 % exposed) |
| On **success** (66 %): member loyalty | **+9** (`INNER_CIRCLE_STAGE_INCIDENT_MEMBER_LOYALTY_DELTA`) |
| On **success**: doubt relief | **−6** (`INNER_CIRCLE_STAGE_INCIDENT_DOUBT_RELIEF`) |
| On **success**: bunker loyalty bonus | **+2** (`INNER_CIRCLE_STAGE_INCIDENT_BUNKER_LOYALTY_DELTA`) |
| On **exposed** (34 %): popularity drop | **−22** (`INNER_CIRCLE_STAGE_INCIDENT_DISCOVER_POPULARITY_DROP`) |
| On **exposed**: doubt bump | **+11** (`INNER_CIRCLE_STAGE_INCIDENT_DISCOVER_DOUBT_BUMP`) |

#### Buy Groceries

| Constant | Value |
|----------|-------|
| `INNER_CIRCLE_BUY_GROCERIES_DURATION_SECONDS` | **72** |
| `INNER_CIRCLE_BUY_GROCERIES_CASH_COST` | **10** |
| On complete: food gain | **+42** (`INNER_CIRCLE_BUY_GROCERIES_FOOD_GAIN`) |
| On complete: energy gain | **+14** (`INNER_CIRCLE_BUY_GROCERIES_ENERGY_GAIN`) |
| Bad outcome chance | **0.52** (52 %) → **+12** doubt (`INNER_CIRCLE_BUY_GROCERIES_DOUBT_BAD_AMOUNT`) |

#### Temp Job

Requires `ft_worse_than_being_exploited` focus completed.

| Constant | Value |
|----------|-------|
| `INNER_CIRCLE_TEMP_JOB_DURATION_SECONDS` | **96** |
| On complete: cash gain | **+38** (`INNER_CIRCLE_TEMP_JOB_CASH_GAIN`) |
| On complete: member frustration bump | **+12** (`INNER_CIRCLE_TEMP_JOB_MEMBER_FRUSTRATION_BUMP`) |
| Bad outcome chance | **0.48** (48 %) → **+15** doubt (`INNER_CIRCLE_TEMP_JOB_DOUBT_BAD_AMOUNT`), sets `temp_job_backfire_seen = True` |

---

## Focus Tree

Nodes unlock in a chain. No timer — player clicks `/action/focus-tree-complete?node_id=<id>` when prerequisites are met.

| Node id | Title | Parents | Extra predicate | Completion hook / event |
|---------|-------|---------|-----------------|------------------------|
| `ft_explore_novel_food_sources` | Explore Novel Food Sources | — | None | `rat_trappers_unlock` (sets `User.rat_trappers_unlocked`, posts bulletin) |
| `ft_fireside_chats` | Fireside Chats | `ft_explore_novel_food_sources` | `fireside_chats_focus_gate_done == True` (requires Geiger exodus + Give Speech) | — (unlocks frank/fearmongering Fireside kinds + shorter cooldown) |
| `ft_bunker_shakespeare_company` | Found Bunker Shakespeare Company | `ft_explore_novel_food_sources` | loyalty < **75** OR boredom > **15** | — (no explicit hook; unlocks Theatre path) |
| `ft_venture_out` | Venture Out | `ft_fireside_chats` + `ft_bunker_shakespeare_company` | population < **67** (⅔ of 100) | `venture_out_narrative`: slot-2 member (Vesper Kline) departs; farewell message posted to Group Chat |
| `ft_worse_than_being_exploited` | The only thing worse than being exploited… | `ft_venture_out` | IC cash < **$20** (`TEMP_JOB_FOCUS_CASH_THRESHOLD`) | — (unlocks Temp Job task) |
| `ft_not_being_exploited` | …is not being exploited. | `ft_venture_out` | `temp_job_backfire_seen == True` | — |
| `ft_fire_and_brimstone` | Fire and Brimstone | `ft_worse_than_being_exploited` + `ft_not_being_exploited` | None | — (changes Fireside panel to "Fire and Brimstone", cooldown drops to 60 s) |

---

## Investigation (routine sweeps)

`/action/dispatch-investigation?system=<id>` — moves **5 Idle workers** to Investigation for **10 s**. Blocked during any existing investigation, sermon, or fireside window; also requires ≥ 5 idle workers.

While deployed, **every** active event's `auto_resolve_at` is extended to at least `busy_until` (prevents timed events from expiring during the sweep). When the sweep completes:

- If an active event's `system` matches `investigation_target_system`: **player-resolve** path fires; only the oldest matching event row is resolved.
- Otherwise: routine "sweep complete" flavor message; loyalty delta 0.

---

## Player actions — summary table

All action routes check for sermon/fireside lock (return 204 silently if blocked).

| Route | System | Effect |
|-------|--------|--------|
| `/action/crank` | Power | +1.0 energy |
| `/action/toggle-lights` | Power | Flip lights |
| `/action/adjust-crank?delta=±1` | Power | Worker headcount |
| `/action/adjust-food?delta=±1` | Farming | Worker headcount |
| `/action/adjust-rat-trappers?delta=±1` | Farming | Worker headcount; gated on `rat_trappers_unlocked` |
| `/action/adjust-theatre?delta=±1` | Social | Worker headcount |
| `/action/adjust-basket-weaving-hours?delta=±1` | Social | Hours (0–4) |
| `/action/farming-plot?plot=N` | Farming | Plant / harvest bay N |
| `/action/show-movie?movie_id=X` | Social | Deduct energy; start 60 s screening |
| `/action/give-speech` | Social | Instant loyalty/doubt; 300 s CD |
| `/action/meet-council` | Social | ±1–5 IC loyalty; 600 s CD |
| `/action/start-fireside-chat?kind=X` | Social | 30 s broadcast window; CD varies by Focus progress |
| `/action/start-sermon` | Social | 120 s window; loyalty/boredom bonus on completion |
| `/action/focus-tree-complete?node_id=X` | Focus Tree | Complete node if gates pass |
| `/action/dispatch-investigation?system=X` | All | Deploy 5-person sweep for 10 s |
| `/action/inner-circle/grant-luxuries?slot=N` | Inner Circle | Spend food/energy; reduce frustration |
| `/action/inner-circle/stage-incident?slot=N` | Inner Circle | 48 s task; success/exposed RNG |
| `/action/inner-circle/buy-groceries?slot=N` | Inner Circle | 72 s task; spend cash; gain food + energy |
| `/action/inner-circle/temp-job?slot=N` | Inner Circle | 96 s task; gain cash; frustration risk |

---

## Narrative beats

One-shot messages delivered to `bulletin` when triggered (each fires at most once per player):

| `message_id` | Trigger | Text summary |
|-------------|---------|-------------|
| `welcome_message` | First tick with a valid readings row | "Welcome to Bunker.OS 1.2.0…" |
| `first_departure_notice` | First tick where `departed_this_tick > 0` and no prior departure ever | "A community member has decided to brave the outdoors…" |

---

## Display / Grafana-only

Environment pixel grid **48×48**, backfill **15 min** / **48** samples, reference noise half-range **0.06**, `BAD_APPLE_FRAME_COUNT` **20**. Social movie pixel stack mirrors span; sequence **60** frames; idle heatmap id `__idle_noise__`. These are primarily visual; see [`heatmap_display.md`](heatmap_display.md) where applicable.

---

## Logging

| Constant | Value |
|----------|-------|
| `GAMESTATE_LOG_INTERVAL_SECONDS` | **10** (0 = off) |

---

## Related docs

- [`events.md`](events.md) — random event registry and spawn behavior.
- [`README.md`](../README.md) — notes that tuning lives in `constants.py` and events in `events.py`.
