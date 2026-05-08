"""Background jobs run by APScheduler."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from flask import Flask
from sqlalchemy import select

from .extensions import db
from .models import BunkerLoyalty, BunkerPopulation, BunkerSystems, EnergyReserve, RadiationLevel, User


log = logging.getLogger(__name__)


def game_tick(app: Flask) -> None:
    """One game tick: update all time-varying game systems for every player.

    All writes share one DB commit per tick so timestamps are aligned and the
    state used to gate decisions (radiation vs safe threshold, loyalty vs
    departure rate) is always the *pre-tick* reading.

    Ordering within each tick
    -------------------------
    1. Radiation decay      — based on wall-clock elapsed time.
    2. Loyalty changes      — crank overwork penalty applied first so that
                              a badly overworked crank already shows the
                              disloyalty hit this same tick.
    3. Population departures— uses post-penalty loyalty so that overworking
                              has an immediate effect on resident willingness
                              to leave.
    4. Energy net change    — draw from active systems, generation from crank
                              workers, both scaled by elapsed time.

    All four time series and the BunkerSystems read happen inside a single
    app_context (one connection, one transaction).
    """
    with app.app_context():
        half_life      = app.config["DECAY_HALF_LIFE_SECONDS"]
        rad_threshold  = app.config["RADIATION_SAFE_THRESHOLD"]
        depart_rate    = app.config["BASE_DEPARTURE_RATE"]
        crank_threshold = app.config["CRANK_WORKERS_LOYALTY_THRESHOLD"]
        crank_penalty  = app.config["CRANK_WORKERS_LOYALTY_PENALTY"]
        lights_draw    = app.config["LIGHTS_POWER_DRAW"]        # energy/s
        crank_gen      = app.config["CRANK_POWER_PER_WORKER"]   # energy/s per worker
        now = datetime.now(timezone.utc)

        users = db.session.scalars(select(User)).all()
        if not users:
            return

        processed = 0
        for user in users:
            uid = user.id

            # --- fetch latest time-series readings ---
            latest_rad = db.session.scalars(
                select(RadiationLevel)
                .where(RadiationLevel.user_id == uid)
                .order_by(RadiationLevel.timestamp.desc())
                .limit(1)
            ).first()

            latest_pop = db.session.scalars(
                select(BunkerPopulation)
                .where(BunkerPopulation.user_id == uid)
                .order_by(BunkerPopulation.timestamp.desc())
                .limit(1)
            ).first()

            latest_loy = db.session.scalars(
                select(BunkerLoyalty)
                .where(BunkerLoyalty.user_id == uid)
                .order_by(BunkerLoyalty.timestamp.desc())
                .limit(1)
            ).first()

            latest_energy = db.session.scalars(
                select(EnergyReserve)
                .where(EnergyReserve.user_id == uid)
                .order_by(EnergyReserve.timestamp.desc())
                .limit(1)
            ).first()

            # Current-state control panel (may be None for legacy users).
            systems = db.session.get(BunkerSystems, uid)

            if latest_rad is None or latest_pop is None or latest_loy is None:
                log.warning("user %s is missing seed data, skipping tick", uid)
                continue

            elapsed = (now - latest_rad.timestamp).total_seconds()
            if elapsed <= 0:
                # Clock skew or duplicate run; skip rather than regress any value.
                continue

            # 1. Radiation decay -----------------------------------------------
            new_rad_level = latest_rad.level * (0.5 ** (elapsed / half_life))
            db.session.add(RadiationLevel(user_id=uid, level=new_rad_level, timestamp=now))

            # 2. Loyalty — crank overwork penalty --------------------------------
            new_loyalty = latest_loy.loyalty
            if systems is not None and systems.crank_workers > crank_threshold:
                excess = systems.crank_workers - crank_threshold
                new_loyalty = max(0.0, new_loyalty - excess * crank_penalty)

            # 3. Population departures (gated by pre-tick radiation, post-penalty loyalty)
            current_population = latest_pop.count
            if latest_rad.level < rad_threshold and current_population > 0:
                disloyalty = max(0.0, 1.0 - (new_loyalty / 100.0))
                departed = min(
                    current_population,
                    max(0, round(current_population * disloyalty * depart_rate)),
                )
            else:
                departed = 0

            db.session.add(BunkerPopulation(
                user_id=uid,
                count=current_population - departed,
                departed=departed,
                timestamp=now,
            ))

            # 4. Energy net change -----------------------------------------------
            if latest_energy is not None and systems is not None:
                draw = lights_draw if systems.lights_on else 0.0
                generation = systems.crank_workers * crank_gen
                new_energy = max(0.0, latest_energy.level + (generation - draw) * elapsed)
                db.session.add(EnergyReserve(user_id=uid, level=new_energy, timestamp=now))

            # Write loyalty sample (includes any penalty computed above).
            db.session.add(BunkerLoyalty(user_id=uid, loyalty=new_loyalty, timestamp=now))

            processed += 1

        if processed:
            db.session.commit()
            log.debug("game tick: processed %d user(s)", processed)
