"""Spawn-path behaviour for random events (mocked DB + RNG)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from app.events import EventSpawnContext, RATS_SILO_KIND, try_spawn_event
from app.models import PlayerActiveEvent, SystemMessage


def test_try_spawn_event_fires_when_eligible_and_roll_succeeds():
    """Eligible context + RNG below spawn_chance must enqueue an active row."""
    uid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    tick = datetime(2026, 5, 8, 12, 0, 0, tzinfo=timezone.utc)
    ctx = EventSpawnContext(latest_food_level=100.0, population_count=20)

    mock_session = MagicMock()
    mock_session.get.return_value = None
    captured: list[object] = []
    mock_session.add = captured.append

    with patch("app.events.db.session", mock_session):
        with patch("app.events.random.random", return_value=0.0):
            try_spawn_event(uid, ctx, tick)

    active = [o for o in captured if isinstance(o, PlayerActiveEvent)]
    assert len(active) == 1
    row = active[0]
    assert row.user_id == uid
    assert row.kind == RATS_SILO_KIND
    assert row.started_at == tick
    assert row.auto_resolve_at == tick + timedelta(seconds=60)

    msgs = [o for o in captured if isinstance(o, SystemMessage)]
    assert len(msgs) == 1
    assert "ALERT" in msgs[0].body
