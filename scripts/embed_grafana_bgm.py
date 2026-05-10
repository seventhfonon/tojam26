#!/usr/bin/env python3
"""Merge BGM templating variables + audio controls into the Silo Command nav panel."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNIPPET = ROOT / "grafana" / "bgm-panel-snippet.html"

BGM_URL = "http://localhost:5001/assets/audio/JazzWithAmbience.ogg"

NAV_PANEL_ID = 1
BGM_PANEL_ID = 200

MARKER_START = "<!--silo-bgm-injected-->"
MARKER_END = "<!--/silo-bgm-injected-->"

DASHBOARDS: list[str] = [
    "grafana/dashboards/environment.json",
    "grafana/dashboards/power.json",
    "grafana/dashboards/farming.json",
    "grafana/dashboards/community.json",
    "grafana/dashboards/inner-circle.json",
    "grafana/dashboards/focus-tree.json",
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


def _strip_leading_html_comment(html: str) -> str:
    h = html.lstrip()
    if h.startswith("<!--"):
        end = h.find("-->")
        if end != -1:
            h = h[end + 3 :].lstrip()
    return h


def _nav_markup(raw_snippet: str) -> str:
    inner = _strip_leading_html_comment(raw_snippet)
    inner = inner.replace(
        'class="silo-bgm-bar"',
        'class="silo-bgm-bar silo-bgm-bar--nav"',
        1,
    )
    return f"{MARKER_START}{inner}{MARKER_END}"


def _remove_nav_bgm(content: str) -> str:
    out = content
    while True:
        i = out.find(MARKER_START)
        if i == -1:
            break
        j = out.find(MARKER_END, i)
        if j == -1:
            break
        out = out[:i] + out[j + len(MARKER_END) :]
    return out


def _inject_nav_bgm(content: str, nav_markup: str) -> str:
    content = _remove_nav_bgm(content)
    idx = content.rfind("</div>")
    if idx == -1:
        msg = "Cannot find closing </div> for BGM nav injection"
        raise ValueError(msg)
    return content[:idx] + nav_markup + content[idx:]


def main() -> None:
    raw = SNIPPET.read_text(encoding="utf-8")
    nav_markup = _nav_markup(raw)
    for rel in DASHBOARDS:
        path = ROOT / rel
        data = json.loads(path.read_text(encoding="utf-8"))
        lst = data.setdefault("templating", {}).setdefault("list", [])
        uid = str(data.get("uid", ""))
        if not any(v.get("name") == "dashboard_uid" for v in lst):
            lst.append(_const("dashboard_uid", uid))
        if not any(v.get("name") == "bgm_url" for v in lst):
            lst.append(_const("bgm_url", BGM_URL))
        panels = data.setdefault("panels", [])
        data["panels"] = [
            p
            for p in panels
            if not (p.get("id") == BGM_PANEL_ID and p.get("type") == "text")
        ]
        nav_ok = False
        for p in data["panels"]:
            if p.get("id") != NAV_PANEL_ID or p.get("type") != "text":
                continue
            opts = p.setdefault("options", {})
            opts["mode"] = "html"
            c = str(opts.get("content", ""))
            opts["content"] = _inject_nav_bgm(c, nav_markup)
            nav_ok = True
            break
        if not nav_ok:
            msg = f"No text nav panel (id={NAV_PANEL_ID}) in {rel}"
            raise RuntimeError(msg)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print("Updated", len(DASHBOARDS), "dashboards (BGM in Silo Command nav)")


if __name__ == "__main__":
    main()
