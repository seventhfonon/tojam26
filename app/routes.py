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
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID, uuid4

from flask import (
    Blueprint,
    abort,
    current_app,
    jsonify,
    make_response,
    redirect,
    request,
    send_from_directory,
)
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .extensions import db
from . import constants
from . import dashboard_gates
from . import inner_circle
from .focus_tree import focus_tree_status_payload, try_complete_focus
from .events import (
    EventDefinition,
    combined_rats_consumption_per_second_for_trappers,
    investigation_dispatch_status_payload,
    player_has_active_event_kind,
    rat_trapper_food_production_per_second,
    try_dispatch_investigation,
)
from .jobs import (
    current_bad_apple_frame_index,
    environment_pixel_goat_break_active_at,
    noisy_radiation_display,
    normalize_worker_assignments,
    record_environment_pixel_noise_sample,
    record_social_movie_pixel_sample,
    reset_social_movie_pixel_frame_for_screening,
)
from .models import (
    BunkerBoredom,
    BunkerCropPlot,
    BunkerDoubt,
    BunkerFarmingSystem,
    BunkerLightingSystem,
    BunkerLoyalty,
    BunkerPopulation,
    BunkerPowerCrankSystem,
    BunkerProfession,
    BunkerSocialState,
    BunkerTheatreSystem,
    EnergyReserve,
    FoodReserve,
    InnerCircleMember,
    PlayerMovieExhaustion,
    RadiationLevel,
    SystemMessage,
    User,
)
from .professions import (
    PROFESSION_FARMING,
    PROFESSION_IDLE,
    PROFESSION_INVESTIGATION,
    PROFESSION_POWER_CRANK,
    PROFESSION_RAT_TRAPPING,
    PROFESSION_THEATRE,
)
from .strings import (
    FIRESIDE_LABEL_BRIMSTONE_FEAR,
    FIRESIDE_LABEL_BRIMSTONE_FRANK,
    FIRESIDE_LABEL_BRIMSTONE_REASSURING,
    FIRESIDE_PANEL_TITLE_BRIMSTONE,
    FIRESIDE_PANEL_TITLE_FIRESIDE_CHATS,
    FIRESIDE_PANEL_TITLE_GIVE_SPEECH,
    FIRESIDE_STOCK_LABEL_FEARMONGERING,
    FIRESIDE_STOCK_LABEL_FRANK,
    FIRESIDE_STOCK_LABEL_REASSURING,
    NEGATIVE_COUNCIL_MESSAGES,
    POSITIVE_COUNCIL_MESSAGES,
)


log = logging.getLogger(__name__)
bp = Blueprint("main", __name__)

_ALLOWED_GAME_AUDIO_SUFFIXES = frozenset({".mp3", ".ogg", ".wav", ".m4a"})
_GAME_AUDIO_DIR = Path(__file__).resolve().parent / "assets" / "audio"


@bp.get("/assets/audio/<filename>")
def serve_game_audio(filename: str):
    """Serve loopable game audio for Grafana Text-panel `<audio>` tags."""
    if (
        not filename
        or filename.startswith(".")
        or "/" in filename
        or "\\" in filename
    ):
        abort(404)
    suf = Path(filename).suffix.lower()
    if suf not in _ALLOWED_GAME_AUDIO_SUFFIXES:
        abort(404)
    path = _GAME_AUDIO_DIR / filename
    if not path.is_file():
        abort(404)
    mt = "audio/mpeg" if suf == ".mp3" else None
    return send_from_directory(_GAME_AUDIO_DIR, filename, mimetype=mt)


def _seed_bunker_facilities(user: User) -> None:
    """Create lighting / crank / farm systems and profession lines for a new player."""
    now = datetime.now(timezone.utc)
    uid = user.id
    pop_cap = constants.INITIAL_POPULATION
    farm_n = min(constants.INITIAL_FARM_WORKERS, pop_cap)
    crank_line = BunkerProfession(
        user_id=uid,
        profession=PROFESSION_POWER_CRANK,
        count=0,
        updated_at=now,
    )
    farm_line = BunkerProfession(
        user_id=uid,
        profession=PROFESSION_FARMING,
        count=farm_n,
        updated_at=now,
    )
    rat_line = BunkerProfession(
        user_id=uid,
        profession=PROFESSION_RAT_TRAPPING,
        count=0,
        updated_at=now,
    )
    theatre_line = BunkerProfession(
        user_id=uid,
        profession=PROFESSION_THEATRE,
        count=0,
        updated_at=now,
    )
    idle_line = BunkerProfession(
        user_id=uid,
        profession=PROFESSION_IDLE,
        count=max(0, pop_cap - farm_n),
        updated_at=now,
    )
    investigation_line = BunkerProfession(
        user_id=uid,
        profession=PROFESSION_INVESTIGATION,
        count=0,
        updated_at=now,
    )
    db.session.add_all(
        [crank_line, farm_line, rat_line, theatre_line, idle_line, investigation_line]
    )
    db.session.flush()
    db.session.add(
        BunkerLightingSystem(user_id=uid, lights_on=True, updated_at=now),
    )
    db.session.add(
        BunkerPowerCrankSystem(
            user_id=uid,
            profession_line_id=crank_line.id,
            updated_at=now,
        ),
    )
    db.session.add(
        BunkerFarmingSystem(
            user_id=uid,
            profession_line_id=farm_line.id,
            rat_trapper_line_id=rat_line.id,
            updated_at=now,
        ),
    )
    db.session.add(
        BunkerTheatreSystem(
            user_id=uid,
            profession_line_id=theatre_line.id,
            phase=constants.THEATRE_PHASE_IDLE,
            play_index=0,
            phase_entered_at=now,
            updated_at=now,
        ),
    )
    for plot_i in range(constants.FARM_PLOT_COUNT):
        db.session.add(
            BunkerCropPlot(user_id=uid, plot_index=plot_i, crop_ready_at=None),
        )


def _get_power_crank_system(user_id: str) -> BunkerPowerCrankSystem | None:
    return db.session.scalars(
        select(BunkerPowerCrankSystem)
        .where(BunkerPowerCrankSystem.user_id == user_id)
        .options(selectinload(BunkerPowerCrankSystem.profession_line))
    ).first()


def _get_farming_system(user_id: str) -> BunkerFarmingSystem | None:
    return db.session.scalars(
        select(BunkerFarmingSystem)
        .where(BunkerFarmingSystem.user_id == user_id)
        .options(
            selectinload(BunkerFarmingSystem.profession_line),
            selectinload(BunkerFarmingSystem.rat_trapper_line),
        )
    ).first()


def _get_theatre_system(user_id: str) -> BunkerTheatreSystem | None:
    return db.session.scalars(
        select(BunkerTheatreSystem)
        .where(BunkerTheatreSystem.user_id == user_id)
        .options(selectinload(BunkerTheatreSystem.profession_line))
    ).first()


def _theatre_play_title(theatre_sys: BunkerTheatreSystem | None) -> str:
    if theatre_sys is None:
        return ""
    titles = constants.THEATRE_PLAY_TITLES
    if not titles:
        return ""
    return titles[int(theatre_sys.play_index) % len(titles)]


def _theatre_status_ui(phase: str) -> str:
    if phase == constants.THEATRE_PHASE_WRITING:
        return "planning"
    if phase == constants.THEATRE_PHASE_REHEARSING:
        return "rehearsing"
    if phase == constants.THEATRE_PHASE_READY:
        return "showing"
    return "idle"


def _ensure_crop_plots(user_id: str) -> None:
    """Ensure ``FARM_PLOT_COUNT`` rows exist for this player (migration / legacy gaps)."""
    if _get_farming_system(user_id) is None:
        return
    existing = db.session.scalars(
        select(BunkerCropPlot.plot_index).where(BunkerCropPlot.user_id == user_id)
    ).all()
    have = set(existing)
    added = False
    for i in range(constants.FARM_PLOT_COUNT):
        if i not in have:
            db.session.add(BunkerCropPlot(user_id=user_id, plot_index=i, crop_ready_at=None))
            added = True
    if added:
        db.session.commit()


def _crop_plot_phase(now: datetime, crop_ready_at: datetime | None) -> str:
    if crop_ready_at is None:
        return "empty"
    if now >= crop_ready_at:
        return "ready"
    return "growing"


def _plot_expected_harvest_yield(
    plot: BunkerCropPlot | None,
    now: datetime,
    current_farm_workers: int,
) -> float:
    """Harvest estimate: mean workers over full growth if current assignment holds until harvest."""
    w_curr = float(max(0, current_farm_workers))
    if plot is None or plot.crop_ready_at is None:
        return constants.harvest_yield_from_avg_farm_workers(w_curr)
    if plot.crop_planted_at is None:
        return constants.harvest_yield_from_avg_farm_workers(w_curr)
    duration_total = (plot.crop_ready_at - plot.crop_planted_at).total_seconds()
    if duration_total <= 0:
        return constants.harvest_yield_from_avg_farm_workers(w_curr)
    remaining = max(
        0.0,
        (plot.crop_ready_at - max(now, plot.crop_planted_at)).total_seconds(),
    )
    projected_avg = (
        plot.growth_worker_seconds + w_curr * remaining
    ) / duration_total
    return constants.harvest_yield_from_avg_farm_workers(projected_avg)


def _idle_profession_line(user_id: str) -> BunkerProfession | None:
    return db.session.scalars(
        select(BunkerProfession).where(
            BunkerProfession.user_id == user_id,
            BunkerProfession.profession == PROFESSION_IDLE,
        )
    ).first()


def _investigation_profession_line(user_id: str) -> BunkerProfession | None:
    return db.session.scalars(
        select(BunkerProfession).where(
            BunkerProfession.user_id == user_id,
            BunkerProfession.profession == PROFESSION_INVESTIGATION,
        )
    ).first()


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
            inner_circle_loyalty=constants.INITIAL_INNER_CIRCLE_LOYALTY,
            inner_circle_cash=float(constants.INITIAL_INNER_CIRCLE_CASH),
        )
        db.session.add(row)
        inner_circle.seed_members_for_user_if_needed(user_id)
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


def _player_actions_locked(user: User, now: datetime) -> bool:
    """True during wall-clock sermon or Fireside Chat windows."""
    su = user.sermon_busy_until
    if su is not None and now < su:
        return True
    fu = user.fireside_busy_until
    return fu is not None and now < fu


def _reject_if_sermon_locked(user: User | None):
    """Return iframe redirect response when sermon/fireside blocks actions; else None."""
    if user is None:
        return None
    now = datetime.now(timezone.utc)
    if _player_actions_locked(user, now):
        return _action_response(user.id)
    return None


def _fireside_ui_bundle(user_id: str) -> dict[str, object]:
    """Community Fireside panel: title, per-kind labels, cooldown, and enabled kinds."""
    from .focus_tree import completed_node_ids

    done = completed_node_ids(user_id)
    reassuring = constants.FIRESIDE_KIND_REASSURING
    frank = constants.FIRESIDE_KIND_FRANK
    fear = constants.FIRESIDE_KIND_FEARMONGERING
    stock_labels = {
        reassuring: FIRESIDE_STOCK_LABEL_REASSURING,
        frank: FIRESIDE_STOCK_LABEL_FRANK,
        fear: FIRESIDE_STOCK_LABEL_FEARMONGERING,
    }
    all_enabled = {reassuring: True, frank: True, fear: True}
    if "ft_fire_and_brimstone" in done:
        return {
            "panel_title": FIRESIDE_PANEL_TITLE_BRIMSTONE,
            "cooldown_seconds": int(constants.FIRESIDE_BRIMSTONE_COOLDOWN_SECONDS),
            "labels": {
                reassuring: FIRESIDE_LABEL_BRIMSTONE_REASSURING,
                frank: FIRESIDE_LABEL_BRIMSTONE_FRANK,
                fear: FIRESIDE_LABEL_BRIMSTONE_FEAR,
            },
            "kind_enabled": dict(all_enabled),
        }
    if "ft_fireside_chats" in done:
        return {
            "panel_title": FIRESIDE_PANEL_TITLE_FIRESIDE_CHATS,
            "cooldown_seconds": int(constants.FIRESIDE_CHAT_COOLDOWN_SECONDS),
            "labels": dict(stock_labels),
            "kind_enabled": dict(all_enabled),
        }
    return {
        "panel_title": FIRESIDE_PANEL_TITLE_GIVE_SPEECH,
        "cooldown_seconds": int(constants.FIRESIDE_GIVE_SPEECH_COOLDOWN_SECONDS),
        "labels": dict(stock_labels),
        "kind_enabled": {
            reassuring: True,
            frank: False,
            fear: False,
        },
    }


def _create_player() -> User:
    """Always mint a fresh UUID and seed all game-state tables."""
    user = User(id=str(uuid4()))
    db.session.add(user)
    true_rad = constants.INITIAL_RADIATION
    db.session.add(RadiationLevel(
        user_id=user.id,
        level=true_rad,
        level_display=noisy_radiation_display(true_rad),
    ))
    db.session.add(BunkerPopulation(
        user_id=user.id,
        count=constants.INITIAL_POPULATION,
        departed=0,
    ))
    db.session.add(BunkerLoyalty(
        user_id=user.id,
        loyalty=constants.INITIAL_LOYALTY,
    ))
    db.session.add(EnergyReserve(
        user_id=user.id,
        level=constants.INITIAL_ENERGY,
    ))
    db.session.add(FoodReserve(
        user_id=user.id,
        level=constants.INITIAL_FOOD,
        consumption_per_second=0.0,
        production_per_second=0.0,
    ))
    _seed_bunker_facilities(user)
    db.session.add(
        BunkerBoredom(
            user_id=user.id,
            boredom=constants.INITIAL_BOREDOM,
        )
    )
    db.session.add(
        BunkerDoubt(
            user_id=user.id,
            doubt=constants.INITIAL_DOUBT,
        )
    )
    db.session.add(
        BunkerSocialState(
            user_id=user.id,
            inner_circle_loyalty=constants.INITIAL_INNER_CIRCLE_LOYALTY,
            inner_circle_cash=float(constants.INITIAL_INNER_CIRCLE_CASH),
        )
    )
    inner_circle.seed_members_for_user_if_needed(user.id)
    now = datetime.now(timezone.utc)
    record_environment_pixel_noise_sample(
        user.id,
        now,
        current_bad_apple_frame_index(),
        use_reference_image=environment_pixel_goat_break_active_at(now),
    )
    record_social_movie_pixel_sample(user.id, now)
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

    blocked = _reject_if_sermon_locked(user)
    if blocked is not None:
        return blocked

    latest = db.session.scalars(
        select(EnergyReserve)
        .where(EnergyReserve.user_id == user.id)
        .order_by(EnergyReserve.timestamp.desc())
        .limit(1)
    ).first()

    if latest is not None:
        new_level = latest.level + constants.MANUAL_CRANK_ENERGY
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

    blocked = _reject_if_sermon_locked(user)
    if blocked is not None:
        return blocked

    lighting = db.session.get(BunkerLightingSystem, user.id)
    if lighting is not None:
        lighting.lights_on = not lighting.lights_on
        lighting.updated_at = datetime.now(timezone.utc)
        db.session.commit()
        log.info("toggle lights: user=%s lights=%s", user.id, lighting.lights_on)

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

    blocked = _reject_if_sermon_locked(user)
    if blocked is not None:
        return blocked

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

    crank_sys = _get_power_crank_system(user.id)
    farm_sys = _get_farming_system(user.id)
    theatre_sys = _get_theatre_system(user.id)
    idle_line = _idle_profession_line(user.id)
    inv_line = _investigation_profession_line(user.id)
    if (
        crank_sys is None
        or crank_sys.profession_line is None
        or farm_sys is None
        or farm_sys.profession_line is None
        or farm_sys.rat_trapper_line is None
        or theatre_sys is None
        or theatre_sys.profession_line is None
    ):
        return _action_response(user.id)

    now = datetime.now(timezone.utc)
    crank_line = crank_sys.profession_line
    farm_line = farm_sys.profession_line
    rat_line = farm_sys.rat_trapper_line
    theatre_line = theatre_sys.profession_line

    if delta > 0:
        crank_line.count += 1
    elif delta < 0:
        crank_line.count -= 1
    crank_line.count = max(0, min(crank_line.count, max_workers))
    normalize_worker_assignments(
        crank_line,
        farm_line,
        rat_line,
        theatre_line,
        idle_line,
        inv_line,
        max_workers,
        now,
    )
    crank_sys.updated_at = now
    farm_sys.updated_at = now
    theatre_sys.updated_at = now
    db.session.commit()
    log.info(
        "adjust crank workers: user=%s delta=%+d crank=%d food=%d",
        user.id,
        delta,
        crank_line.count,
        farm_line.count,
    )

    return _action_response(user.id)


@bp.route("/action/adjust-food")
def action_adjust_food():
    """Increment or decrement farm workers (shared population pool with crank)."""
    user = _identify_player()
    if user is None:
        return redirect("/new")

    blocked = _reject_if_sermon_locked(user)
    if blocked is not None:
        return blocked

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

    crank_sys = _get_power_crank_system(user.id)
    farm_sys = _get_farming_system(user.id)
    theatre_sys = _get_theatre_system(user.id)
    idle_line = _idle_profession_line(user.id)
    inv_line = _investigation_profession_line(user.id)
    if (
        crank_sys is None
        or crank_sys.profession_line is None
        or farm_sys is None
        or farm_sys.profession_line is None
        or farm_sys.rat_trapper_line is None
        or theatre_sys is None
        or theatre_sys.profession_line is None
    ):
        return _action_response(user.id)

    now = datetime.now(timezone.utc)
    crank_line = crank_sys.profession_line
    farm_line = farm_sys.profession_line
    rat_line = farm_sys.rat_trapper_line
    theatre_line = theatre_sys.profession_line

    if delta > 0:
        farm_line.count += 1
    elif delta < 0:
        farm_line.count -= 1
    farm_line.count = max(0, min(farm_line.count, max_workers))
    normalize_worker_assignments(
        crank_line,
        farm_line,
        rat_line,
        theatre_line,
        idle_line,
        inv_line,
        max_workers,
        now,
    )
    crank_sys.updated_at = now
    farm_sys.updated_at = now
    theatre_sys.updated_at = now
    db.session.commit()
    log.info(
        "adjust food workers: user=%s delta=%+d crank=%d food=%d",
        user.id,
        delta,
        crank_line.count,
        farm_line.count,
    )

    return _action_response(user.id)


@bp.route("/action/adjust-rat-trappers")
def action_adjust_rat_trappers():
    """Increment or decrement rat trappers (same resident pool as crank/farm)."""
    user = _identify_player()
    if user is None:
        return redirect("/new")

    blocked = _reject_if_sermon_locked(user)
    if blocked is not None:
        return blocked

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

    crank_sys = _get_power_crank_system(user.id)
    farm_sys = _get_farming_system(user.id)
    theatre_sys = _get_theatre_system(user.id)
    idle_line = _idle_profession_line(user.id)
    inv_line = _investigation_profession_line(user.id)
    if (
        crank_sys is None
        or crank_sys.profession_line is None
        or farm_sys is None
        or farm_sys.profession_line is None
        or farm_sys.rat_trapper_line is None
        or theatre_sys is None
        or theatre_sys.profession_line is None
    ):
        return _action_response(user.id)

    if delta > 0 and not bool(user.rat_trappers_unlocked):
        return _action_response(user.id)

    now = datetime.now(timezone.utc)
    crank_line = crank_sys.profession_line
    farm_line = farm_sys.profession_line
    rat_line = farm_sys.rat_trapper_line
    theatre_line = theatre_sys.profession_line

    if delta > 0:
        rat_line.count += 1
    elif delta < 0:
        rat_line.count -= 1
    rat_line.count = max(0, min(rat_line.count, max_workers))
    normalize_worker_assignments(
        crank_line,
        farm_line,
        rat_line,
        theatre_line,
        idle_line,
        inv_line,
        max_workers,
        now,
    )
    crank_sys.updated_at = now
    farm_sys.updated_at = now
    theatre_sys.updated_at = now
    db.session.commit()
    log.info(
        "adjust rat trappers: user=%s delta=%+d crank=%d food=%d trappers=%d",
        user.id,
        delta,
        crank_line.count,
        farm_line.count,
        rat_line.count,
    )

    return _action_response(user.id)


@bp.route("/action/adjust-theatre")
def action_adjust_theatre():
    """Increment or decrement actors (shared resident pool)."""
    user = _identify_player()
    if user is None:
        return redirect("/new")

    blocked = _reject_if_sermon_locked(user)
    if blocked is not None:
        return blocked

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

    crank_sys = _get_power_crank_system(user.id)
    farm_sys = _get_farming_system(user.id)
    theatre_sys = _get_theatre_system(user.id)
    idle_line = _idle_profession_line(user.id)
    inv_line = _investigation_profession_line(user.id)
    if (
        crank_sys is None
        or crank_sys.profession_line is None
        or farm_sys is None
        or farm_sys.profession_line is None
        or farm_sys.rat_trapper_line is None
        or theatre_sys is None
        or theatre_sys.profession_line is None
    ):
        return _action_response(user.id)

    now = datetime.now(timezone.utc)
    crank_line = crank_sys.profession_line
    farm_line = farm_sys.profession_line
    rat_line = farm_sys.rat_trapper_line
    theatre_line = theatre_sys.profession_line

    if delta > 0:
        theatre_line.count += 1
    elif delta < 0:
        theatre_line.count -= 1
    theatre_line.count = max(0, min(theatre_line.count, max_workers))
    normalize_worker_assignments(
        crank_line,
        farm_line,
        rat_line,
        theatre_line,
        idle_line,
        inv_line,
        max_workers,
        now,
    )
    crank_sys.updated_at = now
    farm_sys.updated_at = now
    theatre_sys.updated_at = now
    db.session.commit()
    log.info(
        "adjust theater actors: user=%s delta=%+d actors=%d",
        user.id,
        delta,
        theatre_line.count,
    )

    return _action_response(user.id)


@bp.route("/action/adjust-basket-weaving-hours")
def action_adjust_basket_weaving_hours():
    """Raise or lower mandatory basket-weaving hours per resident (0..max)."""
    user = _identify_player()
    if user is None:
        return redirect("/new")

    blocked = _reject_if_sermon_locked(user)
    if blocked is not None:
        return blocked

    social = db.session.get(BunkerSocialState, user.id)
    if social is None:
        return _action_response(user.id)

    try:
        delta = int(request.args.get("delta", 0))
    except (ValueError, TypeError):
        delta = 0

    if delta == 0:
        return _action_response(user.id)

    cur = constants.basket_weaving_hours_clamped(social.basket_weaving_hours)
    social.basket_weaving_hours = constants.basket_weaving_hours_clamped(cur + delta)
    db.session.commit()
    log.info(
        "adjust basket weaving hours: user=%s delta=%+d hours=%d",
        user.id,
        delta,
        social.basket_weaving_hours,
    )

    return _action_response(user.id)


@bp.route("/action/farming-plot")
def action_farming_plot():
    """Plant / harvest one bay; harvest food scales with mean farm workers during that growth."""
    user = _identify_player()
    if user is None:
        return redirect("/new")

    blocked = _reject_if_sermon_locked(user)
    if blocked is not None:
        return blocked

    raw_plot = request.args.get("plot")
    try:
        plot_index = int(raw_plot)
    except (TypeError, ValueError):
        return _action_response(user.id)

    if plot_index < 0 or plot_index >= constants.FARM_PLOT_COUNT:
        return _action_response(user.id)

    now = datetime.now(timezone.utc)
    farming = _get_farming_system(user.id)
    if farming is None:
        return _action_response(user.id)

    _ensure_crop_plots(user.id)
    plot = db.session.get(BunkerCropPlot, (user.id, plot_index))
    if plot is None:
        return _action_response(user.id)

    if plot.crop_ready_at is None:
        growth = constants.FARM_PLANT_GROWTH_SECONDS
        plot.crop_ready_at = now + timedelta(seconds=growth)
        plot.crop_planted_at = now
        plot.growth_worker_seconds = 0.0
        farming.updated_at = now
        db.session.commit()
        log.info(
            "farm plot plant: user=%s plot=%s ready_at=%s",
            user.id,
            plot_index,
            plot.crop_ready_at,
        )
        return _action_response(user.id)

    if now < plot.crop_ready_at:
        return _action_response(user.id)

    farm_line = farming.profession_line
    food_workers = float(farm_line.count if farm_line is not None else 0)

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
    consumption_ps = pop * constants.FOOD_PER_CAPITA_PER_SECOND
    growth_secs = float(constants.FARM_PLANT_GROWTH_SECONDS)
    if plot.crop_planted_at is not None and plot.crop_ready_at is not None:
        duration = max(1e-9, (plot.crop_ready_at - plot.crop_planted_at).total_seconds())
    else:
        duration = growth_secs
    avg_workers = plot.growth_worker_seconds / duration
    if plot.crop_planted_at is None:
        avg_workers = food_workers
    harvest_amt = constants.harvest_yield_from_avg_farm_workers(avg_workers)
    trap_n = farming.rat_trapper_line.count if farming.rat_trapper_line is not None else 0
    player_row = db.session.get(User, user.id)
    bg = float(player_row.rat_background_consumption_ps) if player_row is not None else 0.0
    introduced = bool(player_row.silo_rats_introduced) if player_row is not None else False
    swarm_active = introduced and player_has_active_event_kind(user.id, EventDefinition.RATS_SILO)
    combined_rat = combined_rats_consumption_per_second_for_trappers(pop, bg, swarm_active)
    trap_prod = rat_trapper_food_production_per_second(trap_n, combined_rat)
    production_ps = constants.FOOD_PER_WORKER_PER_SECOND * food_workers + trap_prod

    plot.crop_ready_at = None
    plot.crop_planted_at = None
    plot.growth_worker_seconds = 0.0
    farming.updated_at = now
    new_level = latest_food.level + harvest_amt
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
    log.info(
        "farm plot harvest: user=%s plot=%s food %.1f->%.1f (+%.1f avg_workers=%.2f)",
        user.id,
        plot_index,
        latest_food.level,
        new_level,
        harvest_amt,
        avg_workers,
    )

    return _action_response(user.id)


@bp.route("/action/show-movie")
def action_show_movie():
    """Start a catalog screening: pay energy now; boredom relief + exhaustion after duration."""
    user = _identify_player()
    if user is None:
        return redirect("/new")

    blocked = _reject_if_sermon_locked(user)
    if blocked is not None:
        return blocked

    movie_id = (request.args.get("movie_id") or "").strip()
    spec = constants.MOVIES_BY_ID.get(movie_id)
    if spec is None:
        return _action_response(user.id)

    now = datetime.now(timezone.utc)
    social = _get_or_create_social_state(user.id)
    if social.movie_screening_movie_id is not None:
        return _action_response(user.id)

    latest_energy = db.session.scalars(
        select(EnergyReserve)
        .where(EnergyReserve.user_id == user.id)
        .order_by(EnergyReserve.timestamp.desc())
        .limit(1)
    ).first()
    if latest_energy is None:
        return _action_response(user.id)

    if latest_energy.level + 1e-9 < spec.energy_cost:
        return _action_response(user.id)

    social.movie_screening_movie_id = movie_id
    social.movie_screening_started_at = now
    reset_social_movie_pixel_frame_for_screening(user.id)
    record_social_movie_pixel_sample(user.id, now)

    new_energy = max(0.0, latest_energy.level - spec.energy_cost)
    db.session.add(
        EnergyReserve(user_id=user.id, level=new_energy, timestamp=now),
    )

    db.session.commit()
    log.info(
        "show movie started: user=%s movie=%s energy %.2f→%.2f",
        user.id,
        movie_id,
        latest_energy.level,
        new_energy,
    )

    return _action_response(user.id)


@bp.route("/action/give-speech")
def action_give_speech():
    """Raise loyalty, lower doubt; 5-minute cooldown; diminishing returns."""
    user = _identify_player()
    if user is None:
        return redirect("/new")

    blocked = _reject_if_sermon_locked(user)
    if blocked is not None:
        return blocked

    now = datetime.now(timezone.utc)
    social = _get_or_create_social_state(user.id)
    cd = constants.SOCIAL_SPEECH_COOLDOWN_SECONDS
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
    lk = constants.SOCIAL_SPEECH_DIMINISH_K
    loyalty_gain = constants.SOCIAL_SPEECH_LOYALTY_GAIN_BASE / (1.0 + lk * uses)
    doubt_relief = constants.SOCIAL_SPEECH_DOUBT_RELIEF_BASE / (1.0 + lk * uses)

    new_loyalty = min(100.0, latest_loyalty.loyalty + loyalty_gain)
    new_doubt = max(0.0, latest_doubt.doubt - doubt_relief)

    social.speech_action_count = uses + 1
    social.last_give_speech_at = now
    db.session.add(BunkerLoyalty(user_id=user.id, loyalty=new_loyalty, timestamp=now))
    db.session.add(BunkerDoubt(user_id=user.id, doubt=new_doubt, timestamp=now))
    soc_gate = db.session.get(BunkerSocialState, user.id)
    if soc_gate is not None and soc_gate.awaiting_post_geiger_exodus_speech:
        soc_gate.fireside_chats_focus_gate_done = True
        soc_gate.awaiting_post_geiger_exodus_speech = False
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

    blocked = _reject_if_sermon_locked(user)
    if blocked is not None:
        return blocked

    now = datetime.now(timezone.utc)
    social = _get_or_create_social_state(user.id)
    cd = constants.SOCIAL_COUNCIL_COOLDOWN_SECONDS
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

    db.session.add(
        SystemMessage(
            user_id=user.id,
            body=body,
            timestamp=now,
            channel=constants.MESSAGE_CHANNEL_BULLETIN,
        )
    )
    db.session.commit()
    log.info("meet council: user=%s inner_circle delta=%+d -> %d", user.id, delta, new_inner)

    return _action_response(user.id)


def _inner_circle_slot_query() -> int | None:
    try:
        return int(request.args.get("slot", ""))
    except (TypeError, ValueError):
        return None


@bp.route("/inner-circle/status")
def inner_circle_http_status():
    user = _identify_player()
    if user is None:
        resp = jsonify({"error": "no_player"})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp, 400
    now = datetime.now(timezone.utc)
    _get_or_create_social_state(user.id)
    resp = jsonify(inner_circle.inner_circle_status_payload(user.id, now))
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@bp.route("/inner-circle/psyche-swatch")
def inner_circle_psyche_swatch():
    """PNG-less RGB preview for Grafana Text panels (``<img>`` avoids fetch/CORS)."""
    user = _identify_player()
    try:
        slot = int(request.args.get("slot", ""))
    except (TypeError, ValueError):
        slot = -1

    fill = "#30363d"
    if user is not None and 0 <= slot < constants.INNER_CIRCLE_MEMBER_COUNT:
        inner_circle.seed_members_for_user_if_needed(user.id)
        row = db.session.get(InnerCircleMember, (user.id, slot))
        if row is not None:
            r_ch = max(0, min(255, int(round(float(row.frustration) * 2.55))))
            g_ch = max(0, min(255, int(round(float(row.loyalty) * 2.55))))
            b_ch = max(0, min(255, int(round(float(row.disposition) * 2.55))))
            fill = f"rgb({r_ch},{g_ch},{b_ch})"

    svg = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<svg xmlns=\"http://www.w3.org/2000/svg\" "
        "width=\"320\" height=\"160\" viewBox=\"0 0 320 160\">"
        f"<rect width=\"320\" height=\"160\" fill=\"{fill}\"/>"
        "</svg>"
    )
    resp = make_response(svg)
    resp.mimetype = "image/svg+xml"
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@bp.route("/action/inner-circle/grant-luxuries")
def action_inner_circle_grant_luxuries():
    user = _identify_player()
    if user is None:
        return redirect("/new")
    blocked = _reject_if_sermon_locked(user)
    if blocked is not None:
        return blocked
    slot = _inner_circle_slot_query()
    if slot is None:
        return _action_response(user.id)
    now = datetime.now(timezone.utc)
    err = inner_circle.try_grant_luxuries(user.id, slot, now)
    if err is None:
        db.session.commit()
    else:
        db.session.rollback()
    return _action_response(user.id)


@bp.route("/action/inner-circle/stage-incident")
def action_inner_circle_stage_incident():
    user = _identify_player()
    if user is None:
        return redirect("/new")
    blocked = _reject_if_sermon_locked(user)
    if blocked is not None:
        return blocked
    slot = _inner_circle_slot_query()
    if slot is None:
        return _action_response(user.id)
    now = datetime.now(timezone.utc)
    err = inner_circle.try_start_stage_incident(user.id, slot, now)
    if err is None:
        db.session.commit()
    else:
        db.session.rollback()
    return _action_response(user.id)


@bp.route("/action/inner-circle/buy-groceries")
def action_inner_circle_buy_groceries():
    user = _identify_player()
    if user is None:
        return redirect("/new")
    blocked = _reject_if_sermon_locked(user)
    if blocked is not None:
        return blocked
    slot = _inner_circle_slot_query()
    if slot is None:
        return _action_response(user.id)
    now = datetime.now(timezone.utc)
    err = inner_circle.try_start_buy_groceries(user.id, slot, now)
    if err is None:
        db.session.commit()
    else:
        db.session.rollback()
    return _action_response(user.id)


@bp.route("/action/inner-circle/temp-job")
def action_inner_circle_temp_job():
    user = _identify_player()
    if user is None:
        return redirect("/new")
    blocked = _reject_if_sermon_locked(user)
    if blocked is not None:
        return blocked
    slot = _inner_circle_slot_query()
    if slot is None:
        return _action_response(user.id)
    now = datetime.now(timezone.utc)
    err = inner_circle.try_start_temp_job(user.id, slot, now)
    if err is None:
        db.session.commit()
    else:
        db.session.rollback()
    return _action_response(user.id)


@bp.route("/action/start-fireside-chat")
def action_start_fireside_chat():
    """Begin a timed Fireside Chat (locks other actions until it completes)."""
    user = _identify_player()
    if user is None:
        return redirect("/new")

    kind_raw = (request.args.get("kind") or "").strip().lower()
    if kind_raw not in constants.FIRESIDE_KINDS:
        log.warning("start fireside: invalid kind=%r user=%s", kind_raw, user.id)
        return _action_response(user.id)

    now = datetime.now(timezone.utc)
    bundle = _fireside_ui_bundle(user.id)
    kinds_ok = bundle["kind_enabled"]
    if not isinstance(kinds_ok, dict) or not bool(kinds_ok.get(kind_raw, False)):
        log.warning("start fireside: kind not enabled=%r user=%s", kind_raw, user.id)
        return _action_response(user.id)

    if user.sermon_busy_until is not None and now < user.sermon_busy_until:
        return _action_response(user.id)
    if user.fireside_busy_until is not None and now < user.fireside_busy_until:
        return _action_response(user.id)

    social = _get_or_create_social_state(user.id)
    cd_cap = int(bundle["cooldown_seconds"])
    cd_rem = _social_cooldown_remaining_seconds(
        social.last_fireside_chat_at,
        cd_cap,
        now,
    )
    if cd_rem > 0:
        return _action_response(user.id)

    social.last_fireside_chat_at = now
    user.fireside_busy_until = now + timedelta(
        seconds=constants.FIRESIDE_CHAT_DURATION_SECONDS
    )
    user.fireside_pending_kind = kind_raw
    user.fireside_effect_fraction_accrued = 0.0
    db.session.commit()
    log.info(
        "start fireside: user=%s kind=%s busy_until=%s",
        user.id,
        kind_raw,
        user.fireside_busy_until,
    )

    return _action_response(user.id)


@bp.route("/action/start-sermon")
def action_start_sermon():
    """Block other actions until sermon completes; loyalty reward applied on next tick after."""
    user = _identify_player()
    if user is None:
        return redirect("/new")

    now = datetime.now(timezone.utc)
    if user.fireside_busy_until is not None and now < user.fireside_busy_until:
        return _action_response(user.id)
    if user.sermon_busy_until is not None and now < user.sermon_busy_until:
        return _action_response(user.id)

    user.sermon_busy_until = now + timedelta(seconds=constants.SERMON_DURATION_SECONDS)
    user.sermon_reward_pending = True
    db.session.commit()
    log.info("start sermon: user=%s busy_until=%s", user.id, user.sermon_busy_until)

    return _action_response(user.id)


def _social_movie_status_detail(user: User, now: datetime) -> dict[str, object]:
    """Per-title screening stats for Grafana movie rows (see ``/socials/movie-status``)."""
    movie_cd = constants.SOCIAL_MOVIE_COOLDOWN_SECONDS
    actions_locked = _player_actions_locked(user, now)
    social = db.session.get(BunkerSocialState, user.id)
    movie_rem = 0
    screening_mid: str | None = None
    screening_started: datetime | None = None
    if social is not None:
        movie_rem = _social_cooldown_remaining_seconds(
            social.last_show_movie_at, movie_cd, now
        )
        screening_mid = social.movie_screening_movie_id
        screening_started = social.movie_screening_started_at

    # Screening runtime is the throttle; no extra cooldown after a screening ends.
    can_show_movie = not actions_locked and screening_mid is None

    latest_energy = db.session.scalars(
        select(EnergyReserve)
        .where(EnergyReserve.user_id == user.id)
        .order_by(EnergyReserve.timestamp.desc())
        .limit(1)
    ).first()
    energy_level = float(latest_energy.level) if latest_energy is not None else 0.0

    exh_rows = db.session.scalars(
        select(PlayerMovieExhaustion).where(PlayerMovieExhaustion.user_id == user.id)
    ).all()
    exhaust_by_id = {r.movie_id: float(r.exhaustion) for r in exh_rows}

    movies_out: list[dict[str, object]] = []
    for spec in constants.MOVIES:
        exh = exhaust_by_id.get(spec.id, 0.0)
        cost = float(spec.energy_cost)
        can_play = can_show_movie and energy_level + 1e-9 >= cost
        movies_out.append(
            {
                "id": spec.id,
                "title": spec.title,
                "energy_cost": cost,
                "exhaustion": exh,
                "can_play": can_play,
            }
        )

    return {
        "actions_locked": actions_locked,
        "movie_cooldown_seconds_remaining": movie_rem,
        "can_show_movie": can_show_movie,
        "energy_level": energy_level,
        "movie_exhaustion_decay_per_second": float(constants.MOVIE_EXHAUSTION_DECAY_PER_SECOND),
        "movie_screening_duration_seconds": float(constants.MOVIE_SCREENING_DURATION_SECONDS),
        "screening_movie_id": screening_mid,
        "screening_started_at": screening_started.isoformat()
        if screening_started is not None
        else None,
        "movies": movies_out,
    }


@bp.route("/socials/action-status")
def socials_action_status():
    """JSON for Grafana: social action cooldown readiness."""
    user = _identify_player()
    now = datetime.now(timezone.utc)
    speech_cd = constants.SOCIAL_SPEECH_COOLDOWN_SECONDS
    council_cd = constants.SOCIAL_COUNCIL_COOLDOWN_SECONDS

    movie_rem = 0
    speech_rem = 0
    council_rem = 0
    actions_locked = False
    sermon_rem = 0
    fireside_rem = 0
    fireside_cd_rem = 0
    movies_payload: list[dict[str, object]] = []
    theatre_phase = constants.THEATRE_PHASE_IDLE
    theatre_actors = 0
    theatre_next_perf_rem = 0
    theatre_play_title = ""
    theatre_status_ui = "idle"

    basket_mandatory_hours = 0
    basket_population = 0
    basket_loyalty_ps = 0.0
    basket_cash_ps = 0.0
    basket_can_adjust = False

    can_show_movie_flag = False
    fire_ui: dict[str, object] = {}

    if user is not None:
        fire_ui = _fireside_ui_bundle(user.id)
        md = _social_movie_status_detail(user, now)
        movie_rem = int(md["movie_cooldown_seconds_remaining"])
        actions_locked = bool(md["actions_locked"])
        can_show_movie_flag = bool(md["can_show_movie"])
        movies_payload = [
            {
                "id": m["id"],
                "title": m["title"],
                "energy_cost": m["energy_cost"],
                "exhaustion": m["exhaustion"],
            }
            for m in md["movies"]
        ]

        if user.sermon_busy_until is not None and now < user.sermon_busy_until:
            sermon_rem = max(
                0,
                math.ceil((user.sermon_busy_until - now).total_seconds()),
            )

        if user.fireside_busy_until is not None and now < user.fireside_busy_until:
            fireside_rem = max(
                0,
                math.ceil((user.fireside_busy_until - now).total_seconds()),
            )

        social = db.session.get(BunkerSocialState, user.id)
        if social is not None:
            speech_rem = _social_cooldown_remaining_seconds(
                social.last_give_speech_at, speech_cd, now
            )
            council_rem = _social_cooldown_remaining_seconds(
                social.last_meet_council_at, council_cd, now
            )
            cd_dyn = int(fire_ui["cooldown_seconds"])
            fireside_cd_rem = _social_cooldown_remaining_seconds(
                social.last_fireside_chat_at,
                cd_dyn,
                now,
            )

        theatre_sys = _get_theatre_system(user.id)
        if theatre_sys is not None:
            theatre_phase = theatre_sys.phase
            theatre_play_title = _theatre_play_title(theatre_sys)
            theatre_status_ui = _theatre_status_ui(theatre_phase)
            if theatre_sys.profession_line is not None:
                theatre_actors = theatre_sys.profession_line.count
            if theatre_sys.next_performance_at is not None:
                theatre_next_perf_rem = max(
                    0,
                    math.ceil((theatre_sys.next_performance_at - now).total_seconds()),
                )

    sermon_active = (
        user is not None
        and user.sermon_busy_until is not None
        and now < user.sermon_busy_until
    )
    fireside_active = (
        user is not None
        and user.fireside_busy_until is not None
        and now < user.fireside_busy_until
    )
    can_start_fireside_chat = (
        user is not None
        and not sermon_active
        and not fireside_active
        and fireside_cd_rem == 0
    )

    if user is not None:
        latest_pop_basket = db.session.scalars(
            select(BunkerPopulation)
            .where(BunkerPopulation.user_id == user.id)
            .order_by(BunkerPopulation.timestamp.desc())
            .limit(1)
        ).first()
        basket_population = latest_pop_basket.count if latest_pop_basket is not None else 0
        soc_bw = db.session.get(BunkerSocialState, user.id)
        if soc_bw is not None:
            basket_mandatory_hours = constants.basket_weaving_hours_clamped(
                soc_bw.basket_weaving_hours
            )
        basket_loyalty_ps = constants.basket_weaving_loyalty_per_second(
            basket_mandatory_hours
        )
        basket_cash_ps = constants.basket_weaving_cash_per_second(
            basket_mandatory_hours, basket_population
        )
        basket_can_adjust = not actions_locked and not sermon_active

    resp = jsonify(
        {
            "actions_locked": actions_locked,
            "sermon_seconds_remaining": sermon_rem,
            "fireside_seconds_remaining": fireside_rem,
            "fireside_cooldown_seconds_remaining": fireside_cd_rem,
            "fireside_chat_duration_seconds": int(constants.FIRESIDE_CHAT_DURATION_SECONDS),
            "fireside_chat_cooldown_seconds": int(fire_ui["cooldown_seconds"])
            if fire_ui
            else int(constants.FIRESIDE_GIVE_SPEECH_COOLDOWN_SECONDS),
            "fireside_ui": fire_ui,
            "can_start_fireside_chat": can_start_fireside_chat,
            "can_start_sermon": user is not None and not actions_locked,
            "can_show_movie": can_show_movie_flag,
            "can_give_speech": user is not None and speech_rem == 0 and not actions_locked,
            "can_meet_council": user is not None and council_rem == 0 and not actions_locked,
            "movie_cooldown_seconds_remaining": movie_rem,
            "speech_cooldown_seconds_remaining": speech_rem,
            "council_cooldown_seconds_remaining": council_rem,
            "movies": movies_payload,
            "theatre_phase": theatre_phase,
            "theatre_actor_count": theatre_actors,
            "theatre_next_performance_seconds_remaining": theatre_next_perf_rem,
            "theatre_play_title": theatre_play_title,
            "theatre_status_ui": theatre_status_ui,
            "theatre_loyalty_per_second": (
                float(constants.THEATRE_LOYALTY_PER_SECOND)
                if theatre_phase
                in (
                    constants.THEATRE_PHASE_WRITING,
                    constants.THEATRE_PHASE_REHEARSING,
                    constants.THEATRE_PHASE_READY,
                )
                else 0.0
            ),
            "theatre_boredom_relief_per_second": (
                float(constants.THEATRE_BOREDOM_RELIEF_PER_SECOND)
                if theatre_phase == constants.THEATRE_PHASE_READY
                else 0.0
            ),
            "theatre_energy_draw_per_second": float(
                constants.THEATRE_POWER_DRAW_PER_ACTOR
            )
            * float(theatre_actors),
            "theatre_boredom_relief_per_play": float(
                constants.THEATRE_BOREDOM_RELIEF_PER_PLAY
            ),
            "basket_weaving_mandatory_hours": basket_mandatory_hours,
            "basket_weaving_hours_max": int(constants.BASKET_WEAVING_HOURS_MAX),
            "basket_weaving_population": basket_population,
            "basket_weaving_loyalty_per_second": float(basket_loyalty_ps),
            "basket_weaving_cash_per_second": float(basket_cash_ps),
            "can_adjust_basket_weaving_hours": basket_can_adjust,
        }
    )
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@bp.route("/socials/movie-status")
def socials_movie_status():
    """JSON for Grafana: per-movie interaction rows (energy, screening state)."""
    user = _identify_player()
    now = datetime.now(timezone.utc)
    if user is None:
        resp = jsonify(
            {
                "actions_locked": True,
                "movie_cooldown_seconds_remaining": 0,
                "can_show_movie": False,
                "energy_level": 0.0,
                "movie_exhaustion_decay_per_second": float(
                    constants.MOVIE_EXHAUSTION_DECAY_PER_SECOND
                ),
                "movie_screening_duration_seconds": float(
                    constants.MOVIE_SCREENING_DURATION_SECONDS
                ),
                "screening_movie_id": None,
                "screening_started_at": None,
                "movies": [],
            }
        )
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp

    resp = jsonify(_social_movie_status_detail(user, now))
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@bp.route("/farming/crop-status")
def farming_crop_status():
    """JSON for Grafana: each hydro plot's phase (empty / growing / ready)."""
    growth = constants.FARM_PLANT_GROWTH_SECONDS
    harvest_yield = constants.FARM_HARVEST_YIELD
    harvest_ref_workers = constants.FARM_HARVEST_YIELD_REF_AVG_WORKERS
    plot_count = constants.FARM_PLOT_COUNT
    plot_columns = constants.FARM_PLOT_GRID_COLUMNS

    def payload(plots: list[dict]) -> dict:
        return {
            "plots": plots,
            "growth_seconds": growth,
            "harvest_yield": harvest_yield,
            "harvest_yield_ref_workers": harvest_ref_workers,
            "plot_columns": plot_columns,
        }

    empty_plots = [
        {
            "plot": i,
            "phase": "empty",
            "ready_at": None,
            "expected_yield": round(
                constants.harvest_yield_from_avg_farm_workers(0.0), 2
            ),
        }
        for i in range(plot_count)
    ]

    user = _identify_player()
    if user is None:
        resp = jsonify(payload(empty_plots))
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp

    if _get_farming_system(user.id) is None:
        resp = jsonify(payload(empty_plots))
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp

    _ensure_crop_plots(user.id)
    now = datetime.now(timezone.utc)
    rows = db.session.scalars(
        select(BunkerCropPlot)
        .where(BunkerCropPlot.user_id == user.id)
        .order_by(BunkerCropPlot.plot_index)
    ).all()
    by_idx = {r.plot_index: r for r in rows}

    farming = _get_farming_system(user.id)
    farm_line = farming.profession_line if farming is not None else None
    farm_workers = farm_line.count if farm_line is not None else 0

    plots_out: list[dict] = []
    for i in range(plot_count):
        row = by_idx.get(i)
        ready_at = row.crop_ready_at if row else None
        phase = _crop_plot_phase(now, ready_at)
        exp_yield = _plot_expected_harvest_yield(row, now, farm_workers)
        plots_out.append(
            {
                "plot": i,
                "phase": phase,
                "ready_at": ready_at.isoformat().replace("+00:00", "Z")
                if ready_at
                else None,
                "expected_yield": round(exp_yield, 2),
            }
        )

    resp = jsonify(payload(plots_out))
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@bp.route("/systems/worker-assignment-status")
def worker_assignment_status():
    """JSON for Grafana: shared worker pool (crank + farm vs population)."""
    user = _identify_player()
    if user is None:
        resp = jsonify(
            {
                "population": 0,
                "crank_workers": 0,
                "farm_workers": 0,
                "rat_trapper_workers": 0,
                "theatre_workers": 0,
                "investigation_workers": 0,
                "idle_workers": 0,
                "can_hire": False,
                "can_hire_rat_trapper": False,
                "can_hire_theatre": False,
                "can_fire_crank": False,
                "can_fire_farm": False,
                "can_fire_rat_trapper": False,
                "can_fire_theatre": False,
            }
        )
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp

    latest_pop = db.session.scalars(
        select(BunkerPopulation)
        .where(BunkerPopulation.user_id == user.id)
        .order_by(BunkerPopulation.timestamp.desc())
        .limit(1)
    ).first()
    population = latest_pop.count if latest_pop is not None else 0

    crank_sys = _get_power_crank_system(user.id)
    farm_sys = _get_farming_system(user.id)
    theatre_sys = _get_theatre_system(user.id)
    crank_line = crank_sys.profession_line if crank_sys is not None else None
    farm_line = farm_sys.profession_line if farm_sys is not None else None
    idle_line = _idle_profession_line(user.id)

    crank_workers = crank_line.count if crank_line is not None else 0
    farm_workers = farm_line.count if farm_line is not None else 0
    rat_line = farm_sys.rat_trapper_line if farm_sys is not None else None
    rat_trapper_workers = rat_line.count if rat_line is not None else 0
    theatre_line = theatre_sys.profession_line if theatre_sys is not None else None
    theatre_workers = theatre_line.count if theatre_line is not None else 0
    inv_line = _investigation_profession_line(user.id)
    investigation_workers = inv_line.count if inv_line is not None else 0
    idle_workers = (
        idle_line.count
        if idle_line is not None
        else max(
            0,
            population
            - crank_workers
            - farm_workers
            - rat_trapper_workers
            - theatre_workers
            - investigation_workers,
        )
    )

    facilities_ready = (
        crank_sys is not None
        and farm_sys is not None
        and theatre_sys is not None
        and crank_line is not None
        and farm_line is not None
        and rat_line is not None
        and theatre_line is not None
    )
    assigned = (
        crank_workers
        + farm_workers
        + rat_trapper_workers
        + theatre_workers
        + investigation_workers
    )
    can_hire = facilities_ready and population > 0 and assigned < population
    trap_unlocked = bool(user.rat_trappers_unlocked)
    can_hire_rat_trapper = can_hire and trap_unlocked

    resp = jsonify(
        {
            "population": population,
            "crank_workers": crank_workers,
            "farm_workers": farm_workers,
            "rat_trapper_workers": rat_trapper_workers,
            "theatre_workers": theatre_workers,
            "investigation_workers": investigation_workers,
            "idle_workers": idle_workers,
            "can_hire": can_hire,
            "can_hire_rat_trapper": can_hire_rat_trapper,
            "can_hire_theatre": can_hire and idle_workers > 0,
            "can_fire_crank": crank_workers > 0,
            "can_fire_farm": farm_workers > 0,
            "can_fire_rat_trapper": rat_trapper_workers > 0,
            "can_fire_theatre": theatre_workers > 0,
        }
    )
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@bp.route("/systems/investigation-dispatch-status")
def systems_investigation_dispatch_status():
    """Minimal JSON for Grafana: whether a routine sweep can deploy (no event spoilers)."""
    user = _identify_player()
    system_q = (request.args.get("system") or "").strip()
    payload = investigation_dispatch_status_payload(user.id if user else None, system_q)
    resp = jsonify(payload)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@bp.route("/systems/ui-gates")
def systems_ui_gates():
    """JSON for Grafana: panel IDs to hide based on focus completions / active events."""
    user = _identify_player()
    uid_q = (request.args.get("user_id") or "").strip()
    user_id = user.id if user else (uid_q if _is_valid_uuid(uid_q) else None)
    dash_uid = (request.args.get("dashboard_uid") or "").strip()
    payload = dashboard_gates.ui_gates_payload(user_id, dash_uid)
    resp = jsonify(payload)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@bp.route("/systems/focus-tree-status")
def systems_focus_tree_status():
    """JSON for Focus Tree panels: completion flags and whether each button may fire."""
    user = _identify_player()
    payload = focus_tree_status_payload(user.id if user else None)
    resp = jsonify(payload)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@bp.route("/action/focus-tree-complete")
def action_focus_tree_complete():
    """Mark one Focus Tree node complete when prerequisites are satisfied."""
    user = _identify_player()
    if user is None:
        return redirect("/new")

    blocked = _reject_if_sermon_locked(user)
    if blocked is not None:
        return blocked

    node_id = (request.args.get("node_id") or "").strip()
    now = datetime.now(timezone.utc)
    if try_complete_focus(user.id, node_id, now):
        db.session.commit()
        log.info("focus-tree-complete: user=%s node=%s", user.id, node_id)

    return _action_response(user.id)


@bp.route("/action/dispatch-investigation")
def action_dispatch_investigation():
    """Deploy routine subsystem sweep when enough idle residents are available."""
    user = _identify_player()
    if user is None:
        return redirect("/new")

    blocked = _reject_if_sermon_locked(user)
    if blocked is not None:
        return blocked

    system_q = (request.args.get("system") or "").strip()
    now = datetime.now(timezone.utc)
    if try_dispatch_investigation(user.id, system_q, now):
        db.session.commit()
        log.info("dispatch-investigation: user=%s system=%s", user.id, system_q)

    return _action_response(user.id)


def _system_messages_payload(rows: list[SystemMessage]) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for m in reversed(rows):
        urgency, display_body = constants.parse_system_message_body(m.body)
        out.append(
            {
                "ts": m.timestamp.strftime("%H:%M"),
                "body": display_body,
                "urgency": urgency,
                "emoji": constants.system_message_urgency_emoji(urgency),
            }
        )
    return out


@bp.route("/system-messages")
def system_messages():
    """Return the 5 most recent Silo Bulletin lines for the current player.

    Rows use ``MESSAGE_CHANNEL_BULLETIN`` on ``system_messages``. JSON list of
    ``{ts, body, urgency, emoji}`` ordered oldest-first for terminal rendering.
    """
    user = _identify_player()
    msgs: list[dict[str, object]] = []
    if user is not None:
        rows = db.session.scalars(
            select(SystemMessage)
            .where(
                SystemMessage.user_id == user.id,
                SystemMessage.channel == constants.MESSAGE_CHANNEL_BULLETIN,
            )
            .order_by(SystemMessage.timestamp.desc())
            .limit(5)
        ).all()
        msgs = _system_messages_payload(rows)
    resp = jsonify(msgs)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@bp.route("/inner-circle/group-chat-messages")
def inner_circle_group_chat_messages():
    """Recent Inner Circle Group Chat lines (``MESSAGE_CHANNEL_GROUP_CHAT``)."""
    user = _identify_player()
    msgs: list[dict[str, object]] = []
    if user is not None:
        rows = db.session.scalars(
            select(SystemMessage)
            .where(
                SystemMessage.user_id == user.id,
                SystemMessage.channel == constants.MESSAGE_CHANNEL_GROUP_CHAT,
            )
            .order_by(SystemMessage.timestamp.desc())
            .limit(5)
        ).all()
        msgs = _system_messages_payload(rows)
    resp = jsonify(msgs)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@bp.route("/healthz")
def healthz():
    return {"status": "ok"}
