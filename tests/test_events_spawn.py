"""Spawn-path behavior for random events (mocked DB + RNG)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app import constants
from app.events import (
    EVENTS_BY_DEFINITION,
    EventDefinition,
    _rats_silo_intro_after_player_resolve,
    try_spawn_event,
)
from app.models import PlayerActiveEvent, SystemMessage, User


def _fake_kind_active_factory(captured: list[object]):
    def fake_has_kind(user_id: str, definition: EventDefinition) -> bool:
        return any(
            isinstance(o, PlayerActiveEvent)
            and str(o.kind) == definition.value
            for o in captured
        )

    return fake_has_kind


def test_try_spawn_event_intro_first_visit_when_eligible_and_roll_succeeds():
    """Before resident rats exist only ``rats_silo_intro`` may enqueue."""
    spec = EVENTS_BY_DEFINITION[EventDefinition.RATS_SILO_INTRO.value]
    uid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    tick = datetime(2026, 5, 8, 12, 0, 0, tzinfo=timezone.utc)

    mock_session = MagicMock()
    captured: list[object] = []

    user_row = MagicMock()
    user_row.silo_rats_introduced = False
    user_row.rat_background_consumption_ps = 0.0

    def session_get(cls: object, pk: object) -> object | None:
        if cls is User:
            assert pk == uid
            return user_row
        return None

    mock_session.get.side_effect = session_get
    mock_session.add = captured.append

    with patch("app.events.db.session", mock_session):
        with patch(
            "app.events.player_has_active_event_kind",
            side_effect=_fake_kind_active_factory(captured),
        ):
            with patch("app.events.random.random", side_effect=[0.0, 1.0]):
                try_spawn_event(uid, 100.0, 20, 0, tick)

    assert user_row.silo_rats_introduced is True
    assert float(user_row.rat_background_consumption_ps) == pytest.approx(
        float(constants.RAT_BACKGROUND_INITIAL_DRAIN_PS)
    )

    active = [o for o in captured if isinstance(o, PlayerActiveEvent)]
    assert len(active) == 1
    row = active[0]
    assert row.user_id == uid
    assert row.kind == EventDefinition.RATS_SILO_INTRO
    assert row.started_at == tick
    assert row.auto_resolve_at is None
    assert row.system == spec.system

    msgs = [o for o in captured if isinstance(o, SystemMessage)]
    announce = spec.spawn_announcement(uid, tick)
    gc_body = (
        spec.spawn_group_chat_announcement(uid, tick)
        if spec.spawn_group_chat_announcement is not None
        else None
    )
    assert len(msgs) == (1 if announce else 0) + (1 if gc_body else 0)
    by_ch = {m.channel: m for m in msgs}
    if announce:
        assert by_ch[constants.MESSAGE_CHANNEL_BULLETIN].body == announce
    if gc_body:
        assert by_ch[constants.MESSAGE_CHANNEL_GROUP_CHAT].body == gc_body


def test_try_spawn_event_spike_after_intro_when_eligible():
    """With infestation active, registry advances to ``rats_silo`` swarm checks."""
    spec = EVENTS_BY_DEFINITION[EventDefinition.RATS_SILO.value]
    uid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    tick = datetime(2026, 5, 8, 12, 0, 0, tzinfo=timezone.utc)

    mock_session = MagicMock()
    captured: list[object] = []

    user_row = MagicMock()
    user_row.silo_rats_introduced = True
    user_row.rat_background_consumption_ps = 0.0

    def session_get(cls: object, pk: object) -> object | None:
        if cls is User:
            assert pk == uid
            return user_row
        return None

    mock_session.get.side_effect = session_get
    mock_session.add = captured.append

    with patch("app.events.db.session", mock_session):
        with patch(
            "app.events.player_has_active_event_kind",
            side_effect=_fake_kind_active_factory(captured),
        ):
            with patch("app.events.random.random", return_value=0.0):
                try_spawn_event(uid, 100.0, 20, 0, tick)

    active = [o for o in captured if isinstance(o, PlayerActiveEvent)]
    assert len(active) == 1
    row = active[0]
    assert row.kind == EventDefinition.RATS_SILO
    assert row.auto_resolve_at == tick + timedelta(seconds=spec.duration_seconds)


def test_try_spawn_event_intro_and_spike_same_tick_when_both_roll():
    """Multiple specs may spawn in one tick when RNG succeeds for each."""
    uid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    tick = datetime(2026, 5, 8, 12, 0, 0, tzinfo=timezone.utc)

    mock_session = MagicMock()
    captured: list[object] = []

    user_row = MagicMock()
    user_row.silo_rats_introduced = False
    user_row.rat_background_consumption_ps = 0.0

    def session_get(cls: object, pk: object) -> object | None:
        if cls is User:
            return user_row
        return None

    mock_session.get.side_effect = session_get
    mock_session.add = captured.append

    with patch("app.events.db.session", mock_session):
        with patch(
            "app.events.player_has_active_event_kind",
            side_effect=_fake_kind_active_factory(captured),
        ):
            with patch("app.events.random.random", return_value=0.0):
                try_spawn_event(uid, 100.0, 20, 0, tick)

    active = [o for o in captured if isinstance(o, PlayerActiveEvent)]
    kinds = {str(o.kind) for o in active}
    assert EventDefinition.RATS_SILO_INTRO.value in kinds
    assert EventDefinition.RATS_SILO.value in kinds
    assert len(active) == 2


def test_try_spawn_skips_kind_already_present():
    """No second row for the same active definition."""
    uid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    tick = datetime(2026, 5, 8, 12, 0, 0, tzinfo=timezone.utc)

    existing = PlayerActiveEvent(
        id=str(uuid4()),
        user_id=uid,
        kind=EventDefinition.RATS_SILO_INTRO,
        started_at=tick,
        auto_resolve_at=None,
        system="farming",
    )
    captured: list[object] = [existing]

    mock_session = MagicMock()
    user_row = MagicMock()
    user_row.silo_rats_introduced = False
    user_row.rat_background_consumption_ps = 0.0

    def session_get(cls: object, pk: object) -> object | None:
        if cls is User:
            return user_row
        return None

    mock_session.get.side_effect = session_get
    mock_session.add = captured.append

    with patch("app.events.db.session", mock_session):
        with patch(
            "app.events.player_has_active_event_kind",
            side_effect=_fake_kind_active_factory(captured),
        ):
            with patch("app.events.random.random", return_value=0.0):
                try_spawn_event(uid, 100.0, 20, 0, tick)

    intros = [
        o
        for o in captured
        if isinstance(o, PlayerActiveEvent)
        and str(o.kind) == EventDefinition.RATS_SILO_INTRO.value
    ]
    assert len(intros) == 1


def test_intro_player_resolve_does_not_unlock_trappers_without_focus():
    uid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    tick = datetime(2026, 5, 8, 12, 0, 0, tzinfo=timezone.utc)
    user_row = MagicMock()
    user_row.rat_trappers_unlocked = False
    mock_session = MagicMock()
    mock_session.get.return_value = user_row

    with patch("app.events.db.session", mock_session):
        _rats_silo_intro_after_player_resolve(uid, tick)

    assert user_row.rat_trappers_unlocked is False


def test_try_spawn_event_rats_suppressed_when_trapper_output_covers_margin():
    """Enough rat trappers vs combined drain block ``rats_silo`` even on lucky roll."""
    uid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    tick = datetime(2026, 5, 8, 12, 0, 0, tzinfo=timezone.utc)

    mock_session = MagicMock()
    captured: list[object] = []

    user_row = MagicMock()
    user_row.silo_rats_introduced = True
    user_row.rat_background_consumption_ps = 36.0

    def session_get(cls: object, pk: object) -> object | None:
        if cls is User:
            return user_row
        return None

    mock_session.get.side_effect = session_get
    mock_session.add = captured.append

    with patch("app.events.db.session", mock_session):
        with patch("app.events.player_has_active_event_kind", return_value=False):
            with patch("app.events.random.random", return_value=0.0):
                try_spawn_event(uid, 100.0, 20, 1, tick)

    active = [o for o in captured if isinstance(o, PlayerActiveEvent)]
    assert len(active) == 0
