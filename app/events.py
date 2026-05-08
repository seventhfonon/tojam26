"""Random gameplay events: spawn conditions, active row, resolve paths.

Definitions live in code; ``player_active_events`` holds at most one active
event per player. Tunables for each event live on :class:`GameEventSpec`.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from .extensions import db
from .models import BunkerLoyalty, PlayerActiveEvent, SystemMessage

log = logging.getLogger(__name__)

RATS_SILO_KIND = "rats_silo"


@dataclass(frozen=True)
class EventSpawnContext:
    """Snapshot used only to evaluate whether an event may spawn."""

    latest_food_level: float
    population_count: int


@dataclass(frozen=True)
class GameEventSpec:
    """Code-defined event; all tuning for this event stays on this object."""

    kind: str
    spawn_chance_per_tick: float
    duration_seconds: int
    food_consumption_multiplier: float
    loyalty_delta_auto: float
    loyalty_delta_player: float
    announce_on_start: bool
    start_message: str | None
    resolve_message_auto: str
    resolve_message_player: str
    spawn_min_food_level: float | None
    spawn_min_population: int | None
    eligible: Callable[["EventSpawnContext", "GameEventSpec"], bool]


def spawn_threshold_eligible(ctx: EventSpawnContext, spec: GameEventSpec) -> bool:
    """Eligible when food/pop meet optional floors (either may be omitted)."""
    if spec.spawn_min_food_level is not None:
        if ctx.latest_food_level < spec.spawn_min_food_level:
            return False
    if spec.spawn_min_population is not None:
        if ctx.population_count < spec.spawn_min_population:
            return False
    return True


REGISTERED_EVENTS: tuple[GameEventSpec, ...] = (
    GameEventSpec(
        kind=RATS_SILO_KIND,
        spawn_chance_per_tick=0.1,
        duration_seconds=60,
        food_consumption_multiplier=3.0,
        loyalty_delta_auto=-5.0,
        loyalty_delta_player=4.0,
        announce_on_start=False,
        start_message=(
            "."
        ),
        resolve_message_auto=(
            "The rat swarm dispersed after exhausting scattered grain. "
            "Residents are unhappy about the wasted supplies."
        ),
        resolve_message_player=(
            "Investigation team cleared the silo breach and salvaged "
            "most of the spillage. Morale improved."
        ),
        spawn_min_food_level=15.0,
        spawn_min_population=10,
        eligible=spawn_threshold_eligible,
    ),
)

EVENTS_BY_KIND: dict[str, GameEventSpec] = {s.kind: s for s in REGISTERED_EVENTS}


def spec_for_kind(kind: str) -> GameEventSpec | None:
    return EVENTS_BY_KIND.get(kind)


def active_event_food_multiplier(user_id: str) -> float:
    row = db.session.get(PlayerActiveEvent, user_id)
    if row is None:
        return 1.0
    spec = spec_for_kind(row.kind)
    if spec is None:
        return 1.0
    return float(spec.food_consumption_multiplier)


def active_event_row(user_id: str) -> PlayerActiveEvent | None:
    return db.session.get(PlayerActiveEvent, user_id)


def auto_resolve_if_due(user_id: str, tick_time: datetime) -> float:
    """If the deadline passed, clear the row, log resolution, return loyalty delta."""
    row = db.session.get(PlayerActiveEvent, user_id)
    if row is None or tick_time < row.auto_resolve_at:
        return 0.0

    spec = spec_for_kind(row.kind)
    db.session.delete(row)
    if spec is None:
        log.warning("auto-resolve unknown event kind=%s user=%s", row.kind, user_id)
        return 0.0

    delta = float(spec.loyalty_delta_auto)
    db.session.add(
        SystemMessage(
            user_id=user_id,
            body=spec.resolve_message_auto,
            timestamp=tick_time,
        )
    )
    log.info("event auto-resolved: user=%s kind=%s loyalty_delta=%s", user_id, spec.kind, delta)
    return delta


def try_spawn_event(
    user_id: str,
    ctx: EventSpawnContext,
    tick_time: datetime,
) -> None:
    if db.session.get(PlayerActiveEvent, user_id) is not None:
        return

    for spec in REGISTERED_EVENTS:
        if not spec.eligible(ctx, spec):
            continue
        chance = float(spec.spawn_chance_per_tick)
        if random.random() >= chance:
            continue

        duration_s = int(spec.duration_seconds)
        db.session.add(
            PlayerActiveEvent(
                user_id=user_id,
                kind=spec.kind,
                started_at=tick_time,
                auto_resolve_at=tick_time + timedelta(seconds=duration_s),
            )
        )
        if spec.announce_on_start and spec.start_message:
            db.session.add(
                SystemMessage(
                    user_id=user_id,
                    body=spec.start_message,
                    timestamp=tick_time,
                )
            )
        log.info("event spawned: user=%s kind=%s duration_s=%s", user_id, spec.kind, duration_s)
        return


def try_player_resolve(
    user_id: str,
    kind: str,
    tick_time: datetime | None = None,
) -> bool:
    """Player-driven resolution. Returns True if an active event was cleared."""
    row = db.session.get(PlayerActiveEvent, user_id)
    if row is None or row.kind != kind:
        return False

    spec = spec_for_kind(kind)
    if spec is None:
        return False

    when = tick_time if tick_time is not None else datetime.now(timezone.utc)
    db.session.delete(row)

    latest = db.session.scalars(
        select(BunkerLoyalty)
        .where(BunkerLoyalty.user_id == user_id)
        .order_by(BunkerLoyalty.timestamp.desc())
        .limit(1)
    ).first()
    base = latest.loyalty if latest is not None else 0.0

    delta = float(spec.loyalty_delta_player)
    new_loyalty = max(0.0, min(100.0, base + delta))

    db.session.add(
        SystemMessage(
            user_id=user_id,
            body=spec.resolve_message_player,
            timestamp=when,
        )
    )
    db.session.add(BunkerLoyalty(user_id=user_id, loyalty=new_loyalty, timestamp=when))
    log.info(
        "event player-resolved: user=%s kind=%s loyalty %.1f→%.1f",
        user_id,
        kind,
        base,
        new_loyalty,
    )
    return True


def active_event_status_payload(user_id: str | None) -> dict[str, Any]:
    """JSON-serializable status for Grafana polling."""
    if not user_id:
        return {"active": False}
    row = db.session.get(PlayerActiveEvent, user_id)
    if row is None:
        return {"active": False}
    return {
        "active": True,
        "kind": row.kind,
        "resolve_deadline": row.auto_resolve_at.isoformat(),
    }
