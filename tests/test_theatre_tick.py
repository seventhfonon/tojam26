"""Theatre tick cadence (phase boundaries for Grafana / UI)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app import constants
from app.jobs import handle_theatre_tick


def test_ready_performance_rotation_returns_to_planning():
    """After a show ends, cycle back to writing (planning) for the next title."""
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    due = t0 + timedelta(seconds=constants.THEATRE_PERFORMANCE_INTERVAL_SECONDS)
    theatre = SimpleNamespace(
        phase=constants.THEATRE_PHASE_READY,
        phase_entered_at=t0,
        next_performance_at=due,
        play_index=0,
        updated_at=None,
    )
    handle_theatre_tick(
        theatre,
        actor_count=1,
        tick_time=due,
        energy_level=1.0,
        elapsed_seconds=1.0,
    )
    assert theatre.phase == constants.THEATRE_PHASE_WRITING
    assert theatre.phase_entered_at == due
    assert theatre.next_performance_at is None
    assert theatre.play_index == 1
