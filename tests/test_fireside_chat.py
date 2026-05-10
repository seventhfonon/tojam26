"""Fireside Chat completion + rhetoric backlash (PostgreSQL integration)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError

from app import constants
from app.extensions import db
from app.events import EventDefinition
from app.jobs import _fireside_broadcast_overlap_fraction, game_tick
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
    BunkerTheatreSystem,
    EnergyReserve,
    FoodReserve,
    PlayerActiveEvent,
    RadiationLevel,
    SystemMessage,
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

integration = pytest.mark.integration


@pytest.fixture
def app_ctx():
    from app import create_app

    try:
        flask_app = create_app()
    except OperationalError as exc:
        pytest.skip(f"PostgreSQL not reachable (DATABASE_URL): {exc}")
    except IntegrityError as exc:
        pytest.skip(f"Database schema/migration incompatible with create_app: {exc}")

    with flask_app.app_context():
        yield flask_app


def test_fireside_broadcast_overlap_fraction_covers_full_window_in_one_tick():
    tick = datetime(2026, 1, 1, 12, 0, 30, tzinfo=timezone.utc)
    broadcast_end = tick
    elapsed = 30.0
    frac = _fireside_broadcast_overlap_fraction(
        elapsed, tick, broadcast_end, 30.0
    )
    assert abs(frac - 1.0) < 1e-9


def test_fireside_broadcast_overlap_fraction_partial_mid_chat():
    broadcast_end = datetime(2026, 1, 1, 12, 1, 0, tzinfo=timezone.utc)
    tick = datetime(2026, 1, 1, 12, 0, 50, tzinfo=timezone.utc)
    elapsed = 10.0
    frac = _fireside_broadcast_overlap_fraction(
        elapsed, tick, broadcast_end, 30.0
    )
    assert abs(frac - 10.0 / 30.0) < 1e-6


def test_enqueue_fireside_rhetoric_backlash_inserts_event_and_warning_message():
    """Pure unit test: manual backlash enqueue (no Flask app / DB required)."""
    from unittest.mock import MagicMock, patch

    from datetime import datetime, timezone

    from app.events import (
        EventDefinition,
        enqueue_fireside_rhetoric_backlash,
    )
    from app.models import PlayerActiveEvent, SystemMessage

    uid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    tick = datetime(2026, 5, 9, 12, 0, 0, tzinfo=timezone.utc)

    mock_session = MagicMock()
    captured: list[object] = []

    mock_session.add = captured.append
    mock_session.scalars.return_value.first.return_value = None

    with patch("app.events.db.session", mock_session):
        enqueue_fireside_rhetoric_backlash(uid, tick)

    active = [o for o in captured if isinstance(o, PlayerActiveEvent)]
    assert len(active) == 1
    assert active[0].kind == EventDefinition.FIRESIDE_RHETORIC_BACKLASH
    msgs = [o for o in captured if isinstance(o, SystemMessage)]
    assert len(msgs) == 2
    bulletin = next(m for m in msgs if m.channel == constants.MESSAGE_CHANNEL_BULLETIN)
    assert "holes in your speech" in bulletin.body
    gc = next(m for m in msgs if m.channel == constants.MESSAGE_CHANNEL_GROUP_CHAT)
    assert len(gc.body) > 0


def _seed_minimal_player(uid: str, tick_origin: datetime) -> None:
    """Enough rows for ``fetch_game_tick_readings_for_user`` + facility hooks."""
    from app.constants import FARM_PLOT_COUNT

    db.session.add(User(id=uid))
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
            play_index=0,
            phase_entered_at=tick_origin,
            updated_at=tick_origin,
        )
    )
    for pi in range(FARM_PLOT_COUNT):
        db.session.add(BunkerCropPlot(user_id=uid, plot_index=pi, crop_ready_at=None))
    db.session.add(BunkerBoredom(user_id=uid, boredom=0.0, timestamp=tick_origin))
    db.session.add(BunkerDoubt(user_id=uid, doubt=10.0, timestamp=tick_origin))
    db.session.commit()


@integration
def test_fireside_reassuring_adds_loyalty_when_window_elapses(app_ctx):
    uid = str(uuid4())
    tick_origin = datetime.now(timezone.utc) - timedelta(seconds=10)
    _seed_minimal_player(uid, tick_origin)

    user = db.session.get(User, uid)
    assert user is not None
    done_at = datetime.now(timezone.utc) - timedelta(seconds=2)
    user.fireside_busy_until = done_at
    user.fireside_pending_kind = constants.FIRESIDE_KIND_REASSURING
    db.session.commit()

    game_tick(app_ctx)

    latest_loyalty = db.session.scalars(
        select(BunkerLoyalty.loyalty)
        .where(BunkerLoyalty.user_id == uid)
        .order_by(BunkerLoyalty.timestamp.desc())
        .limit(1)
    ).first()
    assert latest_loyalty is not None
    assert float(latest_loyalty) >= 88.0 + float(constants.FIRESIDE_REASSURING_LOYALTY_DELTA) - 1.5

    user_after = db.session.get(User, uid)
    assert user_after is not None
    assert user_after.fireside_busy_until is None
    assert user_after.fireside_pending_kind is None


@integration
def test_fireside_fearmonger_backfire_enqueues_event(app_ctx):
    uid = str(uuid4())
    tick_origin = datetime.now(timezone.utc) - timedelta(seconds=10)
    _seed_minimal_player(uid, tick_origin)

    user = db.session.get(User, uid)
    assert user is not None
    done_at = datetime.now(timezone.utc) - timedelta(seconds=2)
    user.fireside_busy_until = done_at
    user.fireside_pending_kind = constants.FIRESIDE_KIND_FEARMONGERING
    db.session.commit()

    with patch("app.jobs.random.random", return_value=0.0):
        game_tick(app_ctx)

    ev = db.session.scalars(
        select(PlayerActiveEvent).where(
            PlayerActiveEvent.user_id == uid,
            PlayerActiveEvent.kind == EventDefinition.FIRESIDE_RHETORIC_BACKLASH.value,
        )
    ).first()
    assert ev is not None

    msg = db.session.scalars(
        select(SystemMessage)
        .where(
            SystemMessage.user_id == uid,
            SystemMessage.channel == constants.MESSAGE_CHANNEL_BULLETIN,
        )
        .order_by(SystemMessage.timestamp.desc())
        .limit(1)
    ).first()
    assert msg is not None
    assert "holes in your speech" in msg.body
