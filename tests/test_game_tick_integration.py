"""End-to-end ``game_tick`` against PostgreSQL (optional in CI)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.extensions import db
from app.jobs import game_tick
from app.models import (
    BunkerLoyalty,
    BunkerPopulation,
    BunkerSystems,
    EnergyReserve,
    FoodReserve,
    RadiationLevel,
    User,
)


pytestmark = pytest.mark.integration


@pytest.fixture
def app_ctx():
    from sqlalchemy.exc import OperationalError

    from app import create_app

    try:
        flask_app = create_app()
    except OperationalError as exc:
        pytest.skip(f"PostgreSQL not reachable (DATABASE_URL): {exc}")

    with flask_app.app_context():
        yield flask_app


def test_game_tick_commits_simulation_sample(app_ctx):
    uid = str(uuid4())
    tick_origin = datetime.now(timezone.utc) - timedelta(seconds=10)

    u = User(id=uid)
    db.session.add(u)
    db.session.add(
        RadiationLevel(
            user_id=uid,
            level=100.0,
            level_display=100.0,
            timestamp=tick_origin,
        )
    )
    db.session.add(
        BunkerPopulation(
            user_id=uid,
            count=50,
            departed=0,
            timestamp=tick_origin,
        )
    )
    db.session.add(
        BunkerLoyalty(user_id=uid, loyalty=88.0, timestamp=tick_origin)
    )
    db.session.add(EnergyReserve(user_id=uid, level=50.0, timestamp=tick_origin))
    db.session.add(
        FoodReserve(
            user_id=uid,
            level=400.0,
            consumption_per_second=0.0,
            production_per_second=0.0,
            timestamp=tick_origin,
        )
    )
    db.session.add(
        BunkerSystems(
            user_id=uid,
            lights_on=True,
            crank_workers=0,
            food_workers=3,
        )
    )
    db.session.commit()

    rad_before = db.session.query(RadiationLevel).filter_by(user_id=uid).count()

    game_tick(app_ctx)

    rad_after = db.session.query(RadiationLevel).filter_by(user_id=uid).count()
    assert rad_after > rad_before, "tick should append a new radiation sample"
