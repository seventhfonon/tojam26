"""HTTP routes for the Flask app.

The Flask side is intentionally tiny: it exists to mint and persist a player
UUID, then redirect the player into Grafana with that UUID injected as a
dashboard variable. All actual gameplay UI lives in Grafana.

Action endpoints
----------------
Player interactions in Grafana are implemented as ordinary GET requests to
Flask that mutate game state. The Grafana Text panel uses JavaScript to fire
these requests into a hidden <iframe>, so the main dashboard page never
navigates away and no new tab is opened.

When Flask detects the request came from an iframe (via the standard
Sec-Fetch-Dest: iframe browser header), it returns 204 No Content instead
of a redirect, so the browser doesn't waste a full Grafana page load inside
the hidden frame. Direct browser access (e.g. for debugging) still redirects
to Grafana as before.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from urllib.parse import urlencode
from uuid import UUID, uuid4

from flask import Blueprint, current_app, make_response, redirect, request
from sqlalchemy import select

from .extensions import db
from .models import BunkerLoyalty, BunkerPopulation, BunkerSystems, EnergyReserve, RadiationLevel, User


log = logging.getLogger(__name__)
bp = Blueprint("main", __name__)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _is_valid_uuid(value: str | None) -> bool:
    if not value:
        return False
    try:
        UUID(value)
    except (ValueError, TypeError):
        return False
    return True


def _grafana_url(user_id: str) -> str:
    grafana_base = current_app.config["GRAFANA_URL"].rstrip("/")
    dashboard_uid = current_app.config["GRAFANA_DASHBOARD_UID"]
    params = urlencode({"var-user_id": user_id, "kiosk": "tv"})
    return f"{grafana_base}/d/{dashboard_uid}?{params}"


def _redirect_to_grafana(user_id: str):
    return redirect(_grafana_url(user_id))


def _action_response(user_id: str):
    """Return the right response for an action endpoint.

    When called from the hidden iframe (Sec-Fetch-Dest: iframe), return
    204 No Content — the action is done, nothing needs to be displayed.
    When accessed directly (e.g. from the address bar for debugging),
    redirect to the Grafana dashboard as before.
    """
    if request.headers.get("Sec-Fetch-Dest") == "iframe":
        return ("", 204)
    return _redirect_to_grafana(user_id)


def _set_player_cookie(response, user_id: str):
    response.set_cookie(
        current_app.config["USER_COOKIE_NAME"],
        user_id,
        max_age=current_app.config["USER_COOKIE_MAX_AGE"],
        httponly=True,
        samesite="Lax",
    )


def _identify_player() -> User | None:
    """Return the current player from cookie (preferred) or ?user_id= param."""
    cookie_name = current_app.config["USER_COOKIE_NAME"]
    user_id = request.cookies.get(cookie_name)
    if not _is_valid_uuid(user_id):
        user_id = request.args.get("user_id")
    if not _is_valid_uuid(user_id):
        return None
    return db.session.get(User, user_id)


def _create_player() -> User:
    """Always mint a fresh UUID and seed all game-state tables."""
    user = User(id=str(uuid4()))
    db.session.add(user)
    db.session.add(RadiationLevel(
        user_id=user.id,
        level=current_app.config["INITIAL_RADIATION"],
    ))
    db.session.add(BunkerPopulation(
        user_id=user.id,
        count=current_app.config["INITIAL_POPULATION"],
        departed=0,
    ))
    db.session.add(BunkerLoyalty(
        user_id=user.id,
        loyalty=current_app.config["INITIAL_LOYALTY"],
    ))
    db.session.add(EnergyReserve(
        user_id=user.id,
        level=current_app.config["INITIAL_ENERGY"],
    ))
    db.session.add(BunkerSystems(
        user_id=user.id,
        lights_on=True,
        crank_workers=0,
    ))
    db.session.commit()
    return user


def _get_or_create_player() -> User:
    """Return existing player from cookie, or create a new one."""
    cookie_name = current_app.config["USER_COOKIE_NAME"]
    existing = request.cookies.get(cookie_name)

    if _is_valid_uuid(existing):
        user = db.session.get(User, existing)
        if user is not None:
            return user

    return _create_player()


# ---------------------------------------------------------------------------
# Landing routes
# ---------------------------------------------------------------------------

@bp.route("/")
def index():
    """Mint/reuse a player UUID and redirect into Grafana."""
    user = _get_or_create_player()
    response = make_response(_redirect_to_grafana(user.id))
    _set_player_cookie(response, user.id)
    return response


@bp.route("/new")
def new_session():
    """Always start a brand-new player session, ignoring any existing cookie.

    Useful during development to spin up a fresh game state without needing to
    clear browser cookies or restart the container.
    """
    user = _create_player()
    response = make_response(_redirect_to_grafana(user.id))
    _set_player_cookie(response, user.id)
    return response


# ---------------------------------------------------------------------------
# Action endpoints — mutate game state, redirect back to Grafana
# ---------------------------------------------------------------------------

@bp.route("/action/crank")
def action_crank():
    """Add a burst of energy from a manual crank press."""
    user = _identify_player()
    if user is None:
        return redirect("/new")

    latest = db.session.scalars(
        select(EnergyReserve)
        .where(EnergyReserve.user_id == user.id)
        .order_by(EnergyReserve.timestamp.desc())
        .limit(1)
    ).first()

    if latest is not None:
        new_level = latest.level + current_app.config["MANUAL_CRANK_ENERGY"]
        db.session.add(EnergyReserve(
            user_id=user.id,
            level=new_level,
            timestamp=datetime.now(timezone.utc),
        ))
        db.session.commit()
        log.info("manual crank: user=%s energy %.2f→%.2f", user.id, latest.level, new_level)

    return _action_response(user.id)


@bp.route("/action/toggle-lights")
def action_toggle_lights():
    """Flip the lights on or off."""
    user = _identify_player()
    if user is None:
        return redirect("/new")

    systems = db.session.get(BunkerSystems, user.id)
    if systems is not None:
        systems.lights_on = not systems.lights_on
        systems.updated_at = datetime.now(timezone.utc)
        db.session.commit()
        log.info("toggle lights: user=%s lights=%s", user.id, systems.lights_on)

    return _action_response(user.id)


@bp.route("/action/adjust-crank")
def action_adjust_crank():
    """Increment or decrement the number of workers on the power crank.

    ``?delta=1`` adds one worker; ``?delta=-1`` removes one.
    Result is clamped to [0, current population].
    Workers above CRANK_WORKERS_LOYALTY_THRESHOLD reduce loyalty each tick.
    """
    user = _identify_player()
    if user is None:
        return redirect("/new")

    try:
        delta = int(request.args.get("delta", 0))
    except (ValueError, TypeError):
        delta = 0

    latest_pop = db.session.scalars(
        select(BunkerPopulation)
        .where(BunkerPopulation.user_id == user.id)
        .order_by(BunkerPopulation.timestamp.desc())
        .limit(1)
    ).first()
    max_workers = latest_pop.count if latest_pop is not None else 0

    systems = db.session.get(BunkerSystems, user.id)
    if systems is not None:
        systems.crank_workers = max(0, min(systems.crank_workers + delta, max_workers))
        systems.updated_at = datetime.now(timezone.utc)
        db.session.commit()
        log.info("adjust crank workers: user=%s delta=%+d workers=%d", user.id, delta, systems.crank_workers)

    return _action_response(user.id)


@bp.route("/healthz")
def healthz():
    return {"status": "ok"}
