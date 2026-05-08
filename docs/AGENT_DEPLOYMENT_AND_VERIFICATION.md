# Agent reference: deployment and verification

This document summarizes **automated and semi-automated steps** that have been used in Cursor agent sessions for the **tojam26 / silo** stack (Flask + Postgres + Grafana). Other agents can follow the same checklist after changing backend code, dashboards, or Docker config.

For product architecture and tuning knobs, see the root [README.md](../README.md).

---

## 1. Prerequisites

- **Docker** with Compose v2 (`docker compose`).
- Working directory: **repository root** (where `docker-compose.yml` lives).
- **Network permission** may be required for first-time image pulls.

---

## 2. Start or refresh the stack

**Full rebuild (after Dockerfile or `requirements.txt` changes):**

```bash
docker compose up -d --build
```

**Start without rebuild (compose/env/dashboard volume mounts only):**

```bash
docker compose up -d
```

**Foreground run with logs (debugging):**

```bash
docker compose up --build
```

Wait until `db` is healthy (`depends_on` gates `web` and `grafana`). First cold start can take ~1 minute while images download.

---

## 3. Tear down (optional)

**Stop project containers; keep named volumes (Postgres + Grafana data persist):**

```bash
docker compose down
```

**Stop and delete volumes (clean DB and Grafana state):**

```bash
docker compose down -v
```

These commands only affect **services defined in this project’s** `docker-compose.yml`; other Docker projects on the machine are untouched.

**Remove a single service’s container and its anonymous volume (example: DB only):**

```bash
docker compose rm -sv db
```

---

## 4. Quick HTTP checks

Run from the host after `docker compose up`.

| Check | Command / URL | Expected |
|--------|----------------|----------|
| Flask liveness | `curl -s http://localhost:5001/healthz` | JSON `{"status":"ok"}` |
| New player + redirect | `curl -sS -o /dev/null -w "%{redirect_url}\n" http://localhost:5001/new` | Redirect to Grafana with `d/silo-environment` (or configured UID) and `var-user_id=<uuid>` |
| Grafana environment dashboard | `curl -sS -o /dev/null -w "%{http_code}\n" "http://localhost:3000/d/silo-environment"` | `200` |
| Grafana power dashboard | `curl -sS -o /dev/null -w "%{http_code}\n" "http://localhost:3000/d/silo-power"` | `200` |
| Grafana farming dashboard | `curl -sS -o /dev/null -w "%{http_code}\n" "http://localhost:3000/d/silo-farming"` | `200` |

**System messages API (needs a real UUID from the DB or redirect URL):**

```bash
curl -s "http://localhost:5001/system-messages?user_id=<UUID>" | python3 -m json.tool
```

Optional: confirm CORS for browser `fetch` from Grafana:

```bash
curl -sI "http://localhost:5001/system-messages?user_id=<UUID>" | grep -i "access-control\|content-type"
```

---

## 5. Database inspection (`psql`)

Postgres is exposed on **localhost:5432**; user, password, and database name are all **`silo`** (see `docker-compose.yml`).

**List tables:**

```bash
docker compose exec db psql -U silo -d silo -c "\dt"
```

**One-liner UUID for ad-hoc queries (avoids shell interpreting hyphens):**

```bash
docker compose exec -T db psql -U silo -d silo -tAc "SELECT id FROM users LIMIT 1;" > /tmp/silo_uid.txt
cat /tmp/silo_uid.txt
```

Use a variable name other than `UID` (reserved on macOS / zsh); e.g. `SILO_UID=$(tr -d '[:space:]' </tmp/silo_uid.txt)`.

**Example: verify a table exists after a model change:**

```bash
docker compose exec -T db psql -U silo -d silo -c "\dt system_messages"
```

**Outdoor radiation (`level` vs noisy `level_display`):** after `web` has started at least once, Postgres should have both columns (new installs get them from `create_all()`; old volumes get `level_display` from the app startup guard in `app/__init__.py`).

```bash
docker compose exec -T db psql -U silo -d silo -c "\d radiation_levels"
docker compose exec -T db psql -U silo -d silo -c "SELECT level, level_display FROM radiation_levels LIMIT 3;"
```

Grafana panels use `COALESCE(level_display, level)`. If Grafana reports “column does not exist”, the DB was never migrated: restart the `web` container (or run `ALTER TABLE radiation_levels ADD COLUMN IF NOT EXISTS level_display double precision` plus `UPDATE radiation_levels SET level_display = level WHERE level_display IS NULL` as `silo`).

**Farming (`food_reserves`, `bunker_systems.food_workers`, `crop_ready_at`):**

```bash
docker compose exec -T db psql -U silo -d silo -c "\d food_reserves"
docker compose exec -T db psql -U silo -d silo -c "SELECT level, consumption_per_second, production_per_second FROM food_reserves LIMIT 2;"
```

Use `-T` on `docker compose exec` when piping to avoid TTY allocation issues.

---

## 6. Grafana dashboard JSON

Provisioned dashboards live under `grafana/dashboards/` (currently **`environment.json`** UID `silo-environment`, **`power.json`** UID `silo-power`, **`farming.json`** UID `silo-farming`). Grafana reloads provisioned files on a short interval; bumping the dashboard JSON **`version`** field encourages a faster pick-up after edits.

**Validate JSON syntax locally:**

```bash
python3 -c "import json; json.load(open('grafana/dashboards/environment.json')); json.load(open('grafana/dashboards/power.json')); json.load(open('grafana/dashboards/farming.json')); print('ok')"
```

---

## 7. Python sanity check (no test suite required)

There is no dedicated pytest suite documented for this repo; agents have used compile-only verification:

```bash
python3 -m compileall app -q
```

If a project venv exists (e.g. `env/`), you can instead run:

```bash
./env/bin/python -m compileall app -q
```

---

## 8. Service map (local defaults)

| Service | Host port | Notes |
|---------|-----------|--------|
| `web` (Flask) | **5001** → container `5000` | Player entry: `http://localhost:5001` or `/new` for a fresh session |
| `grafana` | **3000** | Anonymous viewer; dashboards under `/d/<uid>` |
| `db` (Postgres) | **5432** | `silo` / `silo` / `silo` |

---

## 9. Grafana logs (when panels show “no data” or 400s)

```bash
docker compose logs grafana --tail=50
```

Past sessions used this to spot failed `/api/ds/query` requests (e.g. missing `var-user_id` in the URL, or SQL errors).

---

## 10. Flask / scheduler notes for agents

- **Game tick and other jobs** run **inside the `web` container** via APScheduler; they are not separate containers.
- In **Flask debug mode**, the reloader can spawn two processes; the app factory should only start the scheduler in the worker that owns ticks (see `WERKZEUG_RUN_MAIN` guard in `app/__init__.py`).
- Schema is bootstrapped with **`db.create_all()`** on startup, not Alembic (see README caveats).

---

## 11. Minimal “changed something” checklist

1. `python3 -m compileall app -q`
2. `python3 -c "import json; ..."` for any edited `grafana/dashboards/*.json`
3. `docker compose up -d --build` (or `-d` if only JSON/env changed and images unchanged)
4. `curl -s http://localhost:5001/healthz`
5. Spot-check Grafana URLs above or open `http://localhost:5001/new` in a browser

---

*Derived from agent session transcripts and recurring verification commands used while building this repository.*
