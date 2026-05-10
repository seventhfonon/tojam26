"""Random gameplay events: spawn conditions, concurrent rows, resolve paths.

Definitions live in code; ``player_active_events`` holds zero or more active
rows per player (unique per ``kind``). Tunables live on :class:`GameEventSpec`.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any

from sqlalchemy import select

from .extensions import db
from . import constants as game_constants
from .constants import (
    GAME_SYSTEM_LABELS,
    INVESTIGATION_DISPATCH_BY_SYSTEM,
    normalize_game_system_id,
)
from .models import BunkerProfession, PlayerActiveEvent, SystemMessage, User
from .professions import PROFESSION_IDLE, PROFESSION_INVESTIGATION

log = logging.getLogger(__name__)


class EventDefinition(StrEnum):
    """Wire ids persisted on ``PlayerActiveEvent.kind`` (enum value == stored string)."""

    RATS_SILO_INTRO = "rats_silo_intro"
    RATS_SILO = "rats_silo"

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
    rat_trapper_count: int = 0
    silo_rats_introduced: bool = False
    rat_background_consumption_ps: float = 0.0


@dataclass(frozen=True)
class EventOutcome:
    """Loyalty adjustment and player-facing copy when an event resolves."""

    loyalty_delta: float
    message: str


@dataclass(frozen=True)
class EventTickEffects:
    """Per-tick simulation adjustments while this event row is active.

    Add fields here as new systems read modifiers from ``game_tick`` (defaults keep
    legacy behaviour when unchanged).
    """

    #: Multiplier on baseline population food consumption (per capita × population).
    food_consumption_multiplier: float = 1.0


_TICK_EFFECTS_REFERENCE_TIME = datetime(1970, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class GameEventSpec:
    """Code-defined event; spawn gates, immediate hooks, and resolution outcomes."""

    definition: EventDefinition
    spawn_chance_per_tick: float
    #: ``None`` — no timer; cleared only via ``player_resolve`` (investigation on matching ``system``).
    duration_seconds: int | None
    #: Simulation deltas for each tick while the active row exists (may vary by user/time).
    tick_effects: Callable[[str, datetime], EventTickEffects]
    #: ``True`` iff this event may attempt RNG this tick (thresholds, suppression, etc.).
    can_spawn: Callable[[EventSpawnContext], bool]
    #: Timer expiry — auto-resolve path (no investigation dispatch).
    auto_resolve: Callable[[str, datetime], EventOutcome]
    #: Investigation sweep completes with subsystem match — player-resolution path.
    player_resolve: Callable[[str, datetime], EventOutcome]
    #: Optional system message when the event row is created; ``None`` skips posting.
    spawn_announcement: Callable[[str, datetime], str | None]
    #: Runs immediately after the ``PlayerActiveEvent`` row is added (same transaction); DB/session side effects only.
    on_spawn: Callable[[str, datetime], None]
    #: When set, ties this event to a bunker subsystem (see ``GAME_SYSTEM_IDS``).
    system: str | None = None
    #: Optional side effects after ``player_resolve`` (same transaction as messages).
    on_player_resolve: Callable[[str, datetime], None] | None = None


def rats_spike_marginal_food_consumption_per_second(population: int) -> float:
    """Extra human-food/sec during ``rats_silo`` vs baseline (population × per-capita × (mult − 1))."""
    spec = spec_for_definition(EventDefinition.RATS_SILO)
    if spec is None or population <= 0:
        return 0.0
    mult = float(spec.tick_effects("", _TICK_EFFECTS_REFERENCE_TIME).food_consumption_multiplier)
    per_cap = float(game_constants.FOOD_PER_CAPITA_PER_SECOND)
    return max(0.0, float(population) * per_cap * max(0.0, mult - 1.0))


def combined_rats_consumption_per_second_for_trappers(
    population: int,
    rat_background_ps: float,
    swarm_active: bool,
) -> float:
    """Total rat-driven food pressure used for trapper salvage (background nibbling + optional swarm spike)."""
    bg = max(0.0, float(rat_background_ps))
    spike_marginal = (
        rats_spike_marginal_food_consumption_per_second(population) if swarm_active else 0.0
    )
    return bg + spike_marginal


def rat_trapper_food_production_per_second(trapper_count: int, combined_rat_consumption_ps: float) -> float:
    """Food/sec recovered by trappers, proportional to combined rat drain × trapper headcount."""
    scale = float(game_constants.RAT_TRAPPER_PRODUCTION_PER_TRAP_PRESSURE_UNIT)
    basis = max(0.0, float(combined_rat_consumption_ps))
    return max(0.0, float(trapper_count) * scale * basis)


def rats_spike_suppressed_by_trappers(ctx: EventSpawnContext) -> bool:
    """Swarm cannot spawn if trapper output already covers its marginal spike drain (combined-rate basis)."""
    spike_marginal = rats_spike_marginal_food_consumption_per_second(ctx.population_count)
    if spike_marginal <= 0:
        return False
    combined = ctx.rat_background_consumption_ps + spike_marginal
    prod = rat_trapper_food_production_per_second(ctx.rat_trapper_count, combined)
    return prod >= spike_marginal - 1e-12


def _can_spawn_rats_silo_intro(ctx: EventSpawnContext) -> bool:
    if ctx.silo_rats_introduced:
        return False
    if ctx.latest_food_level < 12.0:
        return False
    return ctx.population_count >= 8


def _can_spawn_rats_silo_spike(ctx: EventSpawnContext) -> bool:
    if rats_spike_suppressed_by_trappers(ctx):
        return False
    if not ctx.silo_rats_introduced:
        return False
    if ctx.latest_food_level < 15.0:
        return False
    return ctx.population_count >= 10


def _rats_silo_intro_auto_resolve(_user_id: str, _tick_time: datetime) -> EventOutcome:
    return EventOutcome(
        loyalty_delta=-2.0,
        message=(
            "The intrusion settled into a chronic nuisance: small gnaw-holes "
            "and scattered husks, but bulk stores appear intact for now."
        ),
    )


def _rats_silo_intro_player_resolve(_user_id: str, _tick_time: datetime) -> EventOutcome:
    return EventOutcome(
        loyalty_delta=3.0,
        message=(
            "Containment sealed the breach path and laid deterrent lines; "
            "morale improved once crews proved the bulk grain stayed accounted."
        ),
    )


def _rats_silo_intro_after_player_resolve(user_id: str, _tick_time: datetime) -> None:
    user_row = db.session.get(User, user_id)
    if user_row is not None:
        user_row.rat_trappers_unlocked = True


def _rats_silo_intro_spawn_announce(_user_id: str, _tick_time: datetime) -> str | None:
    return (
        "RATS! Grain-room telemetry flagged vibration behind the inner jacket — "
        "rats have breached the silo liner."
    )


def _noop_on_spawn(_user_id: str, _tick_time: datetime) -> None:
    return None


def _rats_silo_intro_on_spawn(user_id: str, _tick_time: datetime) -> None:
    user_row = db.session.get(User, user_id)
    if user_row is None:
        return
    user_row.silo_rats_introduced = True
    user_row.rat_background_consumption_ps = float(game_constants.RAT_BACKGROUND_INITIAL_DRAIN_PS)


def _rats_silo_spike_auto_resolve(_user_id: str, _tick_time: datetime) -> EventOutcome:
    return EventOutcome(
        loyalty_delta=-5.0,
        message=(
            "The rat swarm dispersed after exhausting scattered grain. "
            "Residents are unhappy about the wasted supplies."
        ),
    )


def _rats_silo_spike_player_resolve(_user_id: str, _tick_time: datetime) -> EventOutcome:
    return EventOutcome(
        loyalty_delta=4.0,
        message=(
            "Investigation team cleared the silo breach and salvaged "
            "most of the spillage. Morale improved."
        ),
    )


def _rats_silo_spike_spawn_announce(_user_id: str, _tick_time: datetime) -> str | None:
    return None


def _rats_silo_intro_tick_effects(_user_id: str, _tick_time: datetime) -> EventTickEffects:
    return EventTickEffects(food_consumption_multiplier=1.0)


def _rats_silo_spike_tick_effects(_user_id: str, _tick_time: datetime) -> EventTickEffects:
    return EventTickEffects(food_consumption_multiplier=3.0)


REGISTERED_EVENTS: tuple[GameEventSpec, ...] = (
    GameEventSpec(
        definition=EventDefinition.RATS_SILO_INTRO,
        spawn_chance_per_tick=0.01,
        duration_seconds=None,
        tick_effects=_rats_silo_intro_tick_effects,
        can_spawn=_can_spawn_rats_silo_intro,
        auto_resolve=_rats_silo_intro_auto_resolve,
        player_resolve=_rats_silo_intro_player_resolve,
        spawn_announcement=_rats_silo_intro_spawn_announce,
        on_spawn=_rats_silo_intro_on_spawn,
        system="farming",
        on_player_resolve=_rats_silo_intro_after_player_resolve,
    ),
    GameEventSpec(
        definition=EventDefinition.RATS_SILO,
        spawn_chance_per_tick=0.008,
        duration_seconds=60,
        tick_effects=_rats_silo_spike_tick_effects,
        can_spawn=_can_spawn_rats_silo_spike,
        auto_resolve=_rats_silo_spike_auto_resolve,
        player_resolve=_rats_silo_spike_player_resolve,
        spawn_announcement=_rats_silo_spike_spawn_announce,
        on_spawn=_noop_on_spawn,
        system="farming",
    ),
)

EVENTS_BY_DEFINITION: dict[str, GameEventSpec] = {
    s.definition.value: s for s in REGISTERED_EVENTS
}


def spec_for_definition(definition: str | EventDefinition) -> GameEventSpec | None:
    key = definition.value if isinstance(definition, EventDefinition) else definition
    return EVENTS_BY_DEFINITION.get(key)


def active_events_for_user(user_id: str) -> list[PlayerActiveEvent]:
    return list(
        db.session.scalars(
            select(PlayerActiveEvent).where(PlayerActiveEvent.user_id == user_id)
        ).all()
    )


def player_has_active_event_kind(user_id: str, definition: EventDefinition) -> bool:
    return (
        db.session.scalars(
            select(PlayerActiveEvent.id).where(
                PlayerActiveEvent.user_id == user_id,
                PlayerActiveEvent.kind == definition.value,
            ).limit(1)
        ).first()
        is not None
    )


def player_has_any_active_event(user_id: str) -> bool:
    return (
        db.session.scalars(
            select(PlayerActiveEvent.id).where(PlayerActiveEvent.user_id == user_id).limit(1)
        ).first()
        is not None
    )


def event_spawn_context_from_user(
    user_id: str,
    *,
    latest_food_level: float,
    population_count: int,
    rat_trapper_count: int,
) -> EventSpawnContext:
    user_row = db.session.get(User, user_id)
    silo = bool(user_row.silo_rats_introduced) if user_row is not None else False
    bg = float(user_row.rat_background_consumption_ps) if user_row is not None else 0.0
    return EventSpawnContext(
        latest_food_level=float(latest_food_level),
        population_count=int(population_count),
        rat_trapper_count=int(rat_trapper_count),
        silo_rats_introduced=silo,
        rat_background_consumption_ps=bg,
    )


def _merge_tick_effects(parts: list[EventTickEffects]) -> EventTickEffects:
    mult = 1.0
    for fx in parts:
        mult *= float(fx.food_consumption_multiplier)
    return EventTickEffects(food_consumption_multiplier=mult)


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


def active_event_tick_effects(user_id: str, tick_time: datetime) -> EventTickEffects:
    """Merged per-tick modifiers from **all** active event rows (food mult = product)."""
    merged: list[EventTickEffects] = []
    for row in active_events_for_user(user_id):
        spec = spec_for_definition(row.kind)
        if spec is None:
            continue
        merged.append(spec.tick_effects(user_id, tick_time))
    if not merged:
        return EventTickEffects()
    return _merge_tick_effects(merged)


def active_event_food_multiplier(user_id: str, tick_time: datetime) -> float:
    """Human food consumption multiplier from active random events (product across rows)."""
    return float(active_event_tick_effects(user_id, tick_time).food_consumption_multiplier)


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

    ev = None
    if target_sys is not None:
        ev = db.session.scalars(
            select(PlayerActiveEvent)
            .where(
                PlayerActiveEvent.user_id == user_id,
                PlayerActiveEvent.system.is_not(None),
                PlayerActiveEvent.system == target_sys,
            )
            .order_by(PlayerActiveEvent.started_at.asc())
        ).first()
    if ev is not None:
        spec = spec_for_definition(ev.kind)
        kind_str = ev.kind
        db.session.delete(ev)
        if spec is None:
            log.warning("finalize investigation unknown kind=%s user=%s", kind_str, user_id)
            return 0.0

        outcome = spec.player_resolve(user_id, tick_time)
        if spec.on_player_resolve is not None:
            spec.on_player_resolve(user_id, tick_time)
        delta = float(outcome.loyalty_delta)
        db.session.add(
            SystemMessage(
                user_id=user_id,
                body=outcome.message,
                timestamp=tick_time,
            )
        )
        log.info(
            "investigation tied off subsystem event: user=%s definition=%s system=%s loyalty_delta=%s",
            user_id,
            spec.definition,
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
    """Auto-resolve every overdue active row; return summed loyalty delta."""
    user = db.session.get(User, user_id)
    if (
        user is not None
        and user.investigation_busy_until is not None
        and tick_time < user.investigation_busy_until
    ):
        return 0.0

    rows = list(
        db.session.scalars(
            select(PlayerActiveEvent).where(
                PlayerActiveEvent.user_id == user_id,
                PlayerActiveEvent.auto_resolve_at.is_not(None),
                PlayerActiveEvent.auto_resolve_at <= tick_time,
            )
        ).all()
    )
    if not rows:
        return 0.0

    total_delta = 0.0
    for row in rows:
        kind_str = row.kind
        spec = spec_for_definition(row.kind)
        release_investigation_team_to_idle(user_id, tick_time)
        db.session.delete(row)
        if spec is None:
            log.warning("auto-resolve unknown event kind=%s user=%s", kind_str, user_id)
            continue

        outcome = spec.auto_resolve(user_id, tick_time)
        delta = float(outcome.loyalty_delta)
        total_delta += delta
        db.session.add(
            SystemMessage(
                user_id=user_id,
                body=outcome.message,
                timestamp=tick_time,
            )
        )
        log.info(
            "event auto-resolved: user=%s definition=%s loyalty_delta=%s",
            user_id,
            spec.definition,
            delta,
        )
    return total_delta


def try_spawn_event(
    user_id: str,
    latest_food_level: float,
    population_count: int,
    rat_trapper_count: int,
    tick_time: datetime,
) -> None:
    for spec in REGISTERED_EVENTS:
        if player_has_active_event_kind(user_id, spec.definition):
            continue
        ctx = event_spawn_context_from_user(
            user_id,
            latest_food_level=latest_food_level,
            population_count=population_count,
            rat_trapper_count=rat_trapper_count,
        )
        if not spec.can_spawn(ctx):
            continue
        chance = float(spec.spawn_chance_per_tick)
        if random.random() >= chance:
            continue

        deadline: datetime | None
        if spec.duration_seconds is None:
            deadline = None
        else:
            duration_s = int(spec.duration_seconds)
            deadline = tick_time + timedelta(seconds=duration_s)
        db.session.add(
            PlayerActiveEvent(
                user_id=user_id,
                kind=spec.definition,
                started_at=tick_time,
                auto_resolve_at=deadline,
                system=spec.system,
            )
        )
        spec.on_spawn(user_id, tick_time)
        announce_body = spec.spawn_announcement(user_id, tick_time)
        if announce_body:
            db.session.add(
                SystemMessage(
                    user_id=user_id,
                    body=announce_body,
                    timestamp=tick_time,
                )
            )
        log.info(
            "event spawned: user=%s definition=%s duration_s=%s",
            user_id,
            spec.definition,
            spec.duration_seconds,
        )


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
    if user.sermon_busy_until is not None and when < user.sermon_busy_until:
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

    for ev_row in active_events_for_user(user_id):
        if ev_row.auto_resolve_at is None or busy_until > ev_row.auto_resolve_at:
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
    sermon_locked = (
        user is not None
        and user.sermon_busy_until is not None
        and now < user.sermon_busy_until
    )
    need = int(cfg.team_size)
    can_dispatch = (
        user is not None
        and not deployed
        and not sermon_locked
        and idle_n >= need
    )
    return {
        "can_dispatch": can_dispatch,
        "team_deployed": deployed,
        "team_required": need,
    }
