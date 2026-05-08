"""Scripted narrative lines tied to game-state triggers.

Message definitions live in code (including callable triggers). The database
only records which ``(user_id, message_id)`` pairs have already been delivered,
so each line fires at most once per player.

To add a new line: append a :class:`NarrativeMessage` to ``NARRATIVE_MESSAGES``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from sqlalchemy import select

from .extensions import db
from .models import SystemMessage, UserNarrativeDelivery


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class NarrativeContext:
    """Read-only snapshot passed into each trigger function."""

    user_id: str
    tick_time: datetime
    departed_this_tick: int
    had_prior_departure_event: bool
    had_prior_welcome_message: bool


@dataclass(frozen=True)
class NarrativeMessage:
    """A single deliver-once story beat."""

    id: str
    text: str
    trigger: Callable[[NarrativeContext], bool]


def _trigger_first_departure_notice(ctx: NarrativeContext) -> bool:
    """True the first time anyone leaves the bunker (this tick, first ever)."""
    return ctx.departed_this_tick > 0 and not ctx.had_prior_departure_event


def _trigger_welcome_message(ctx: NarrativeContext) -> bool:
    """True the first time anyone reads this message."""
    return not ctx.had_prior_welcome_message


NARRATIVE_MESSAGES: tuple[NarrativeMessage, ...] = (
    NarrativeMessage(
        id="first_departure_notice",
        text=(
            "A community member has decided to brave the outdoors and leave the bunker. "
            "More may follow."
        ),
        trigger=_trigger_first_departure_notice,
    ),
    NarrativeMessage(
        id="welcome_message",
        text=(
            "Welcome to Bunker.OS 1.2.0. ",
            "If you are reading this message, a nuclear apocalypse has occurred. ",
            "It is not safe to go outside."
        ),
        trigger=_trigger_welcome_message,
    ),
)


def deliver_pending_narrative_messages(ctx: NarrativeContext) -> None:
    """Fire any narrative lines whose triggers pass and which are not yet logged."""
    for message in NARRATIVE_MESSAGES:
        already = db.session.scalar(
            select(UserNarrativeDelivery.id)
            .where(
                UserNarrativeDelivery.user_id == ctx.user_id,
                UserNarrativeDelivery.message_id == message.id,
            )
            .limit(1)
        )
        if already is not None:
            continue
        if not message.trigger(ctx):
            continue

        db.session.add(
            SystemMessage(user_id=ctx.user_id, body=message.text, timestamp=ctx.tick_time)
        )
        db.session.add(
            UserNarrativeDelivery(
                user_id=ctx.user_id,
                message_id=message.id,
                delivered_at=ctx.tick_time,
            )
        )
        log.info(
            "narrative delivered: user=%s message_id=%s",
            ctx.user_id,
            message.id,
        )
