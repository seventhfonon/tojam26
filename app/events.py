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
from .constants import (
    GAME_SYSTEM_LABELS,
    INVESTIGATION_DISPATCH_BY_SYSTEM,
    normalize_game_system_id,
)
from .models import BunkerProfession, PlayerActiveEvent, SystemMessage, User
from .professions import PROFESSION_IDLE, PROFESSION_INVESTIGATION

log = logging.getLogger(__name__)

RATS_SILO_KIND = "rats_silo"

ROUTINE_INVESTIGATION_DISPATCH_TEMPLATE = (
    "{n} residents detached for scheduled sweep of {subsystem}. "
    "They sign back in when the round completes."
)
ROUTINE_INVESTIGATION_COMPLETE_TEMPLATE = (
    "Sweep detail returned from {subsystem}. Routine checklist filed with nothing escalated."
)


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
    #: When set, ties this event to a bunker subsystem (see ``GAME_SYSTEM_IDS``).
    system: str | None = None


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
        spawn_chance_per_tick=0.01,
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
        system="farming",
    ),
)

EVENTS_BY_KIND: dict[str, GameEventSpec] = {s.kind: s for s in REGISTERED_EVENTS}


def spec_for_kind(kind: str) -> GameEventSpec | None:
    return EVENTS_BY_KIND.get(kind)


def _investigation_line(user_id: str) -> BunkerProfession | None:
    return db.session.scalars(
        select(BunkerProfession).where(
            BunkerProfession.user_id == user_id,
            BunkerProfession.profession == PROFESSION_INVESTIGATION,
        )
    ).first()


def _idle_line(user_id: str) -> BunkerProfession | None:
    return db.session.scalars(
        select(BunkerProfession).where(
            BunkerProfession.user_id == user_id,
            BunkerProfession.profession == PROFESSION_IDLE,
        )
    ).first()


def investigation_team_count(user_id: str) -> int:
    row = _investigation_line(user_id)
    return row.count if row is not None else 0


def release_investigation_team_to_idle(user_id: str, tick_time: datetime) -> None:
    """Move everyone on Investigation back to Idle (used when timers fire)."""
    inv_line = _investigation_line(user_id)
    idle_line = _idle_line(user_id)
    if inv_line is None or idle_line is None:
        return
    n = inv_line.count
    if n <= 0:
        return
    inv_line.count = 0
    idle_line.count += n
    inv_line.updated_at = tick_time
    idle_line.updated_at = tick_time


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


def finalize_investigation_if_due(user_id: str, tick_time: datetime) -> float:
    """When sweep timer elapses: free workers; clear active event if it matches target subsystem."""
    user = db.session.get(User, user_id)
    if user is None or user.investigation_busy_until is None:
        return 0.0
    if tick_time < user.investigation_busy_until:
        return 0.0

    release_investigation_team_to_idle(user_id, tick_time)
    target_sys = user.investigation_target_system
    user.investigation_busy_until = None
    user.investigation_target_system = None

    if target_sys:
        subsystem_lbl = GAME_SYSTEM_LABELS.get(target_sys, target_sys)
    else:
        subsystem_lbl = "the bunker"

    ev = db.session.get(PlayerActiveEvent, user_id)
    if (
        ev is not None
        and ev.system is not None
        and target_sys is not None
        and ev.system == target_sys
    ):
        spec = spec_for_kind(ev.kind)
        kind_str = ev.kind
        db.session.delete(ev)
        if spec is None:
            log.warning("finalize investigation unknown kind=%s user=%s", kind_str, user_id)
            return 0.0

        delta = float(spec.loyalty_delta_player)
        db.session.add(
            SystemMessage(
                user_id=user_id,
                body=spec.resolve_message_player,
                timestamp=tick_time,
            )
        )
        log.info(
            "investigation tied off subsystem event: user=%s kind=%s system=%s loyalty_delta=%s",
            user_id,
            spec.kind,
            target_sys,
            delta,
        )
        return delta

    db.session.add(
        SystemMessage(
            user_id=user_id,
            body=ROUTINE_INVESTIGATION_COMPLETE_TEMPLATE.format(subsystem=subsystem_lbl),
            timestamp=tick_time,
        )
    )
    log.info(
        "routine investigation sweep finished: user=%s target_system=%s",
        user_id,
        target_sys,
    )
    return 0.0


def auto_resolve_if_due(user_id: str, tick_time: datetime) -> float:
    """If the deadline passed, clear the row, log resolution, return loyalty delta."""
    row = db.session.get(PlayerActiveEvent, user_id)
    if row is None:
        return 0.0
    user = db.session.get(User, user_id)
    if (
        user is not None
        and user.investigation_busy_until is not None
        and tick_time < user.investigation_busy_until
    ):
        return 0.0
    if tick_time < row.auto_resolve_at:
        return 0.0

    kind_str = row.kind
    spec = spec_for_kind(row.kind)
    release_investigation_team_to_idle(user_id, tick_time)
    db.session.delete(row)
    if spec is None:
        log.warning("auto-resolve unknown event kind=%s user=%s", kind_str, user_id)
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
                system=spec.system,
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


def try_dispatch_investigation(user_id: str, system: str, when: datetime) -> bool:
    """Routine subsystem sweep: Idle → Investigation until timer ends (no event gate)."""
    system_id = normalize_game_system_id(system)
    if system_id is None:
        return False

    cfg = INVESTIGATION_DISPATCH_BY_SYSTEM.get(system_id)
    if cfg is None or cfg.team_size <= 0:
        return False

    user = db.session.get(User, user_id)
    if user is None:
        return False
    if user.investigation_busy_until is not None and when < user.investigation_busy_until:
        return False

    team_n = int(cfg.team_size)
    dur_s = int(cfg.duration_seconds)
    if dur_s <= 0:
        return False

    idle_line = _idle_line(user_id)
    inv_line = _investigation_line(user_id)
    if idle_line is None or inv_line is None:
        return False
    if idle_line.count < team_n:
        return False

    idle_line.count -= team_n
    inv_line.count += team_n
    idle_line.updated_at = when
    inv_line.updated_at = when

    busy_until = when + timedelta(seconds=dur_s)
    user.investigation_busy_until = busy_until
    user.investigation_target_system = system_id

    ev_row = db.session.get(PlayerActiveEvent, user_id)
    if ev_row is not None and busy_until > ev_row.auto_resolve_at:
        ev_row.auto_resolve_at = busy_until

    subsystem_lbl = GAME_SYSTEM_LABELS.get(system_id, system_id)
    db.session.add(
        SystemMessage(
            user_id=user_id,
            body=ROUTINE_INVESTIGATION_DISPATCH_TEMPLATE.format(n=team_n, subsystem=subsystem_lbl),
            timestamp=when,
        )
    )
    log.info(
        "investigation sweep dispatched: user=%s system=%s team=%d until=%s",
        user_id,
        system_id,
        team_n,
        busy_until.isoformat(),
    )
    return True


_DEFAULT_TEAM_REQUIRED_HINT = max(
    c.team_size for c in INVESTIGATION_DISPATCH_BY_SYSTEM.values()
)


def investigation_dispatch_status_payload(
    user_id: str | None, system: str | None
) -> dict[str, Any]:
    """Minimal JSON for UI polling — no spoiler fields about hidden events."""
    system_id = normalize_game_system_id(system)
    if not user_id or system_id is None:
        return {
            "can_dispatch": False,
            "team_deployed": False,
            "team_required": _DEFAULT_TEAM_REQUIRED_HINT,
        }

    cfg = INVESTIGATION_DISPATCH_BY_SYSTEM.get(system_id)
    if cfg is None:
        return {
            "can_dispatch": False,
            "team_deployed": False,
            "team_required": _DEFAULT_TEAM_REQUIRED_HINT,
        }

    user = db.session.get(User, user_id)
    idle_row = _idle_line(user_id)
    idle_n = idle_row.count if idle_row is not None else 0
    now = datetime.now(timezone.utc)
    deployed = (
        user is not None
        and user.investigation_busy_until is not None
        and now < user.investigation_busy_until
    )
    need = int(cfg.team_size)
    can_dispatch = user is not None and not deployed and idle_n >= need
    return {
        "can_dispatch": can_dispatch,
        "team_deployed": deployed,
        "team_required": need,
    }
