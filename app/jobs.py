"""Background jobs run by APScheduler."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from flask import Flask
from sqlalchemy import select

from .extensions import db
from .models import BunkerLoyalty, BunkerPopulation, RadiationLevel, User


log = logging.getLogger(__name__)


def game_tick(app: Flask) -> None:
    """One game tick: decay radiation then update bunker population.

    Both systems share the same DB commit so their timestamps are aligned and
    the radiation reading used to gate departures is always consistent with the
    pre-tick value (i.e. what the bunker sensors showed at the *start* of this
    interval).

    Radiation decay
    ---------------
    Uses exponential decay against actual wall-clock elapsed time::

        level(now) = last_level × 0.5^(elapsed / half_life)

    Going off real elapsed time means the level stays correct across server
    restarts, scheduler jitter, or extended downtime.

    Population departures
    ---------------------
    Each tick, if the current outdoor radiation is below RADIATION_SAFE_THRESHOLD,
    some residents decide to leave based on their collective disloyalty::

        departures = min(population, max(0, round(population × (1 − loyalty/100) × BASE_DEPARTURE_RATE)))

    At full loyalty (100) nobody leaves regardless of radiation. At zero loyalty
    with defaults, departure rate is 5 % of the remaining population per tick.
    The formula is deterministic so the decay curve is smooth and predictable
    in Grafana without any noise.

    A loyalty sample is also appended every tick so the Grafana time series is
    continuous even before any player actions exist to change the value.
    """
    with app.app_context():
        half_life = app.config["DECAY_HALF_LIFE_SECONDS"]
        threshold = app.config["RADIATION_SAFE_THRESHOLD"]
        departure_rate = app.config["BASE_DEPARTURE_RATE"]
        now = datetime.now(timezone.utc)

        users = db.session.scalars(select(User)).all()
        if not users:
            return

        processed = 0
        for user in users:
            uid = user.id

            # --- fetch latest readings for this user ---
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

            if latest_rad is None or latest_pop is None or latest_loy is None:
                log.warning("user %s is missing seed data, skipping tick", uid)
                continue

            # --- radiation decay ---
            elapsed = (now - latest_rad.timestamp).total_seconds()
            if elapsed <= 0:
                # Clock skew or duplicate run; don't regress the value.
                continue

            new_rad_level = latest_rad.level * (0.5 ** (elapsed / half_life))
            db.session.add(RadiationLevel(user_id=uid, level=new_rad_level, timestamp=now))

            # --- population departures (gated by pre-tick radiation) ---
            current_population = latest_pop.count
            current_loyalty = latest_loy.loyalty

            if latest_rad.level < threshold and current_population > 0:
                disloyalty = max(0.0, 1.0 - (current_loyalty / 100.0))
                departed = min(
                    current_population,
                    max(0, round(current_population * disloyalty * departure_rate)),
                )
            else:
                departed = 0

            new_population = current_population - departed
            db.session.add(
                BunkerPopulation(
                    user_id=uid,
                    count=new_population,
                    departed=departed,
                    timestamp=now,
                )
            )

            # --- loyalty sample (value unchanged for now; written for continuity) ---
            db.session.add(BunkerLoyalty(user_id=uid, loyalty=current_loyalty, timestamp=now))

            processed += 1

        if processed:
            db.session.commit()
            log.debug("game tick: processed %d user(s)", processed)
