#!/usr/bin/env python3
"""Merge BGM templating variables + Text panel into provisioned Grafana dashboards."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNIPPET = ROOT / "grafana" / "bgm-panel-snippet.html"

BGM_URL = "http://localhost:5001/assets/audio/ode_to_joy.mp3"

DASHBOARDS: list[tuple[str, str, int]] = [
    ("grafana/dashboards/environment.json", "silo-environment", 66),
    ("grafana/dashboards/power.json", "silo-power", 30),
    ("grafana/dashboards/farming.json", "silo-farming", 43),
    ("grafana/dashboards/community.json", "silo-community", 55),
    ("grafana/dashboards/inner-circle.json", "silo-inner-circle", 62),
    ("grafana/dashboards/focus-tree.json", "silo-focus-tree", 36),
]


def _const(name: str, value: str) -> dict[str, object]:
    return {
        "name": name,
        "type": "constant",
        "query": value,
        "hide": 2,
        "current": {"selected": True, "text": value, "value": value},
        "options": [{"selected": True, "text": value, "value": value}],
        "skipUrlSync": True,
    }


def _panel(y: int, html: str) -> dict[str, object]:
    return {
        "id": 200,
        "type": "text",
        "title": "BGM",
        "datasource": None,
        "gridPos": {"x": 0, "y": y, "w": 24, "h": 2},
        "options": {"mode": "html", "content": html},
    }


def main() -> None:
    html = SNIPPET.read_text(encoding="utf-8")
    for rel, uid, y in DASHBOARDS:
        path = ROOT / rel
        data = json.loads(path.read_text(encoding="utf-8"))
        lst = data.setdefault("templating", {}).setdefault("list", [])
        if not any(v.get("name") == "dashboard_uid" for v in lst):
            lst.append(_const("dashboard_uid", uid))
        if not any(v.get("name") == "bgm_url" for v in lst):
            lst.append(_const("bgm_url", BGM_URL))
        panels = data.setdefault("panels", [])
        if not any(p.get("id") == 200 for p in panels):
            panels.append(_panel(y, html))
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print("Updated", len(DASHBOARDS), "dashboards")


if __name__ == "__main__":
    main()
