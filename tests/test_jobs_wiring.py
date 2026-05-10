"""Regression guards: ``game_tick`` wires narrative + random events via imports.

Dropping ``from .narrative import … deliver_pending_narrative_messages`` caused a
runtime ``NameError`` every tick while HTTP routes still worked — easy to miss.
"""


from __future__ import annotations

import ast
from pathlib import Path


def test_jobs_imports_deliver_pending_narrative_messages():
    import app.jobs as jobs

    fn = getattr(jobs, "deliver_pending_narrative_messages", None)
    assert callable(fn), (
        "game_tick must call deliver_pending_narrative_messages; "
        "import it from app.narrative into app.jobs"
    )


def test_jobs_imports_random_event_hooks():
    import app.jobs as jobs

    required = (
        "active_event_tick_effects",
        "auto_resolve_if_due",
        "finalize_investigation_if_due",
        "player_has_active_event_kind",
        "player_has_any_active_event",
        "try_spawn_event",
    )
    for name in required:
        assert hasattr(jobs, name), f"app.jobs must import / expose {name} from app.events"


def test_game_tick_ast_references_narrative_and_events():
    """Fails if call sites are renamed without updating imports."""
    jobs_path = Path(__file__).resolve().parents[1] / "app" / "jobs.py"
    tree = ast.parse(jobs_path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)

    assert "deliver_pending_narrative_messages" in names
    assert "try_spawn_event" in names
    assert "finalize_investigation_if_due" in names
    assert "record_environment_pixel_noise_sample" in names
    assert "record_social_movie_pixel_sample" in names
