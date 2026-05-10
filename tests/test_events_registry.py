"""Sanity checks for the code-defined event registry."""


from __future__ import annotations


def test_rats_events_registered():
    from datetime import datetime, timezone

    from app.events import (
        EventDefinition,
        EVENTS_BY_DEFINITION,
        REGISTERED_EVENTS,
    )

    uid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    tick = datetime(2026, 5, 8, 12, 0, 0, tzinfo=timezone.utc)

    assert EventDefinition.RATS_SILO.value == "rats_silo"
    assert EventDefinition.RATS_SILO_INTRO.value == "rats_silo_intro"
    assert REGISTERED_EVENTS, "REGISTERED_EVENTS must not be empty"
    assert EventDefinition.RATS_SILO_INTRO.value in EVENTS_BY_DEFINITION
    assert EventDefinition.RATS_SILO.value in EVENTS_BY_DEFINITION
    intro = EVENTS_BY_DEFINITION[EventDefinition.RATS_SILO_INTRO.value]
    assert intro.tick_effects(uid, tick).food_consumption_multiplier == 1.0
    assert intro.system == "farming"
    spec = EVENTS_BY_DEFINITION[EventDefinition.RATS_SILO.value]
    auto_out = spec.auto_resolve(uid, tick)
    player_out = spec.player_resolve(uid, tick)
    assert auto_out.message
    assert player_out.message


def test_game_event_specs_carry_tuning_on_spec():
    """Random-event spawn/sim tuning lives on GameEventSpec; sweep sizing lives in constants."""
    from datetime import datetime, timezone

    from app.constants import INVESTIGATION_DISPATCH_BY_SYSTEM
    from app.events import REGISTERED_EVENTS

    tick = datetime(2026, 5, 8, 12, 0, 0, tzinfo=timezone.utc)
    uid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    for spec in REGISTERED_EVENTS:
        assert 0.0 <= spec.spawn_chance_per_tick <= 1.0
        assert spec.duration_seconds is None or spec.duration_seconds > 0
        fx = spec.tick_effects(uid, tick)
        assert fx.food_consumption_multiplier > 0
        assert callable(spec.tick_effects)
        assert callable(spec.can_spawn)
        assert callable(spec.auto_resolve)
        assert callable(spec.player_resolve)
        assert callable(spec.spawn_announcement)
        assert callable(spec.on_spawn)
        auto_out = spec.auto_resolve(uid, tick)
        player_out = spec.player_resolve(uid, tick)
        assert isinstance(auto_out.loyalty_delta, float)
        assert isinstance(player_out.loyalty_delta, float)
        assert isinstance(auto_out.message, str)
        assert isinstance(player_out.message, str)

    for cfg in INVESTIGATION_DISPATCH_BY_SYSTEM.values():
        assert cfg.team_size > 0
        assert cfg.duration_seconds > 0


def test_investigation_dispatch_defined_for_each_game_system():
    from app.constants import GAME_SYSTEM_IDS, INVESTIGATION_DISPATCH_BY_SYSTEM

    assert set(INVESTIGATION_DISPATCH_BY_SYSTEM.keys()) == set(GAME_SYSTEM_IDS)
