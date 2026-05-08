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
import math
import random
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from uuid import UUID, uuid4

from flask import Blueprint, current_app, jsonify, make_response, redirect, request
from sqlalchemy import select

from .extensions import db
from .events import active_event_status_payload, spec_for_kind, try_player_resolve
from .jobs import noisy_radiation_display
from .models import (
    BunkerBoredom,
    BunkerDoubt,
    BunkerLoyalty,
    BunkerPopulation,
    BunkerSocialState,
    BunkerSystems,
    EnergyReserve,
    FoodReserve,
    RadiationLevel,
    SystemMessage,
    User,
)
from .social_flavor import NEGATIVE_COUNCIL_MESSAGES, POSITIVE_COUNCIL_MESSAGES


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


def _get_or_create_social_state(user_id: str) -> BunkerSocialState:
    row = db.session.get(BunkerSocialState, user_id)
    if row is None:
        row = BunkerSocialState(
            user_id=user_id,
            inner_circle_loyalty=current_app.config["INITIAL_INNER_CIRCLE_LOYALTY"],
        )
        db.session.add(row)
        db.session.commit()
    return row


def _social_cooldown_remaining_seconds(
    last_action_at: datetime | None, cooldown_seconds: int, now: datetime
) -> int:
    """Whole seconds until the action is usable; 0 if ready."""
    if last_action_at is None:
        return 0
    elapsed = (now - last_action_at).total_seconds()
    rem = float(cooldown_seconds) - elapsed
    return max(0, math.ceil(rem)) if rem > 0 else 0


def _create_player() -> User:
    """Always mint a fresh UUID and seed all game-state tables."""
    user = User(id=str(uuid4()))
    db.session.add(user)
    true_rad = current_app.config["INITIAL_RADIATION"]
    noise_max = current_app.config["RADIATION_DISPLAY_NOISE_MAX"]
    db.session.add(RadiationLevel(
        user_id=user.id,
        level=true_rad,
        level_display=noisy_radiation_display(true_rad, noise_max),
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
    db.session.add(FoodReserve(
        user_id=user.id,
        level=current_app.config["INITIAL_FOOD"],
        consumption_per_second=0.0,
        production_per_second=0.0,
    ))
    db.session.add(BunkerSystems(
        user_id=user.id,
        lights_on=True,
        crank_workers=0,
        food_workers=min(
            current_app.config["INITIAL_FARM_WORKERS"],
            current_app.config["INITIAL_POPULATION"],
        ),
        crop_ready_at=None,
    ))
    db.session.add(
        BunkerBoredom(
            user_id=user.id,
            boredom=current_app.config["INITIAL_BOREDOM"],
        )
    )
    db.session.add(
        BunkerDoubt(
            user_id=user.id,
            doubt=current_app.config["INITIAL_DOUBT"],
        )
    )
    db.session.add(
        BunkerSocialState(
            user_id=user.id,
            inner_circle_loyalty=current_app.config["INITIAL_INNER_CIRCLE_LOYALTY"],
        )
    )
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
        if delta > 0:
            systems.crank_workers += 1
        elif delta < 0:
            systems.crank_workers -= 1
        systems.crank_workers = max(0, min(systems.crank_workers, max_workers))
        while systems.crank_workers + systems.food_workers > max_workers and systems.food_workers > 0:
            systems.food_workers -= 1
        if systems.crank_workers + systems.food_workers > max_workers:
            systems.crank_workers = max(0, max_workers - systems.food_workers)
        systems.updated_at = datetime.now(timezone.utc)
        db.session.commit()
        log.info("adjust crank workers: user=%s delta=%+d crank=%d food=%d", user.id, delta, systems.crank_workers, systems.food_workers)

    return _action_response(user.id)


@bp.route("/action/adjust-food")
def action_adjust_food():
    """Increment or decrement farm workers (shared population pool with crank)."""
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
        if delta > 0:
            systems.food_workers += 1
        elif delta < 0:
            systems.food_workers -= 1
        systems.food_workers = max(0, min(systems.food_workers, max_workers))
        while systems.crank_workers + systems.food_workers > max_workers and systems.crank_workers > 0:
            systems.crank_workers -= 1
        if systems.crank_workers + systems.food_workers > max_workers:
            systems.food_workers = max(0, max_workers - systems.crank_workers)
        systems.updated_at = datetime.now(timezone.utc)
        db.session.commit()
        log.info("adjust food workers: user=%s delta=%+d crank=%d food=%d", user.id, delta, systems.crank_workers, systems.food_workers)

    return _action_response(user.id)


@bp.route("/action/plant-crops")
def action_plant_crops():
    """Start a crop timer; harvest unlocks after ``FARM_PLANT_GROWTH_SECONDS``."""
    user = _identify_player()
    if user is None:
        return redirect("/new")

    systems = db.session.get(BunkerSystems, user.id)
    if systems is not None and systems.crop_ready_at is None:
        growth = current_app.config["FARM_PLANT_GROWTH_SECONDS"]
        systems.crop_ready_at = datetime.now(timezone.utc) + timedelta(seconds=growth)
        systems.updated_at = datetime.now(timezone.utc)
        db.session.commit()
        log.info("plant crops: user=%s ready_at=%s", user.id, systems.crop_ready_at)

    return _action_response(user.id)


@bp.route("/action/harvest-crops")
def action_harvest_crops():
    """Add harvest yield to food stockpile when the crop timer has elapsed."""
    user = _identify_player()
    if user is None:
        return redirect("/new")

    now = datetime.now(timezone.utc)
    systems = db.session.get(BunkerSystems, user.id)
    if systems is None or systems.crop_ready_at is None or now < systems.crop_ready_at:
        return _action_response(user.id)

    latest_food = db.session.scalars(
        select(FoodReserve)
        .where(FoodReserve.user_id == user.id)
        .order_by(FoodReserve.timestamp.desc())
        .limit(1)
    ).first()
    if latest_food is None:
        return _action_response(user.id)

    latest_pop = db.session.scalars(
        select(BunkerPopulation)
        .where(BunkerPopulation.user_id == user.id)
        .order_by(BunkerPopulation.timestamp.desc())
        .limit(1)
    ).first()
    pop = latest_pop.count if latest_pop is not None else 0
    consumption_ps = pop * current_app.config["FOOD_PER_CAPITA_PER_SECOND"]
    production_ps = systems.food_workers * current_app.config["FOOD_PER_WORKER_PER_SECOND"]
    new_level = latest_food.level + current_app.config["FARM_HARVEST_YIELD"]

    systems.crop_ready_at = None
    systems.updated_at = now
    db.session.add(
        FoodReserve(
            user_id=user.id,
            level=new_level,
            consumption_per_second=consumption_ps,
            production_per_second=production_ps,
            timestamp=now,
        )
    )
    db.session.commit()
    log.info("harvest crops: user=%s food %.1f→%.1f", user.id, latest_food.level, new_level)

    return _action_response(user.id)


@bp.route("/action/show-movie")
def action_show_movie():
    """Reduce boredom; 5-minute cooldown; diminishing returns per use."""
    user = _identify_player()
    if user is None:
        return redirect("/new")

    now = datetime.now(timezone.utc)
    social = _get_or_create_social_state(user.id)
    cd = current_app.config["SOCIAL_MOVIE_COOLDOWN_SECONDS"]
    if _social_cooldown_remaining_seconds(social.last_show_movie_at, cd, now) > 0:
        return _action_response(user.id)

    latest = db.session.scalars(
        select(BunkerBoredom)
        .where(BunkerBoredom.user_id == user.id)
        .order_by(BunkerBoredom.timestamp.desc())
        .limit(1)
    ).first()
    if latest is None:
        return _action_response(user.id)

    base = current_app.config["SOCIAL_MOVIE_BOREDOM_RELIEF_BASE"]
    k = current_app.config["SOCIAL_MOVIE_DIMINISH_K"]
    uses = social.movie_action_count
    relief = base / (1.0 + k * uses)
    new_boredom = max(0.0, latest.boredom - relief)

    social.movie_action_count = uses + 1
    social.last_show_movie_at = now
    db.session.add(BunkerBoredom(user_id=user.id, boredom=new_boredom, timestamp=now))
    db.session.commit()
    log.info("show movie: user=%s boredom %.2f→%.2f", user.id, latest.boredom, new_boredom)

    return _action_response(user.id)


@bp.route("/action/give-speech")
def action_give_speech():
    """Raise loyalty, lower doubt; 5-minute cooldown; diminishing returns."""
    user = _identify_player()
    if user is None:
        return redirect("/new")

    now = datetime.now(timezone.utc)
    social = _get_or_create_social_state(user.id)
    cd = current_app.config["SOCIAL_SPEECH_COOLDOWN_SECONDS"]
    if _social_cooldown_remaining_seconds(social.last_give_speech_at, cd, now) > 0:
        return _action_response(user.id)

    latest_loyalty = db.session.scalars(
        select(BunkerLoyalty)
        .where(BunkerLoyalty.user_id == user.id)
        .order_by(BunkerLoyalty.timestamp.desc())
        .limit(1)
    ).first()
    latest_doubt = db.session.scalars(
        select(BunkerDoubt)
        .where(BunkerDoubt.user_id == user.id)
        .order_by(BunkerDoubt.timestamp.desc())
        .limit(1)
    ).first()
    if latest_loyalty is None or latest_doubt is None:
        return _action_response(user.id)

    uses = social.speech_action_count
    lk = current_app.config["SOCIAL_SPEECH_DIMINISH_K"]
    loyalty_gain = current_app.config["SOCIAL_SPEECH_LOYALTY_GAIN_BASE"] / (1.0 + lk * uses)
    doubt_relief = current_app.config["SOCIAL_SPEECH_DOUBT_RELIEF_BASE"] / (1.0 + lk * uses)

    new_loyalty = min(100.0, latest_loyalty.loyalty + loyalty_gain)
    new_doubt = max(0.0, latest_doubt.doubt - doubt_relief)

    social.speech_action_count = uses + 1
    social.last_give_speech_at = now
    db.session.add(BunkerLoyalty(user_id=user.id, loyalty=new_loyalty, timestamp=now))
    db.session.add(BunkerDoubt(user_id=user.id, doubt=new_doubt, timestamp=now))
    db.session.commit()
    log.info(
        "give speech: user=%s loyalty %.2f→%.2f doubt %.2f→%.2f",
        user.id,
        latest_loyalty.loyalty,
        new_loyalty,
        latest_doubt.doubt,
        new_doubt,
    )

    return _action_response(user.id)


@bp.route("/action/meet-council")
def action_meet_council():
    """Adjust hidden inner_circle_loyalty; flavor text only; 10-minute cooldown."""
    user = _identify_player()
    if user is None:
        return redirect("/new")

    now = datetime.now(timezone.utc)
    social = _get_or_create_social_state(user.id)
    cd = current_app.config["SOCIAL_COUNCIL_COOLDOWN_SECONDS"]
    if _social_cooldown_remaining_seconds(social.last_meet_council_at, cd, now) > 0:
        return _action_response(user.id)

    sign = 1 if random.random() < 0.5 else -1
    magnitude = random.randint(1, 5)
    delta = sign * magnitude
    new_inner = max(0, min(100, social.inner_circle_loyalty + delta))
    social.inner_circle_loyalty = new_inner
    social.last_meet_council_at = now

    if delta > 0:
        body = random.choice(POSITIVE_COUNCIL_MESSAGES)
    else:
        body = random.choice(NEGATIVE_COUNCIL_MESSAGES)

    db.session.add(SystemMessage(user_id=user.id, body=body, timestamp=now))
    db.session.commit()
    log.info("meet council: user=%s inner_circle delta=%+d -> %d", user.id, delta, new_inner)

    return _action_response(user.id)


@bp.route("/socials/action-status")
def socials_action_status():
    """JSON for Grafana: social action cooldown readiness."""
    user = _identify_player()
    now = datetime.now(timezone.utc)
    movie_cd = current_app.config["SOCIAL_MOVIE_COOLDOWN_SECONDS"]
    speech_cd = current_app.config["SOCIAL_SPEECH_COOLDOWN_SECONDS"]
    council_cd = current_app.config["SOCIAL_COUNCIL_COOLDOWN_SECONDS"]

    movie_rem = 0
    speech_rem = 0
    council_rem = 0
    if user is not None:
        social = db.session.get(BunkerSocialState, user.id)
        if social is not None:
            movie_rem = _social_cooldown_remaining_seconds(social.last_show_movie_at, movie_cd, now)
            speech_rem = _social_cooldown_remaining_seconds(social.last_give_speech_at, speech_cd, now)
            council_rem = _social_cooldown_remaining_seconds(social.last_meet_council_at, council_cd, now)

    resp = jsonify(
        {
            "can_show_movie": movie_rem == 0,
            "can_give_speech": speech_rem == 0,
            "can_meet_council": council_rem == 0,
            "movie_cooldown_seconds_remaining": movie_rem,
            "speech_cooldown_seconds_remaining": speech_rem,
            "council_cooldown_seconds_remaining": council_rem,
        }
    )
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@bp.route("/farming/crop-status")
def farming_crop_status():
    """JSON for Grafana: harvest eligibility and whether a new plant is allowed."""
    user = _identify_player()
    harvest_ready = False
    can_plant = False
    if user is not None:
        systems = db.session.get(BunkerSystems, user.id)
        if systems is not None:
            if systems.crop_ready_at is None:
                can_plant = True
            else:
                now = datetime.now(timezone.utc)
                harvest_ready = now >= systems.crop_ready_at
    resp = jsonify({"harvest_ready": harvest_ready, "can_plant": can_plant})
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@bp.route("/events/active-status")
def events_active_status():
    """JSON for Grafana: whether a random gameplay event is active."""
    user = _identify_player()
    payload = active_event_status_payload(user.id if user else None)
    resp = jsonify(payload)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@bp.route("/action/resolve-event")
def action_resolve_event():
    """Resolve the active event via player action (kind must match)."""
    user = _identify_player()
    if user is None:
        return redirect("/new")

    kind = (request.args.get("kind") or "").strip()
    if spec_for_kind(kind) is None:
        return _action_response(user.id)

    now = datetime.now(timezone.utc)
    if try_player_resolve(user.id, kind, tick_time=now):
        db.session.commit()
        log.info("resolve-event: user=%s kind=%s", user.id, kind)

    return _action_response(user.id)


@bp.route("/system-messages")
def system_messages():
    """Return the 5 most recent system messages for the current player.

    Returns JSON: list of {ts, body} objects ordered oldest-first (so the
    frontend can render them top-to-bottom with the newest at the bottom).
    The CORS header allows the Grafana origin to fetch this directly.
    """
    user = _identify_player()
    msgs = []
    if user is not None:
        rows = db.session.scalars(
            select(SystemMessage)
            .where(SystemMessage.user_id == user.id)
            .order_by(SystemMessage.timestamp.desc())
            .limit(5)
        ).all()
        msgs = [
            {"ts": m.timestamp.strftime("%H:%M"), "body": m.body}
            for m in reversed(rows)
        ]
    resp = jsonify(msgs)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@bp.route("/healthz")
def healthz():
    return {"status": "ok"}
