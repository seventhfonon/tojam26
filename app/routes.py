"""HTTP routes for the Flask app.

The Flask side is intentionally tiny: it exists to mint and persist a player
UUID, then redirect the player into Grafana with that UUID injected as a
dashboard variable. All actual gameplay UI lives in Grafana.
"""

from __future__ import annotations

from urllib.parse import urlencode
from uuid import UUID, uuid4

from flask import Blueprint, current_app, make_response, redirect, request

from .extensions import db
from .models import RadiationLevel, User


bp = Blueprint("main", __name__)


def _is_valid_uuid(value: str | None) -> bool:
    if not value:
        return False
    try:
        UUID(value)
    except (ValueError, TypeError):
        return False
    return True


def _get_or_create_player() -> User:
    cookie_name = current_app.config["USER_COOKIE_NAME"]
    existing = request.cookies.get(cookie_name)

    if _is_valid_uuid(existing):
        user = db.session.get(User, existing)
        if user is not None:
            return user

    user = User(id=str(uuid4()))
    db.session.add(user)
    db.session.add(
        RadiationLevel(
            user_id=user.id,
            level=current_app.config["INITIAL_RADIATION"],
        )
    )
    db.session.commit()
    return user


@bp.route("/")
def index():
    """Mint/reuse a player UUID and redirect into Grafana."""
    user = _get_or_create_player()

    grafana_url = current_app.config["GRAFANA_URL"].rstrip("/")
    dashboard_uid = current_app.config["GRAFANA_DASHBOARD_UID"]
    params = urlencode({"var-user_id": user.id, "kiosk": "tv"})
    target = f"{grafana_url}/d/{dashboard_uid}?{params}"

    response = make_response(redirect(target))
    response.set_cookie(
        current_app.config["USER_COOKIE_NAME"],
        user.id,
        max_age=current_app.config["USER_COOKIE_MAX_AGE"],
        httponly=True,
        samesite="Lax",
    )
    return response


@bp.route("/healthz")
def healthz():
    return {"status": "ok"}
