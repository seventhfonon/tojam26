"""Background jobs run by APScheduler."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from flask import Flask
from sqlalchemy import select

from .extensions import db
from .models import RadiationLevel, User


log = logging.getLogger(__name__)


def decay_radiation(app: Flask) -> None:
    """Append a decayed radiation sample for every player.

    Uses exponential decay against actual wall-clock elapsed time since the
    user's last sample::

        level(now) = last_level * 0.5 ** (elapsed_seconds / half_life)

    Going off real elapsed time (rather than the configured tick interval) means
    the radiation level keeps "decaying" correctly even across server restarts,
    scheduler jitter, or extended downtime — fitting for a game in which the
    world is recovering whether the bunker's monitoring computer is on or off.

    A new row is written each tick (rather than updating in place) so that
    Grafana renders a smooth time series rather than a single moving value.
    """
    with app.app_context():
        half_life = app.config["DECAY_HALF_LIFE_SECONDS"]
        now = datetime.now(timezone.utc)

        users = db.session.scalars(select(User)).all()
        if not users:
            return

        inserted = 0
        for user in users:
            latest = db.session.scalars(
                select(RadiationLevel)
                .where(RadiationLevel.user_id == user.id)
                .order_by(RadiationLevel.timestamp.desc())
                .limit(1)
            ).first()
            if latest is None:
                continue

            elapsed = (now - latest.timestamp).total_seconds()
            if elapsed <= 0:
                continue  # clock skew or a duplicate run; skip rather than write a regression

            new_level = latest.level * (0.5 ** (elapsed / half_life))
            db.session.add(
                RadiationLevel(
                    user_id=user.id,
                    level=new_level,
                    timestamp=now,
                )
            )
            inserted += 1

        if inserted:
            db.session.commit()
            log.debug("decay tick: wrote %d sample(s)", inserted)
