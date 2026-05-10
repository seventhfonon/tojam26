"""Per–Inner Circle member tasks, drift, and bunker modifiers."""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .extensions import db
from . import constants
from .models import (
    BunkerDoubt,
    BunkerLoyalty,
    BunkerSocialState,
    EnergyReserve,
    FoodReserve,
    InnerCircleCashSample,
    InnerCircleMember,
)

log = logging.getLogger(__name__)


def seed_members_for_user_if_needed(user_id: str) -> None:
    names = constants.INNER_CIRCLE_MEMBER_NAMES
    loys = constants.INNER_CIRCLE_INITIAL_MEMBER_LOYALTIES
    pops = constants.INNER_CIRCLE_INITIAL_MEMBER_POPULARITIES
    for i, name in enumerate(names):
        row = db.session.get(InnerCircleMember, (user_id, i))
        if row is None:
            db.session.add(
                InnerCircleMember(
                    user_id=user_id,
                    slot_index=i,
                    display_name=name,
                    loyalty=float(loys[i]),
                    popularity=float(pops[i]),
                )
            )


def member_is_busy(m: InnerCircleMember, now: datetime) -> bool:
    if m.task_ends_at is None:
        return False
    return now < m.task_ends_at


def popularity_loyalty_penalty(user_id: str) -> float:
    members = db.session.scalars(
        select(InnerCircleMember).where(InnerCircleMember.user_id == user_id)
    ).all()
    total = 0.0
    rate = float(constants.INNER_CIRCLE_UNPOPULAR_LOYALTY_PENALTY_PER_POINT)
    for m in members:
        p = float(m.popularity)
        if p < 50.0:
            total += (50.0 - p) * rate
    return total


def drift_member_loyalties(
    user_id: str,
    bunker_loyalty_anchor: float,
    elapsed_seconds: float,
    tick_time: datetime,
) -> None:
    if elapsed_seconds <= 0:
        return
    anchor = max(
        0.0,
        min(
            100.0,
            bunker_loyalty_anchor + float(constants.INNER_CIRCLE_LOYALTY_BIAS),
        ),
    )
    k = float(constants.INNER_CIRCLE_LOYALTY_DRIFT_PER_SECOND)
    members = db.session.scalars(
        select(InnerCircleMember).where(InnerCircleMember.user_id == user_id)
    ).all()
    for m in members:
        if member_is_busy(m, tick_time):
            continue
        lv = float(m.loyalty)
        lv += k * (anchor - lv) * elapsed_seconds
        m.loyalty = max(0.0, min(100.0, lv))


def sync_aggregate_inner_circle_loyalty(user_id: str) -> None:
    social = db.session.get(BunkerSocialState, user_id)
    if social is None:
        return
    members = db.session.scalars(
        select(InnerCircleMember).where(InnerCircleMember.user_id == user_id)
    ).all()
    if not members:
        return
    mean_loy = sum(float(x.loyalty) for x in members) / len(members)
    social.inner_circle_loyalty = int(round(max(0.0, min(100.0, mean_loy))))


def record_cash_sample(user_id: str, tick_time: datetime) -> None:
    social = db.session.get(BunkerSocialState, user_id)
    if social is None:
        return
    db.session.add(
        InnerCircleCashSample(
            user_id=user_id,
            timestamp=tick_time,
            cash=float(social.inner_circle_cash),
        )
    )


def _clear_task(m: InnerCircleMember) -> None:
    m.task_kind = None
    m.task_started_at = None
    m.task_ends_at = None


def complete_due_tasks(
    user_id: str, tick_time: datetime, working_doubt: float
) -> tuple[float, float]:
    """Finish elapsed member tasks. Returns (working_doubt, bunker_loyalty_bonus)."""
    d = working_doubt
    bunker_loyalty_bonus = 0.0
    members = db.session.scalars(
        select(InnerCircleMember)
        .where(InnerCircleMember.user_id == user_id)
        .order_by(InnerCircleMember.slot_index)
    ).all()
    social = db.session.get(BunkerSocialState, user_id)

    for m in members:
        if m.task_kind is None or m.task_ends_at is None:
            continue
        if tick_time + timedelta(microseconds=1) < m.task_ends_at:
            continue

        kind = m.task_kind
        _clear_task(m)

        if kind == constants.INNER_CIRCLE_TASK_STAGE_INCIDENT:
            if random.random() < float(constants.INNER_CIRCLE_STAGE_INCIDENT_DISCOVER_CHANCE):
                m.popularity = max(
                    0.0,
                    float(m.popularity)
                    - float(constants.INNER_CIRCLE_STAGE_INCIDENT_DISCOVER_POPULARITY_DROP),
                )
                d = min(
                    100.0,
                    d + float(constants.INNER_CIRCLE_STAGE_INCIDENT_DISCOVER_DOUBT_BUMP),
                )
                db.session.add(
                    BunkerDoubt(user_id=user_id, doubt=d, timestamp=tick_time)
                )
                log.info(
                    "inner circle: staged incident exposed user=%s slot=%s",
                    user_id,
                    m.slot_index,
                )
            else:
                m.loyalty = min(
                    100.0,
                    float(m.loyalty)
                    + float(constants.INNER_CIRCLE_STAGE_INCIDENT_MEMBER_LOYALTY_DELTA),
                )
                d = max(
                    0.0,
                    d - float(constants.INNER_CIRCLE_STAGE_INCIDENT_DOUBT_RELIEF),
                )
                db.session.add(
                    BunkerDoubt(user_id=user_id, doubt=d, timestamp=tick_time)
                )
                bunker_loyalty_bonus += float(
                    constants.INNER_CIRCLE_STAGE_INCIDENT_BUNKER_LOYALTY_DELTA
                )

        elif kind == constants.INNER_CIRCLE_TASK_BUY_GROCERIES:
            latest_food = db.session.scalars(
                select(FoodReserve)
                .where(FoodReserve.user_id == user_id)
                .order_by(FoodReserve.timestamp.desc())
                .limit(1)
            ).first()
            latest_en = db.session.scalars(
                select(EnergyReserve)
                .where(EnergyReserve.user_id == user_id)
                .order_by(EnergyReserve.timestamp.desc())
                .limit(1)
            ).first()
            if latest_food is not None:
                nf = float(latest_food.level) + float(
                    constants.INNER_CIRCLE_BUY_GROCERIES_FOOD_GAIN
                )
                db.session.add(
                    FoodReserve(
                        user_id=user_id,
                        level=nf,
                        consumption_per_second=float(
                            latest_food.consumption_per_second
                        ),
                        production_per_second=float(latest_food.production_per_second),
                        timestamp=tick_time,
                    )
                )
            if latest_en is not None:
                ne = float(latest_en.level) + float(
                    constants.INNER_CIRCLE_BUY_GROCERIES_ENERGY_GAIN
                )
                db.session.add(
                    EnergyReserve(user_id=user_id, level=ne, timestamp=tick_time)
                )
            if random.random() < float(constants.INNER_CIRCLE_BUY_GROCERIES_DOUBT_BAD_CHANCE):
                d = min(
                    100.0,
                    d + float(constants.INNER_CIRCLE_BUY_GROCERIES_DOUBT_BAD_AMOUNT),
                )
                db.session.add(
                    BunkerDoubt(user_id=user_id, doubt=d, timestamp=tick_time)
                )

        elif kind == constants.INNER_CIRCLE_TASK_TEMP_JOB:
            if social is not None:
                social.inner_circle_cash = float(social.inner_circle_cash) + float(
                    constants.INNER_CIRCLE_TEMP_JOB_CASH_GAIN
                )
            m.loyalty = max(
                0.0,
                float(m.loyalty)
                - float(constants.INNER_CIRCLE_TEMP_JOB_MEMBER_LOYALTY_DROP),
            )
            if random.random() < float(constants.INNER_CIRCLE_TEMP_JOB_DOUBT_BAD_CHANCE):
                d = min(
                    100.0,
                    d + float(constants.INNER_CIRCLE_TEMP_JOB_DOUBT_BAD_AMOUNT),
                )
                db.session.add(
                    BunkerDoubt(user_id=user_id, doubt=d, timestamp=tick_time)
                )

    return (d, bunker_loyalty_bonus)


def _start_task(m: InnerCircleMember, kind: str, duration_seconds: int, now: datetime) -> None:
    m.task_kind = kind
    m.task_started_at = now
    m.task_ends_at = now + timedelta(seconds=int(duration_seconds))


def try_grant_luxuries(user_id: str, slot: int, now: datetime) -> str | None:
    """Return None on success, else an error code."""
    if slot < 0 or slot >= constants.INNER_CIRCLE_MEMBER_COUNT:
        return "bad_slot"
    seed_members_for_user_if_needed(user_id)
    m = db.session.get(InnerCircleMember, (user_id, slot))
    if m is None:
        return "no_member"
    if member_is_busy(m, now):
        return "busy"

    food_need = float(constants.INNER_CIRCLE_GRANT_LUXURIES_FOOD_COST)
    energy_need = float(constants.INNER_CIRCLE_GRANT_LUXURIES_ENERGY_COST)
    latest_food = db.session.scalars(
        select(FoodReserve)
        .where(FoodReserve.user_id == user_id)
        .order_by(FoodReserve.timestamp.desc())
        .limit(1)
    ).first()
    latest_en = db.session.scalars(
        select(EnergyReserve)
        .where(EnergyReserve.user_id == user_id)
        .order_by(EnergyReserve.timestamp.desc())
        .limit(1)
    ).first()
    if latest_food is None or float(latest_food.level) + 1e-9 < food_need:
        return "need_food"
    if latest_en is None or float(latest_en.level) + 1e-9 < energy_need:
        return "need_energy"

    nf = float(latest_food.level) - food_need
    db.session.add(
        FoodReserve(
            user_id=user_id,
            level=max(0.0, nf),
            consumption_per_second=float(latest_food.consumption_per_second),
            production_per_second=float(latest_food.production_per_second),
            timestamp=now,
        )
    )
    ne = float(latest_en.level) - energy_need
    db.session.add(EnergyReserve(user_id=user_id, level=max(0.0, ne), timestamp=now))

    m.loyalty = min(
        100.0,
        float(m.loyalty)
        + float(constants.INNER_CIRCLE_GRANT_LUXURIES_MEMBER_LOYALTY_DELTA),
    )
    latest_loy = db.session.scalars(
        select(BunkerLoyalty)
        .where(BunkerLoyalty.user_id == user_id)
        .order_by(BunkerLoyalty.timestamp.desc())
        .limit(1)
    ).first()
    if latest_loy is not None:
        nl = min(
            100.0,
            float(latest_loy.loyalty)
            + float(constants.INNER_CIRCLE_GRANT_LUXURIES_BUNKER_LOYALTY_DELTA),
        )
        db.session.add(BunkerLoyalty(user_id=user_id, loyalty=nl, timestamp=now))
    sync_aggregate_inner_circle_loyalty(user_id)
    return None


def try_start_stage_incident(user_id: str, slot: int, now: datetime) -> str | None:
    if slot < 0 or slot >= constants.INNER_CIRCLE_MEMBER_COUNT:
        return "bad_slot"
    seed_members_for_user_if_needed(user_id)
    m = db.session.get(InnerCircleMember, (user_id, slot))
    if m is None:
        return "no_member"
    if member_is_busy(m, now):
        return "busy"
    if float(m.popularity) + 1e-9 < float(constants.INNER_CIRCLE_POPULARITY_MIN_STAGE_INCIDENT):
        return "low_popularity"
    _start_task(
        m,
        constants.INNER_CIRCLE_TASK_STAGE_INCIDENT,
        int(constants.INNER_CIRCLE_STAGE_INCIDENT_DURATION_SECONDS),
        now,
    )
    return None


def try_start_buy_groceries(user_id: str, slot: int, now: datetime) -> str | None:
    if slot < 0 or slot >= constants.INNER_CIRCLE_MEMBER_COUNT:
        return "bad_slot"
    seed_members_for_user_if_needed(user_id)
    m = db.session.get(InnerCircleMember, (user_id, slot))
    if m is None:
        return "no_member"
    if member_is_busy(m, now):
        return "busy"
    _start_task(
        m,
        constants.INNER_CIRCLE_TASK_BUY_GROCERIES,
        int(constants.INNER_CIRCLE_BUY_GROCERIES_DURATION_SECONDS),
        now,
    )
    return None


def try_start_temp_job(user_id: str, slot: int, now: datetime) -> str | None:
    if slot < 0 or slot >= constants.INNER_CIRCLE_MEMBER_COUNT:
        return "bad_slot"
    seed_members_for_user_if_needed(user_id)
    m = db.session.get(InnerCircleMember, (user_id, slot))
    if m is None:
        return "no_member"
    if member_is_busy(m, now):
        return "busy"
    _start_task(
        m,
        constants.INNER_CIRCLE_TASK_TEMP_JOB,
        int(constants.INNER_CIRCLE_TEMP_JOB_DURATION_SECONDS),
        now,
    )
    return None


def inner_circle_status_payload(user_id: str, now: datetime) -> dict[str, object]:
    seed_members_for_user_if_needed(user_id)
    social = db.session.get(BunkerSocialState, user_id)
    cash = float(social.inner_circle_cash) if social is not None else 0.0
    members_raw = db.session.scalars(
        select(InnerCircleMember)
        .where(InnerCircleMember.user_id == user_id)
        .order_by(InnerCircleMember.slot_index)
    ).all()
    members: list[dict[str, object]] = []
    pop_min = float(constants.INNER_CIRCLE_POPULARITY_MIN_STAGE_INCIDENT)
    for m in members_raw:
        busy = member_is_busy(m, now)
        prog = 0.0
        if busy and m.task_started_at is not None and m.task_ends_at is not None:
            total = (m.task_ends_at - m.task_started_at).total_seconds()
            if total > 1e-9:
                done = (min(now, m.task_ends_at) - m.task_started_at).total_seconds()
                prog = max(0.0, min(100.0, 100.0 * done / total))
        members.append(
            {
                "slot": m.slot_index,
                "name": m.display_name,
                "loyalty": float(m.loyalty),
                "popularity": float(m.popularity),
                "busy": busy,
                "task_kind": m.task_kind,
                "task_progress_percent": prog,
                "can_stage_incident": float(m.popularity) >= pop_min and not busy,
            }
        )
    return {
        "cash": cash,
        "popularity_min_for_stage_incident": pop_min,
        "members": members,
    }
