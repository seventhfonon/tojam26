"""End-to-end ``game_tick`` against PostgreSQL (optional in CI)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.constants import ENVIRONMENT_PIXEL_BACKFILL_SAMPLES, FARM_PLOT_COUNT
from app.extensions import db
from app.jobs import game_tick
from app.models import (
    BunkerBoredom,
    BunkerCropPlot,
    BunkerDoubt,
    BunkerFarmingSystem,
    BunkerLightingSystem,
    BunkerLoyalty,
    BunkerPopulation,
    BunkerPowerCrankSystem,
    BunkerProfession,
    BunkerProfessionSnapshot,
    BunkerTheatreSystem,
    EnergyReserve,
    EnvironmentPixelNoiseSample,
    FoodReserve,
    RadiationLevel,
    User,
)
from app.professions import (
    PROFESSION_FARMING,
    PROFESSION_IDLE,
    PROFESSION_INVESTIGATION,
    PROFESSION_POWER_CRANK,
    PROFESSION_RAT_TRAPPING,
    PROFESSION_THEATRE,
)


pytestmark = pytest.mark.integration


@pytest.fixture
def app_ctx():
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
    crank_line = BunkerProfession(
        user_id=uid,
        profession=PROFESSION_POWER_CRANK,
        count=0,
        updated_at=tick_origin,
    )
    farm_line = BunkerProfession(
        user_id=uid,
        profession=PROFESSION_FARMING,
        count=3,
        updated_at=tick_origin,
    )
    idle_line = BunkerProfession(
        user_id=uid,
        profession=PROFESSION_IDLE,
        count=47,
        updated_at=tick_origin,
    )
    investigation_line = BunkerProfession(
        user_id=uid,
        profession=PROFESSION_INVESTIGATION,
        count=0,
        updated_at=tick_origin,
    )
    rat_line = BunkerProfession(
        user_id=uid,
        profession=PROFESSION_RAT_TRAPPING,
        count=0,
        updated_at=tick_origin,
    )
    theatre_line = BunkerProfession(
        user_id=uid,
        profession=PROFESSION_THEATRE,
        count=0,
        updated_at=tick_origin,
    )
    db.session.add_all(
        [crank_line, farm_line, rat_line, theatre_line, idle_line, investigation_line]
    )
    db.session.flush()
    db.session.add(BunkerLightingSystem(user_id=uid, lights_on=True, updated_at=tick_origin))
    db.session.add(
        BunkerPowerCrankSystem(
            user_id=uid,
            profession_line_id=crank_line.id,
            updated_at=tick_origin,
        )
    )
    db.session.add(
        BunkerFarmingSystem(
            user_id=uid,
            profession_line_id=farm_line.id,
            rat_trapper_line_id=rat_line.id,
            updated_at=tick_origin,
        )
    )
    db.session.add(
        BunkerTheatreSystem(
            user_id=uid,
            profession_line_id=theatre_line.id,
            phase="idle",
            phase_entered_at=tick_origin,
            updated_at=tick_origin,
        )
    )
    for pi in range(FARM_PLOT_COUNT):
        db.session.add(
            BunkerCropPlot(user_id=uid, plot_index=pi, crop_ready_at=None),
        )
    db.session.add(BunkerBoredom(user_id=uid, boredom=0.0, timestamp=tick_origin))
    db.session.add(BunkerDoubt(user_id=uid, doubt=0.0, timestamp=tick_origin))
    db.session.commit()

    rad_before = db.session.query(RadiationLevel).filter_by(user_id=uid).count()

    game_tick(app_ctx)

    rad_after = db.session.query(RadiationLevel).filter_by(user_id=uid).count()
    assert rad_after > rad_before, "tick should append a new radiation sample"

    pix_n = db.session.scalar(
        select(func.count()).select_from(EnvironmentPixelNoiseSample).where(
            EnvironmentPixelNoiseSample.user_id == uid
        )
    )
    assert pix_n == ENVIRONMENT_PIXEL_BACKFILL_SAMPLES, (
        "tick replaces raster with a full synthetic history per player"
    )

    latest_ts = db.session.scalar(
        select(func.max(BunkerProfessionSnapshot.timestamp)).where(
            BunkerProfessionSnapshot.user_id == uid
        )
    )
    assert latest_ts is not None
    prof_rows = db.session.scalars(
        select(BunkerProfessionSnapshot).where(
            BunkerProfessionSnapshot.user_id == uid,
            BunkerProfessionSnapshot.timestamp == latest_ts,
        )
    ).all()
    assert len(prof_rows) == 6
    by_prof = {r.profession: r.count for r in prof_rows}
    pop_after = db.session.scalar(
        select(BunkerPopulation.count)
        .where(BunkerPopulation.user_id == uid)
        .order_by(BunkerPopulation.timestamp.desc())
        .limit(1)
    )
    assert sum(by_prof.values()) == pop_after
    assert by_prof[PROFESSION_RAT_TRAPPING] == 0
    assert by_prof[PROFESSION_THEATRE] == 0
    assert by_prof[PROFESSION_FARMING] == 3
    assert by_prof[PROFESSION_POWER_CRANK] == 0
    assert by_prof[PROFESSION_INVESTIGATION] == 0
    assert by_prof[PROFESSION_IDLE] == pop_after - 3
