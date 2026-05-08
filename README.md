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
  The dashboard is provisioned from `grafana/dashboards/radiation.json`.
- **APScheduler** runs in-process with Flask and ticks every
  `DECAY_TICK_SECONDS` (default 30s), appending one decayed sample per player.

## Running locally

You need Docker Desktop (or any Docker engine with the Compose v2 plugin).

```bash
docker compose up --build
```

First boot takes a minute while images download and Postgres initializes. Once
the logs settle, open:

- Game entrypoint: <http://localhost:5001>
  (this is what a player should hit; it'll redirect you into Grafana)
- Grafana directly: <http://localhost:3000>
- Postgres (for psql / inspection): `localhost:5432`, user/pw/db all `silo`

> macOS note: port 5000 is reserved by AirPlay Receiver, so the host-side port
> for Flask is `5001`. Inside the container Flask still listens on 5000.

To tear it all down:

```bash
docker compose down            # stops containers, keeps data
docker compose down -v         # also wipes the Postgres + Grafana volumes
```

## Game tuning

All knobs live in `docker-compose.yml` under the `web` service env block (or
`.env` if you copy `.env.example`):

| Variable                  | Default | Meaning                                                 |
| ------------------------- | ------: | ------------------------------------------------------- |
| `INITIAL_RADIATION`       |  `100`  | Starting "rads" recorded on player creation.            |
| `DECAY_TICK_SECONDS`      |   `30`  | How often the background job writes a new sample.       |
| `DECAY_HALF_LIFE_SECONDS` |  `600`  | Exponential half-life of the radiation level.           |

With the defaults, a fresh player sees a smooth decay curve dropping from 100
to ~50 over ten minutes, ~25 over twenty, etc. — slow enough to feel like an
environmental measurement, fast enough to be visibly changing during a play
session.

## Project layout

```
.
├── app/                      # Flask app package
│   ├── __init__.py           #   factory + scheduler bootstrap
│   ├── config.py             #   env-driven config
│   ├── extensions.py         #   db + scheduler singletons
│   ├── jobs.py               #   radiation decay tick
│   ├── models.py             #   User, RadiationLevel
│   └── routes.py             #   landing route -> redirect to Grafana
├── grafana/
│   ├── dashboards/
│   │   └── radiation.json    # provisioned dashboard (uid: silo-main)
│   └── provisioning/
│       ├── dashboards/dashboards.yml
│       └── datasources/postgres.yml
├── docker-compose.yml        # web + db + grafana
├── Dockerfile                # web image
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
3. Add a panel to `grafana/dashboards/radiation.json` that filters on
   `user_id = '$user_id'::uuid`. The `user_id` dashboard variable is already
   wired up and populated from the URL by Flask.

## Notes / known caveats

- `db.create_all()` is used to bootstrap the schema. Once gameplay schema
  starts changing for real, swap in Alembic.
- The Grafana SQL queries interpolate `$user_id` directly. For a local,
  single-trusted-player game this is fine; before hosting publicly, switch to
  parameterized queries or have Flask expose a JSON API and use Grafana's
  Infinity datasource instead.
- The decay scheduler runs in the Flask process. If we ever scale to multiple
  Flask workers, move it to a dedicated worker (Celery beat, a sidecar
  container running just the scheduler, etc.) so it ticks exactly once.
