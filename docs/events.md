# Random gameplay events format

Events are **defined in Python** in [`app/events.py`](../app/events.py). Runtime state is **zero or more concurrent rows** per player in `player_active_events` (`PlayerActiveEvent`), keyed by surrogate **`id`**. At most **one active row per `(user_id, kind)`** (unique constraint).

Global simulation knobs (tick interval, baseline food rates, investigation team sizes, etc.) stay in [`app/constants.py`](../app/constants.py). Per-event tuning and behaviour live on each `GameEventSpec`.

**Schema:** this repo assumes Postgres can be wiped between sessions (`create_all` on empty DB). The ORM defines `player_active_events` with UUID **`id`** as PK and **`UNIQUE (user_id, kind)`**.

---

## `GameEventSpec`

| Field | Type | Role |
|--------|------|------|
| `definition` | `EventDefinition` | Which event this spec is; enum **values** are the wire ids persisted on `PlayerActiveEvent.kind`. |
| `spawn_chance_per_tick` | `float` | Probability **per eligible tick** that this spec wins the spawn roll (see spawn loop below). |
| `duration_seconds` | `int \| None` | Active window in seconds, or **`None`** for no timer (`auto_resolve_at` stays null until investigation dispatch sets a floor). |
| `tick_effects` | `(user_id, tick_time) → EventTickEffects` | Per-tick simulation modifiers while the row exists (extend `EventTickEffects` for new systems). |
| `can_spawn` | `EventSpawnContext → bool` | Hard gate before RNG (food, population, flags, suppression rules, etc.). |
| `auto_resolve` | `(user_id, tick_time) → EventOutcome` | Runs when `tick_time >= auto_resolve_at` (row has a deadline) and no investigation timer blocks resolution. |
| `player_resolve` | `(user_id, tick_time) → EventOutcome` | Runs when an investigation sweep **finishes** and its target subsystem **matches** `spec.system`. |
| `spawn_announcement` | `(user_id, tick_time) → str \| None` | Optional `SystemMessage` body when the row is created; `None` skips posting. |
| `on_spawn` | `(user_id, tick_time) → None` | Runs right after the active-event row is added (same transaction), before `spawn_announcement`; use for immediate DB/session mutations (e.g. flags on `User`). |
| `system` | `str \| None` | Optional bunker subsystem id (e.g. `"farming"`); ties player-resolution to investigation target. |
| `on_player_resolve` | `(user_id, tick_time) → None` \| omitted | Optional hook run immediately after **`player_resolve`** in **`finalize_investigation_if_due`** (same flush); use for unlock flags etc. |

---

## Supporting types

### `EventDefinition`

`StrEnum` of every spawnable random event. Member names are stable Python identifiers; **values** are the strings stored in `PlayerActiveEvent.kind` and used as keys in **`EVENTS_BY_DEFINITION`**. Compare loaded rows with `row.kind == EventDefinition.RATS_SILO` (equality works against plain `str` from the DB).

### `EventSpawnContext`

Immutable snapshot passed into `can_spawn`. For spawning, [`try_spawn_event`](../app/events.py) rebuilds it **inside each iteration** via `event_spawn_context_from_user(...)` so `User` flags (`silo_rats_introduced`, rat drain, etc.) stay consistent **within one tick** after earlier specs ran **`on_spawn`**.

### `EventOutcome`

`loyalty_delta` plus a single `message` string (persisted as a `SystemMessage` on resolve paths).

### `EventTickEffects`

Frozen bundle of **defaults-safe** per-tick modifiers. Today it includes `food_consumption_multiplier` (1.0 = unchanged).

With multiple concurrent rows, `game_tick` calls **`active_event_tick_effects`**, which **loads every active row**, computes each spec’s `tick_effects`, and **merges**:

- **`food_consumption_multiplier`**: **product** of all active values (e.g. intro `1.0` × swarm `3.0` ⇒ `3.0`). When adding new fields to `EventTickEffects`, define an explicit merge rule.

Some helpers read a spec’s tick effects **without** an active DB row (e.g. marginal swarm food pressure uses `rats_silo`’s `tick_effects` at a fixed reference `tick_time` so math stays aligned with the spec).

---

## Registration

- **`EventDefinition`**: `StrEnum`; add a member for each new wire id.
- **`REGISTERED_EVENTS`**: tuple of `GameEventSpec` instances (declaration order is spawn iteration order).
- **`EVENTS_BY_DEFINITION`**: map **`definition.value` (str) → spec** for DB lookups.
- **`spec_for_definition(definition)`**: accepts a **`str`** or **`EventDefinition`**.
- **`active_events_for_user(user_id)`**: list of all active rows for that player.
- **`player_has_active_event_kind(user_id, definition)`**: whether that definition already has an active row (spawn dedupe).
- **`player_has_any_active_event(user_id)`**: whether any random event row exists.
- **`event_spawn_context_from_user(...)`**: builds `EventSpawnContext` from DB + tick readings (food level, population, trapper count).

---

## Lifecycle

### Spawn (`try_spawn_event`)

1. Iterate **`REGISTERED_EVENTS` in order**. For each spec:
   - Skip if this **`definition`** already has an active row (**`player_has_active_event_kind`**).
   - Build **`EventSpawnContext`** from the current **`User`** row and static tick inputs (food level, population, trapper count).
   - Skip if `can_spawn(ctx)` is false.
   - Roll `random.random() < spawn_chance_per_tick`.
   - On success: insert `PlayerActiveEvent`, call **`on_spawn`**, then **`spawn_announcement`** (optional message), then **continue** (another spec may spawn the same tick).

### While active

- **`active_event_tick_effects`**: merges tick effects across **all** active rows (see **EventTickEffects** above).

### Resolution

- **`auto_resolve_if_due`**: while investigation is not blocking timers, finds **every** row with non-null `auto_resolve_at <= tick_time`, deletes each, **`release_investigation_team_to_idle`** (idempotent), applies **`auto_resolve`** per row, posts messages, **sums** loyalty deltas.
- **`finalize_investigation_if_due`**: when the sweep ends, if any active event’s **`system`** matches **`investigation_target_system`**, resolves **the oldest matching row** (`started_at` ascending) via **`player_resolve`**, runs optional **`on_player_resolve`**, deletes **only that row**, then posts the outcome message; otherwise posts a routine “sweep complete” style message.

Dispatching a subsystem investigation sets or **extends** `auto_resolve_at` on **every** active row whose deadline is missing or would fall before the sweep ends (`try_dispatch_investigation`).

---

## Adding a new event

1. Add a **`EventDefinition`** member whose **value** is the wire id you will persist on `PlayerActiveEvent.kind`.
2. Implement **`can_spawn`** using `EventSpawnContext` (add fields to the context and refresh logic in `event_spawn_context_from_user` / callers if needed).
3. Implement **`tick_effects`**; extend **`EventTickEffects`** with new optional fields if something other than food must change per tick — **also extend merge logic** in `active_event_tick_effects`, then read that bundle from [`app/jobs.py`](../app/jobs.py) (or the relevant subsystem).
4. Implement **`auto_resolve`**, **`player_resolve`**, **`on_spawn`** (use a no-op if nothing runs at spawn), optional **`on_player_resolve`** for investigation-completion side effects, and optionally **`spawn_announcement`**.
5. Set **`system`** if resolution should tie to an investigation sweep on that subsystem (`GAME_SYSTEM_IDS` / labels live with constants).
6. Append a **`GameEventSpec(...)`** to **`REGISTERED_EVENTS`**.
7. Add tests under `tests/test_events_*.py` as appropriate.

---

## Related entry points

| Concern | Where |
|---------|--------|
| Tick builds spawn inputs, applies merged tick effects, spawn/resolution | [`app/jobs.py`](../app/jobs.py) (`game_tick` and helpers) |
| Investigation dispatch HTTP | [`app/routes.py`](../app/routes.py) |
| Model | `PlayerActiveEvent`, `User.active_game_events`, `User.rat_trappers_unlocked` in [`app/models.py`](../app/models.py) |
