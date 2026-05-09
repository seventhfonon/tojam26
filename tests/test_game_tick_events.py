"""How ``game_tick`` derives elapsed time affects random-event rolls.

``game_tick`` bails out of the per-user loop when ``elapsed_seconds_for_game_tick``
returns ``None``. Random events are rolled later in that same body
(``try_spawn_event``), so that helper must **not** treat "no simulated radiation
progress yet" (zero age) as "skip the entire tick". Same-wall-clock ticks are
normal at DECAY_TICK_SECONDS intervals.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.jobs import elapsed_seconds_for_game_tick


class TestGameTickElapsedAndRandomEvents:
    def test_zero_radiation_age_still_runs_tick_body_contract(self):
        """Regression: zero elapsed must not become ``None`` (which skips ``try_spawn_event``)."""
        tick_time = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
        rad = SimpleNamespace(timestamp=tick_time)
        elapsed = elapsed_seconds_for_game_tick(tick_time, rad)
        assert elapsed is not None
        assert elapsed == 0.0

    def test_positive_age_matches_wall_clock_delta(self):
        t_rad = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
        t_tick = t_rad + timedelta(seconds=3.25)
        rad = SimpleNamespace(timestamp=t_rad)
        assert elapsed_seconds_for_game_tick(t_tick, rad) == 3.25

    def test_negative_age_returns_none_like_game_tick_skip(self):
        t_tick = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
        t_rad = t_tick + timedelta(seconds=1)
        rad = SimpleNamespace(timestamp=t_rad)
        assert elapsed_seconds_for_game_tick(t_tick, rad) is None
