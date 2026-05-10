"""Declarative visibility gates for Grafana dashboards (UI-only hiding).

Grafana OSS panels cannot be toggled natively from gameplay state. We expose
``GET /systems/ui-gates`` and poll it from a tiny script embedded in each
dashboard's nav Text panel. Scripts locate panels by numeric ``data-panel-id``
and set ``display: none`` on an ancestor section.

**Caveats**

- **Panel IDs drift** when panels are duplicated or reordered in Grafana; update
  :data:`UI_GATE_RULES_BY_DASHBOARD` whenever ``grafana/dashboards/*.json`` changes.
  The Farming dashboard's rat-trapper panels instead read ``gates`` from this
  endpoint and hide their own grid tile (``react-grid-item``): Grafana's DOM does
  not reliably expose stable ``data-panel-id`` roots for the nav bootstrap hider.
- **DOM structure** depends on Grafana major/minor versions; selectors may need
  adjustment (same class of risk as Community/Environment heatmap hacks).
- Hiding panels does **not** enforce authorization; Flask action routes remain the
  source of truth for mutations.

Gate naming
-----------

- ``focus_<node_id>_complete`` — ``True`` iff that Focus Tree node is completed
  (see :data:`app.focus_tree.FOCUS_TREE_NODES`).
- ``event_<EventDefinition.value>_active`` — ``True`` iff the player has an active
  :class:`~app.models.PlayerActiveEvent` row for that kind.
- ``silo_rats_introduced`` — ``True`` iff ``User.silo_rats_introduced`` is set (persists
  after ``rats_silo_intro`` spawns). Farming dashboard rat-trapper tiles consult
  ``focus_ft_explore_novel_food_sources_complete`` for visibility (trapper unlock).
"""

from __future__ import annotations

from dataclasses import dataclass

from .events import EventDefinition, player_has_active_event_kind
from .extensions import db
from .focus_tree import FOCUS_TREE_NODES, completed_node_ids
from .models import User


@dataclass(frozen=True)
class UiGateRule:
    """When ``gate`` evaluates **false**, hide all listed Grafana panel IDs."""

    gate: str
    hide_panel_ids: tuple[int, ...]


def evaluate_gate_state(user_id: str) -> dict[str, bool]:
    """Return named booleans for focus completions, active random events, and User flags."""
    done = completed_node_ids(user_id)
    gates: dict[str, bool] = {}
    for n in FOCUS_TREE_NODES:
        gates[f"focus_{n.id}_complete"] = n.id in done
    for ev in EventDefinition:
        gates[f"event_{ev.value}_active"] = player_has_active_event_kind(
            user_id, ev
        )
    row = db.session.get(User, user_id)
    gates["silo_rats_introduced"] = (
        bool(row.silo_rats_introduced) if row is not None else False
    )
    return gates


#: Map Grafana dashboard ``uid`` → rules. Empty tuple = no hiding for that dash.
#: Add rows here as product needs (see module docstring for gate keys).
UI_GATE_RULES_BY_DASHBOARD: dict[str, tuple[UiGateRule, ...]] = {}


def hide_panel_ids_for_dashboard(
    user_id: str | None, dashboard_uid: str
) -> tuple[list[int], dict[str, bool]]:
    """Return sorted unique panel IDs to hide and the gate snapshot."""
    if not user_id or not dashboard_uid:
        return [], {}
    gates = evaluate_gate_state(user_id)
    rules = UI_GATE_RULES_BY_DASHBOARD.get(dashboard_uid, ())
    hidden: list[int] = []
    for rule in rules:
        if not gates.get(rule.gate, False):
            hidden.extend(rule.hide_panel_ids)
    # Stable unique sort
    uniq = sorted(set(hidden))
    return uniq, gates


def ui_gates_payload(user_id: str | None, dashboard_uid: str) -> dict[str, object]:
    hide_ids, gates = hide_panel_ids_for_dashboard(user_id, dashboard_uid)
    return {
        "dashboard_uid": dashboard_uid,
        "hide_panel_ids": hide_ids,
        "gates": gates,
    }
