"""Stable labels for profession assignment rows and Grafana snapshots.

Mutable counts live in :class:`~app.models.BunkerProfession` (one row per user per
profession, including ``Investigation`` during incident responses). Worker systems
reference the crank/farming rows via FK; each tick we append copies to
:class:`~app.models.BunkerProfessionSnapshot` for charts.
"""

from __future__ import annotations

# Stored in DB and shown in Grafana; keep stable when changing UI copy.
PROFESSION_IDLE = "Idle"
PROFESSION_POWER_CRANK = "Power crank"
PROFESSION_FARMING = "Farming"
PROFESSION_RAT_TRAPPING = "Rat trapping"
PROFESSION_INVESTIGATION = "Investigation"

PROFESSION_REPORT_ORDER: tuple[str, ...] = (
    PROFESSION_POWER_CRANK,
    PROFESSION_FARMING,
    PROFESSION_RAT_TRAPPING,
    PROFESSION_INVESTIGATION,
    PROFESSION_IDLE,
)
