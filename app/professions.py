"""Stable labels for profession assignment rows and Grafana snapshots.

Mutable counts live in :class:`~app.models.BunkerProfession` (one row per user per
profession, including ``Investigation`` during incident responses). Worker systems
reference the crank/farming rows via FK; each tick we append copies to
:class:`~app.models.BunkerProfessionSnapshot` for charts.
"""

from __future__ import annotations

from .strings import (
    PROFESSION_FARMING,
    PROFESSION_IDLE,
    PROFESSION_INVESTIGATION,
    PROFESSION_POWER_CRANK,
    PROFESSION_RAT_TRAPPING,
    PROFESSION_THEATRE,
)

# Stored in DB and shown in Grafana; keep stable when changing UI copy.
PROFESSION_REPORT_ORDER: tuple[str, ...] = (
    PROFESSION_POWER_CRANK,
    PROFESSION_FARMING,
    PROFESSION_RAT_TRAPPING,
    PROFESSION_THEATRE,
    PROFESSION_INVESTIGATION,
    PROFESSION_IDLE,
)
