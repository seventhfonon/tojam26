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


def test_game_event_specs_carry_tuning_on_spec():
    """Tuning lives on GameEventSpec, not Flask config / constants."""
    from app.events import REGISTERED_EVENTS

    for spec in REGISTERED_EVENTS:
        assert 0.0 <= spec.spawn_chance_per_tick <= 1.0
        assert spec.duration_seconds > 0
        assert spec.food_consumption_multiplier > 0
        assert isinstance(spec.loyalty_delta_auto, float)
        assert isinstance(spec.loyalty_delta_player, float)
        assert callable(spec.eligible)
