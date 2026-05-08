"""Flavor text for social actions (council meetings, etc.)."""

from __future__ import annotations

# Shown in the system message log when inner_circle_loyalty changes upward.
POSITIVE_COUNCIL_MESSAGES: tuple[str, ...] = (
    "The meeting went well.",
    "You left the chamber with a quiet nod from the council.",
    "Several members warmed to your proposal.",
    "The air in the room felt lighter on the way out.",
)

# Shown when inner_circle_loyalty moves downward.
NEGATIVE_COUNCIL_MESSAGES: tuple[str, ...] = (
    "The council members disagreed.",
    "Sideways glances followed you to the door.",
    "The vote was not in your favor.",
    "You were asked to clarify your priorities next time.",
)
