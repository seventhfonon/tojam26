"""Focus Tree dashboard: discrete research nodes with parent gates (branch / merge).

Tree shape is code-defined in :data:`FOCUS_TREE_NODES`. Merge nodes list two parents;
their button stays disabled until both upstream nodes are completed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select

from .events import EventDefinition, spawn_event_for_focus_completion
from .extensions import db
from .models import FocusTreeCompletion


@dataclass(frozen=True)
class FocusNodeDef:
    id: str
    title: str
    description: str
    requirements: str
    parent_ids: tuple[str, ...]
    #: When set, completing this focus spawns this gameplay event (same hooks as RNG spawn).
    spawn_event: EventDefinition | None = None


FOCUS_TREE_NODES: tuple[FocusNodeDef, ...] = (
    FocusNodeDef(
        id="ft_command_coordination",
        title="Command coordination",
        description=(
            "Bring silo departments onto a single incident channel so downstream "
            "plans do not fork silently."
        ),
        requirements="No prerequisites — establish the chain of command.",
        parent_ids=(),
        spawn_event=EventDefinition.RATS_SILO_INTRO,
    ),
    FocusNodeDef(
        id="ft_supply_audit",
        title="Supply lane audit",
        description=(
            "Inventory spare hydro filters and crank lubricant before parallel work "
            "stress-tests both corridors."
        ),
        requirements="Complete Command coordination.",
        parent_ids=("ft_command_coordination",),
        spawn_event=EventDefinition.RATS_SILO,
    ),
    FocusNodeDef(
        id="ft_waste_loop_review",
        title="Waste-loop review",
        description=(
            "Verify compost return paths and gray-water routing so expanded farming "
            "does not starve power cooling loops."
        ),
        requirements="Complete Command coordination.",
        parent_ids=("ft_command_coordination",),
        spawn_event=EventDefinition.RATS_SILO,
    ),
    FocusNodeDef(
        id="ft_joint_readiness_board",
        title="Joint readiness board",
        description=(
            "Merge hydro and power schedules into one board so neither corridor "
            "claims the same maintenance window."
        ),
        requirements=(
            "Both Supply lane audit and Waste-loop review must be marked complete "
            "before this focus unlocks."
        ),
        parent_ids=("ft_supply_audit", "ft_waste_loop_review"),
        spawn_event=EventDefinition.FIRESIDE_RHETORIC_BACKLASH,
    ),
    FocusNodeDef(
        id="ft_surface_handshake",
        title="Surface handshake drill",
        description=(
            "Timed ping drill with the antenna mast — proves merged logistics can "
            "support a single outbound push."
        ),
        requirements="Complete Joint readiness board.",
        parent_ids=("ft_joint_readiness_board",),
        spawn_event=EventDefinition.GEIGER_RUMOR_EXODUS,
    ),
)

_FOCUS_BY_ID: dict[str, FocusNodeDef] = {n.id: n for n in FOCUS_TREE_NODES}


def completed_node_ids(user_id: str) -> set[str]:
    rows = db.session.scalars(
        select(FocusTreeCompletion.node_id).where(FocusTreeCompletion.user_id == user_id)
    ).all()
    return set(rows)


def parents_satisfied(done: set[str], node: FocusNodeDef) -> bool:
    return all(pid in done for pid in node.parent_ids)


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
        can_activate = not completed and parents_satisfied(done, n)
        out[n.id] = {"completed": completed, "can_activate": can_activate}
    return {"nodes": out}


def try_complete_focus(user_id: str, node_id: str, now: datetime) -> bool:
    node = _FOCUS_BY_ID.get(node_id)
    if node is None:
        return False
    done = completed_node_ids(user_id)
    if node.id in done or not parents_satisfied(done, node):
        return False
    db.session.add(
        FocusTreeCompletion(user_id=user_id, node_id=node.id, completed_at=now)
    )
    if node.spawn_event is not None:
        spawn_event_for_focus_completion(user_id, node.spawn_event, now)
    return True
