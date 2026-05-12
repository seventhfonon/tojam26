"""Focus Tree dashboard: discrete research nodes with parent gates (branch / merge).

Some nodes require extra runtime predicates (population, inner-circle cash, social flags).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select

from . import constants
from . import strings
from .events import EventDefinition, spawn_event_for_focus_completion
from .extensions import db
from .models import (
    BunkerBoredom,
    BunkerLoyalty,
    BunkerPopulation,
    BunkerSocialState,
    FocusTreeCompletion,
    InnerCircleMember,
    User,
)


@dataclass(frozen=True)
class FocusNodeDef:
    id: str
    title: str
    description: str
    requirements: str
    parent_ids: tuple[str, ...]
    spawn_event: EventDefinition | None = None
    completion_hook: str | None = None
    predicate_key: str | None = None


FOCUS_TREE_NODES: tuple[FocusNodeDef, ...] = (
    FocusNodeDef(
        id="ft_explore_novel_food_sources",
        title=strings.FOCUS_TITLE_EXPLORE_NOVEL_FOOD,
        description=strings.FOCUS_DESC_EXPLORE_NOVEL_FOOD,
        requirements=strings.FOCUS_REQ_EXPLORE_NOVEL_FOOD,
        parent_ids=(),
        completion_hook="rat_trappers_unlock",
    ),
    FocusNodeDef(
        id="ft_fireside_chats",
        title=strings.FOCUS_TITLE_FIRESIDE_CHATS,
        description=strings.FOCUS_DESC_FIRESIDE_CHATS,
        requirements=strings.FOCUS_REQ_FIRESIDE_CHATS,
        parent_ids=("ft_explore_novel_food_sources",),
        predicate_key="fireside_chats_gate",
    ),
    FocusNodeDef(
        id="ft_bunker_shakespeare_company",
        title=strings.FOCUS_TITLE_BUNKER_SHAKESPEARE,
        description=strings.FOCUS_DESC_BUNKER_SHAKESPEARE,
        requirements=strings.FOCUS_REQ_BUNKER_SHAKESPEARE_TEMPLATE.format(
            loyalty_below=constants.SHAKESPEARE_FOCUS_LOYALTY_BELOW,
            boredom_above=constants.SHAKESPEARE_FOCUS_BOREDOM_ABOVE,
        ),
        parent_ids=("ft_explore_novel_food_sources",),
        predicate_key="bunker_shakespeare_gate",
    ),
    FocusNodeDef(
        id="ft_venture_out",
        title=strings.FOCUS_TITLE_VENTURE_OUT,
        description=strings.FOCUS_DESC_VENTURE_OUT,
        requirements=strings.FOCUS_REQ_VENTURE_OUT,
        parent_ids=("ft_fireside_chats", "ft_bunker_shakespeare_company"),
        predicate_key="venture_out_gate",
        completion_hook="venture_out_narrative",
    ),
    FocusNodeDef(
        id="ft_worse_than_being_exploited",
        title=strings.FOCUS_TITLE_WORSE_THAN_EXPLOITED,
        description=strings.FOCUS_DESC_WORSE_THAN_EXPLOITED,
        requirements=strings.FOCUS_REQ_TEMP_JOB_BRANCH_TEMPLATE.format(
            cash_threshold=constants.TEMP_JOB_FOCUS_CASH_THRESHOLD,
        ),
        parent_ids=("ft_venture_out",),
        predicate_key="cash_below_temp_threshold_gate",
    ),
    FocusNodeDef(
        id="ft_not_being_exploited",
        title=strings.FOCUS_TITLE_NOT_BEING_EXPLOITED,
        description=strings.FOCUS_DESC_NOT_BEING_EXPLOITED,
        requirements=strings.FOCUS_REQ_NOT_BEING_EXPLOITED,
        parent_ids=("ft_venture_out",),
        predicate_key="temp_job_backfire_gate",
    ),
    FocusNodeDef(
        id="ft_fire_and_brimstone",
        title=strings.FOCUS_TITLE_FIRE_AND_BRIMSTONE,
        description=strings.FOCUS_DESC_FIRE_AND_BRIMSTONE,
        requirements=strings.FOCUS_REQ_FIRE_AND_BRIMSTONE,
        parent_ids=("ft_worse_than_being_exploited", "ft_not_being_exploited"),
    ),
)

_FOCUS_BY_ID: dict[str, FocusNodeDef] = {n.id: n for n in FOCUS_TREE_NODES}


def completed_node_ids(user_id: str) -> set[str]:
    rows = db.session.scalars(
        select(FocusTreeCompletion.node_id).where(FocusTreeCompletion.user_id == user_id)
    ).all()
    return set(rows)


def user_completed_focus(user_id: str, node_id: str) -> bool:
    return node_id in completed_node_ids(user_id)


def parents_satisfied(done: set[str], node: FocusNodeDef) -> bool:
    return all(pid in done for pid in node.parent_ids)


def _pred_fireside_chats_gate(user_id: str) -> bool:
    soc = db.session.get(BunkerSocialState, user_id)
    return bool(soc and soc.fireside_chats_focus_gate_done)


def _pred_bunker_shakespeare_gate(user_id: str) -> bool:
    latest_loy = db.session.scalars(
        select(BunkerLoyalty)
        .where(BunkerLoyalty.user_id == user_id)
        .order_by(BunkerLoyalty.timestamp.desc())
        .limit(1)
    ).first()
    latest_boredom = db.session.scalars(
        select(BunkerBoredom)
        .where(BunkerBoredom.user_id == user_id)
        .order_by(BunkerBoredom.timestamp.desc())
        .limit(1)
    ).first()
    if latest_loy is not None and float(latest_loy.loyalty) < float(
        constants.SHAKESPEARE_FOCUS_LOYALTY_BELOW
    ):
        return True
    if latest_boredom is not None and float(latest_boredom.boredom) > float(
        constants.SHAKESPEARE_FOCUS_BOREDOM_ABOVE
    ):
        return True
    return False


def _pred_venture_out_gate(user_id: str) -> bool:
    pop_row = db.session.scalars(
        select(BunkerPopulation)
        .where(BunkerPopulation.user_id == user_id)
        .order_by(BunkerPopulation.timestamp.desc())
        .limit(1)
    ).first()
    if pop_row is None:
        return False
    threshold = int(float(constants.INITIAL_POPULATION) * (2.0 / 3.0) + 1e-9)
    return int(pop_row.count) < threshold


def _pred_cash_below_temp_threshold_gate(user_id: str) -> bool:
    soc = db.session.get(BunkerSocialState, user_id)
    if soc is None:
        return False
    return float(soc.inner_circle_cash) < float(constants.TEMP_JOB_FOCUS_CASH_THRESHOLD)


def _pred_temp_job_backfire_gate(user_id: str) -> bool:
    soc = db.session.get(BunkerSocialState, user_id)
    return bool(soc and soc.temp_job_backfire_seen)


_FOCUS_PREDICATES: dict[str, Callable[[str], bool]] = {
    "fireside_chats_gate": _pred_fireside_chats_gate,
    "bunker_shakespeare_gate": _pred_bunker_shakespeare_gate,
    "venture_out_gate": _pred_venture_out_gate,
    "cash_below_temp_threshold_gate": _pred_cash_below_temp_threshold_gate,
    "temp_job_backfire_gate": _pred_temp_job_backfire_gate,
}


def predicate_satisfied(user_id: str, node: FocusNodeDef) -> bool:
    if node.predicate_key is None:
        return True
    fn = _FOCUS_PREDICATES.get(node.predicate_key)
    return True if fn is None else bool(fn(user_id))


def focus_tree_status_payload(user_id: str | None) -> dict[str, object]:
    empty_nodes = {
        n.id: {"completed": False, "can_activate": False} for n in FOCUS_TREE_NODES
    }
    if user_id is None:
        return {"nodes": empty_nodes}

    done = completed_node_ids(user_id)
    out: dict[str, dict[str, bool]] = {}
    for n in FOCUS_TREE_NODES:
        completed = n.id in done
        can_activate = (
            not completed
            and parents_satisfied(done, n)
            and predicate_satisfied(user_id, n)
        )
        out[n.id] = {"completed": completed, "can_activate": can_activate}
    return {"nodes": out}


def _hook_rat_trappers_unlock(user_id: str, when: datetime) -> None:
    from .constants import MESSAGE_CHANNEL_BULLETIN
    from .events import _post_player_message

    row = db.session.get(User, user_id)
    if row is not None:
        row.rat_trappers_unlocked = True
    _post_player_message(
        user_id,
        strings.MESSAGE_RAT_TRAPPERS_UNLOCKED,
        when,
        channel=MESSAGE_CHANNEL_BULLETIN,
    )


def _hook_venture_out(user_id: str, when: datetime) -> None:
    from . import inner_circle
    from .constants import MESSAGE_CHANNEL_GROUP_CHAT
    from .events import _post_player_message

    inner_circle.seed_members_for_user_if_needed(user_id)
    slot = int(constants.VENTURE_OUT_DEPARTING_MEMBER_SLOT)
    name = constants.INNER_CIRCLE_MEMBER_NAMES[slot]
    m = db.session.get(InnerCircleMember, (user_id, slot))
    if m is not None:
        m.departed = True
    body = constants.VENTURE_OUT_FAREWELL_MESSAGE.format(name=name)
    _post_player_message(user_id, body, when, channel=MESSAGE_CHANNEL_GROUP_CHAT)


_COMPLETION_HOOKS: dict[str, Callable[[str, datetime], None]] = {
    "rat_trappers_unlock": _hook_rat_trappers_unlock,
    "venture_out_narrative": _hook_venture_out,
}


def try_complete_focus(user_id: str, node_id: str, now: datetime) -> bool:
    node = _FOCUS_BY_ID.get(node_id)
    if node is None:
        return False
    done = completed_node_ids(user_id)
    if node.id in done or not parents_satisfied(done, node):
        return False
    if not predicate_satisfied(user_id, node):
        return False
    db.session.add(
        FocusTreeCompletion(user_id=user_id, node_id=node.id, completed_at=now)
    )
    if node.spawn_event is not None:
        spawn_event_for_focus_completion(user_id, node.spawn_event, now)
    hook_key = node.completion_hook
    if hook_key is not None:
        hook = _COMPLETION_HOOKS.get(hook_key)
        if hook is not None:
            hook(user_id, now)
    return True
