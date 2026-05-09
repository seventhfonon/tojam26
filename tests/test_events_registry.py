"""Sanity checks for the code-defined event registry."""


from __future__ import annotations


def test_rats_event_registered():
    from app.events import EVENTS_BY_KIND, RATS_SILO_KIND, REGISTERED_EVENTS

    assert RATS_SILO_KIND == "rats_silo"
    assert REGISTERED_EVENTS, "REGISTERED_EVENTS must not be empty"
    assert RATS_SILO_KIND in EVENTS_BY_KIND
    spec = EVENTS_BY_KIND[RATS_SILO_KIND]
    assert spec.resolve_message_auto
    assert spec.resolve_message_player
    assert spec.system == "farming"


def test_game_event_specs_carry_tuning_on_spec():
    """Random-event spawn/sim tuning lives on GameEventSpec; sweep sizing lives in constants."""
    from app.constants import INVESTIGATION_DISPATCH_BY_SYSTEM
    from app.events import REGISTERED_EVENTS

    for spec in REGISTERED_EVENTS:
        assert 0.0 <= spec.spawn_chance_per_tick <= 1.0
        assert spec.duration_seconds > 0
        assert spec.food_consumption_multiplier > 0
        assert isinstance(spec.loyalty_delta_auto, float)
        assert isinstance(spec.loyalty_delta_player, float)
        assert callable(spec.eligible)

    for cfg in INVESTIGATION_DISPATCH_BY_SYSTEM.values():
        assert cfg.team_size > 0
        assert cfg.duration_seconds > 0


def test_investigation_dispatch_defined_for_each_game_system():
    from app.constants import GAME_SYSTEM_IDS, INVESTIGATION_DISPATCH_BY_SYSTEM

    assert set(INVESTIGATION_DISPATCH_BY_SYSTEM.keys()) == set(GAME_SYSTEM_IDS)
