# silo

A weird, high-concept game in which all player interaction takes place inside a
Grafana dashboard. The player character lives in a post-apocalyptic bunker and
is convinced the outside world is still uninhabitable, despite all telemetry
showing radiation levels steadily decaying back to normal.

This repository is the scaffolding for that experience: a Flask app to manage
player identity and game state, a Postgres database that's also the source of
truth for Grafana, and a provisioned Grafana instance that acts as the entire
game UI.

## Architecture

```
   Player browser
         │
         ▼
  ┌──────────────┐         mint / reuse UUID,         ┌──────────────┐
  │  Flask (web) │  ─────  set cookie, 302 redirect ─►│   Grafana    │
  │   :5000      │                                    │    :3000     │
  └──────┬───────┘                                    └──────┬───────┘
         │ writes (users, radiation_levels)                  │ reads
         ▼                                                   ▼
                       ┌─────────────────────┐
                       │   Postgres (db)     │
                       │       :5432         │
                       └─────────────────────┘
                                 ▲
                                 │
                       APScheduler tick (in-process
                       inside Flask) appends a new
                       radiation sample per user
                       every DECAY_TICK_SECONDS.
```

- **Flask** mints a UUID for each new player on first visit, writes a `users`
  row plus an initial `radiation_levels` row, drops a long-lived cookie, and
  302s the player into Grafana with the UUID injected as a dashboard variable.
- **Postgres** is the single source of truth. Every gameplay system should add
  a `user_id` foreign key so player state stays instanced.
- **Grafana** is the game's entire UI. Anonymous viewer access is enabled and
  the login form is disabled, so the player never sees Grafana's auth chrome.
  Dashboards are provisioned from `grafana/dashboards/` (environment, power,
  farming, etc.).
- **APScheduler** runs in-process with Flask and ticks every
  `DECAY_TICK_SECONDS` (default 30s), appending one decayed sample per player.

## Running locally

You need Docker Desktop (or any Docker engine with the Compose v2 plugin).

**First time or after changing `Dockerfile`, `requirements.txt`, or anything that must live inside the image:**

```bash
docker compose up -d --build
```

**After changing Python models or startup schema guards in `app/`** (new tables, columns, `app/__init__.py` migration helpers): the `web` service bind-mounts the repo, but **Flask only runs migrations when the process starts**. Recreate or restart `web` so `create_app()` runs again:

```bash
docker compose up -d --force-recreate web
# or: docker compose restart web
```

If you prefer a one-liner that also rebuilds the image (needed when dependencies changed), rebuild only `web`:

```bash
docker compose up -d --build web
```

First boot takes a minute while images download and Postgres initializes. Once
the logs settle, open:

- Game entrypoint: <http://localhost:5001>
  (this is what a player should hit; it'll redirect you into Grafana)
- Grafana directly: <http://localhost:3000>
- Postgres (for psql / inspection): `localhost:5432`, user/pw/db all `silo`

> macOS note: port 5000 is reserved by AirPlay Receiver, so the host-side port
> for Flask is `5001`. Inside the container Flask still listens on 5000.

**Agents / smoke tests:** see [docs/AGENT_DEPLOYMENT_AND_VERIFICATION.md](docs/AGENT_DEPLOYMENT_AND_VERIFICATION.md) for curl checks, `psql` snippets, and a short checklist.

To tear it all down:

```bash
docker compose down            # stops containers, keeps data
docker compose down -v         # also wipes the Postgres + Grafana volumes
```

## Unit tests

Tests live under `tests/` and use **pytest** (`pytest.ini` sets `pythonpath`).

```bash
pip install -r requirements.txt
pytest -m "not integration"    # fast suite; no database required
pytest                         # full suite (needs Postgres on `DATABASE_URL`)
```

### Guidelines

- **Coverage over volume:** Add tests where bugs actually hide—invariants at module boundaries, wiring between packages (`jobs` ↔ `narrative`, `jobs` ↔ `events`), and config keys required by registries. Prefer one focused test over several that restate the same behavior.
- **Stay succinct:** Short test bodies; extract helpers only when several tests share non-trivial setup. Avoid boilerplate “smoke” tests that only import a symbol unless they encode a real regression (e.g. a name that must remain imported because another function calls it by reference).
- **No redundancy:** Do not test the standard library, Flask, or SQLAlchemy behavior. Do not duplicate the same assertion across multiple tests unless each failure mode is distinct (e.g. import binding vs. AST presence for the same regression).
- **Integration sparingly:** Use the `integration` marker for checks that need a real DB (see `tests/test_game_tick_integration.py`). Keep them few and high-signal; default CI can run `-m "not integration"` only.

## Game tuning

All knobs live in `app/constants.py` (radiation decay, population, energy,
farming, etc.). Flask still exposes them on `current_app.config` for routes and
jobs. Random gameplay event numbers (spawn odds, durations, loyalty deltas,
eligibility thresholds, messages) live on each `GameEventSpec` in `app/events.py`,
not in `constants.py`.

With the defaults, a fresh player sees a smooth decay curve dropping from 100
to ~50 over ten minutes, ~25 over twenty, etc. — slow enough to feel like an
environmental measurement, fast enough to be visibly changing during a play
session.

## Project layout

```
.
├── app/                      # Flask app package
│   ├── __init__.py           #   factory + scheduler bootstrap
│   ├── config.py             #   env-driven config (+ game tuning from constants)
│   ├── constants.py          #   game tuning (single source of truth)
│   ├── extensions.py         #   db + scheduler singletons
│   ├── jobs.py               #   game tick + background jobs
│   ├── models.py             #   ORM models (users, time series, narrative, …)
│   └── routes.py             #   landing route -> redirect to Grafana
├── grafana/
│   ├── dashboards/
│   │   ├── environment.json  # uid silo-environment
│   │   ├── power.json        # uid silo-power
│   │   └── farming.json      # uid silo-farming
│   └── provisioning/
│       ├── dashboards/dashboards.yml
│       └── datasources/postgres.yml
├── docker-compose.yml        # web + db + grafana
├── Dockerfile                # web image
├── pytest.ini                # pytest paths + markers
├── tests/                    # unit + integration tests
├── requirements.txt
├── wsgi.py                   # entrypoint
├── .env.example
└── README.md
```

## Adding new gameplay systems

The radiation model is the template. To add a new piece of player state:

1. Add a SQLAlchemy model in `app/models.py` with a `user_id` UUID foreign key
   to `users.id` (cascade delete) and a `timestamp` if it's a time series.
2. If it changes over time without player input, add a function in
   `app/jobs.py` and register it in `app/__init__.py`'s scheduler block.
3. Add a panel to the appropriate dashboard under `grafana/dashboards/` that
   filters on `user_id = '$user_id'::uuid`. The `user_id` dashboard variable is
   already wired up and populated from the URL by Flask.
4. For **existing** tables on developer machines that already have a Postgres
   volume, additive columns may need a small `ALTER TABLE ... IF NOT EXISTS`
   guard in `app/__init__.py` (same pattern as `level_display` and farming
   columns) until Alembic is adopted—then restart `web` so that code runs.

## Notes / known caveats

- `db.create_all()` plus a few explicit `_ensure_*` helpers in `create_app()`
  bootstrap and patch the schema on **each `web` process start**. Restart or
  recreate the `web` container after schema-related code changes. For
  production, plan to swap in Alembic instead of ad-hoc ALTERs.
- The Grafana SQL queries interpolate `$user_id` directly. For a local,
  single-trusted-player game this is fine; before hosting publicly, switch to
  parameterized queries or have Flask expose a JSON API and use Grafana's
  Infinity datasource instead.
- The decay scheduler runs in the Flask process. If we ever scale to multiple
  Flask workers, move it to a dedicated worker (Celery beat, a sidecar
  container running just the scheduler, etc.) so it ticks exactly once.
- **Local `web` bind mount:** `docker-compose.yml` mounts the repo at `/app` so
  code changes apply without rebuilding. Omit that `volumes` block for an
  image-only production deploy.
