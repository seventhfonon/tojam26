"""Pure helpers for entertainment / loyalty tuning."""

from __future__ import annotations

from app import constants
from app.jobs import boredom_loyalty_drag, movie_exhaustion_loyalty_drag


def test_movies_catalog_non_empty():
    assert len(constants.MOVIES) >= 1
    assert constants.MOVIES_BY_ID.keys() == {m.id for m in constants.MOVIES}


def test_boredom_loyalty_drag_zero_when_not_bored():
    assert boredom_loyalty_drag(0.0, 30.0) == 0.0


def test_boredom_loyalty_drag_scales_with_boredom():
    low = boredom_loyalty_drag(50.0, 10.0)
    high = boredom_loyalty_drag(100.0, 10.0)
    assert high > low > 0


def test_movie_exhaustion_loyalty_drag_scales():
    assert movie_exhaustion_loyalty_drag(0.0, 5.0) == 0.0
    assert movie_exhaustion_loyalty_drag(49.0, 10.0) == 0.0
    assert movie_exhaustion_loyalty_drag(100.0, 10.0) > movie_exhaustion_loyalty_drag(
        75.0, 10.0
    )
    assert movie_exhaustion_loyalty_drag(75.0, 10.0) > 0


def test_parse_system_message_body_prefixes():
    from app.constants import (
        SYSTEM_MESSAGE_URGENCY_ALERT,
        SYSTEM_MESSAGE_URGENCY_INFO,
        SYSTEM_MESSAGE_URGENCY_WARNING,
        parse_system_message_body,
        system_message_urgency_emoji,
    )

    assert parse_system_message_body("plain") == (SYSTEM_MESSAGE_URGENCY_INFO, "plain")
    assert parse_system_message_body("!Watch") == (SYSTEM_MESSAGE_URGENCY_ALERT, "Watch")
    assert parse_system_message_body("!!Stop") == (SYSTEM_MESSAGE_URGENCY_WARNING, "Stop")
    assert parse_system_message_body("!!!x") == (
        SYSTEM_MESSAGE_URGENCY_WARNING,
        "!x",
    )

    a = system_message_urgency_emoji(SYSTEM_MESSAGE_URGENCY_INFO)
    b = system_message_urgency_emoji(SYSTEM_MESSAGE_URGENCY_ALERT)
    c = system_message_urgency_emoji(SYSTEM_MESSAGE_URGENCY_WARNING)
    assert len({a, b, c}) == 3


def test_system_message_urgency_emoji_unknown_key_defaults():
    from app.constants import SYSTEM_MESSAGE_URGENCY_INFO, system_message_urgency_emoji

    assert system_message_urgency_emoji("nope") == system_message_urgency_emoji(
        SYSTEM_MESSAGE_URGENCY_INFO
    )
