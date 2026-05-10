"""Player-facing copy — default locale.

Edit strings here to change in-game text from Python/API responses and system
messages. Grafana dashboard panel markup lives separately under
``grafana/dashboards/``.
"""

from __future__ import annotations

# --- Subsystem labels (investigation templates, UI) ---
GAME_SYSTEM_LABELS: dict[str, str] = {
    "environment": "Environment sensors",
    "power": "Power systems",
    "farming": "Farming & silos",
    "social": "Community programs",
}

INVESTIGATION_FALLBACK_SUBSYSTEM_LABEL = "the bunker"

# --- Professions (stored in DB; keep values aligned with migrations) ---
PROFESSION_IDLE = "Idle"
PROFESSION_POWER_CRANK = "Power crank"
PROFESSION_FARMING = "Farming"
PROFESSION_RAT_TRAPPING = "Rat trapping"
PROFESSION_THEATRE = "Theatre"
PROFESSION_INVESTIGATION = "Investigation"

# --- Inner Circle ---
INNER_CIRCLE_MEMBER_NAMES: tuple[str, ...] = (
    "Marnie Coldwell",
    "Jace Orbin",
    "Vesper Kline",
    "Tamsin Greer",
    "Nadia Firth",
)

VENTURE_OUT_FAREWELL_MESSAGE = (
    "{name}: I've said three versions of this message aloud in an empty stairwell and deleted them all. "
    "I'm going up on my own terms — not because I believe the sermons about ash gardens, but because "
    "I can't keep translating radiation curves into bedtime stories for adults who deserve harder truths "
    "than I know how to speak.\n\n"
    "The hatch crew knows I've been practicing the route — tell them not to pretend surprise. "
    "I'll carry the handheld dosimeter we calibrated together last quarter; if it screams, I'll treat "
    "that as data, not drama. If it stays quiet longer than any of us dare hope, I'll send one terse ping "
    "on the old scout frequency — not poetry, just coordinates and weather smell — then I'll go dark so "
    "you don't mistake honesty for recruitment.\n\n"
    "Don't deputize my empty chair into mythology. Leave my ration ledger open: I wasn't stealing; I was "
    "padding slack for nights nobody thanked me for covering. If loyalty still counts for anything, waste "
    "that surplus on someone who's afraid to ask.\n\n"
    "I'm stepping through the lock now. Hold your arguments until the pumps finish their cycle — "
    "the hiss is the closest thing we have to a blessing."
)

# --- Theatre catalog (rotation titles) ---
THEATRE_PLAY_TITLES: tuple[str, ...] = (
    "King Lear",
    "The Tempest",
    "Mr. Burns: A Post-Electric Play",
)

# --- Movie screening catalog (display titles by stable id) ---
MOVIE_TITLES_BY_ID: dict[str, str] = {
    "atomic_cafe": "The Atomic Cafe",
    "the_day_after": "The Day After",
    "mad_max": "Mad Max",
    "simpsons_s06e14_barts_comet": 'Simpsons S6E14 "Bart\'s Comet"',
}

# --- Narrative (Silo Bulletin; urgency prefixes !! / ! parsed elsewhere) ---
NARRATIVE_FIRST_DEPARTURE_NOTICE = (
    "!A community member has decided to brave the outdoors and leave the bunker. "
    "More may follow."
)

NARRATIVE_WELCOME_MESSAGE = (
    "Welcome to Bunker.OS 1.2.0. "
    "If you are reading this message, a nuclear apocalypse has occurred. "
    "It is not safe to go outside."
)

# --- Meet Council flavor ---
POSITIVE_COUNCIL_MESSAGES: tuple[str, ...] = (
    "The meeting went well.",
    "You left the chamber with a quiet nod from the council.",
    "Several members warmed to your proposal.",
    "The air in the room felt lighter on the way out.",
)

NEGATIVE_COUNCIL_MESSAGES: tuple[str, ...] = (
    "The council members disagreed.",
    "Sideways glances followed you to the door.",
    "The vote was not in your favor.",
    "You were asked to clarify your priorities next time.",
)

# --- Routine investigation sweep ---
ROUTINE_INVESTIGATION_DISPATCH_TEMPLATE = (
    "{n} residents detached for scheduled sweep of {subsystem}. "
    "They sign back in when the round completes."
)

ROUTINE_INVESTIGATION_COMPLETE_TEMPLATE = (
    "Sweep detail returned from {subsystem}. Routine checklist filed with nothing escalated."
)

# --- Random / scripted events ---
EVENT_RATS_SILO_INTRO_AUTO_RESOLVE_MESSAGE = (
    "The intrusion settled into a chronic nuisance: small gnaw-holes "
    "and scattered husks, but bulk stores appear intact for now."
)

EVENT_RATS_SILO_INTRO_AUTO_RESOLVE_GROUP_CHAT = (
    "Marnie: Chronic nuisance beats a panic — still, lock rotation on the grain bays."
)

EVENT_RATS_SILO_INTRO_PLAYER_RESOLVE_MESSAGE = (
    "Containment sealed the breach path and laid deterrent lines; "
    "morale improved once crews proved the bulk grain stayed accounted."
)

EVENT_RATS_SILO_INTRO_PLAYER_RESOLVE_GROUP_CHAT = (
    "Tamsin: That sweep reads honest — crews counted sacks before we spun the story."
)

EVENT_RATS_SILO_INTRO_SPAWN_ANNOUNCE = (
    "!RATS! Grain-room telemetry flagged vibration behind the inner jacket — "
    "rats have breached the silo liner. We may be able to salvage something by investigating food storage."
)

EVENT_RATS_SILO_INTRO_SPAWN_GROUP_CHAT = (
    "Vesper (quiet): Grain telemetry isn't lying — we need eyes in food storage before rumor does it for us."
)

EVENT_RATS_SILO_SPIKE_AUTO_RESOLVE_MESSAGE = (
    "The rat swarm dispersed after exhausting scattered grain. "
    "Residents are unhappy about the wasted supplies."
)

EVENT_RATS_SILO_SPIKE_AUTO_RESOLVE_GROUP_CHAT = (
    "Nadia: Swarm ate our slack — next time we don't wait on paperwork to kill lights near spillage."
)

EVENT_RATS_SILO_SPIKE_PLAYER_RESOLVE_MESSAGE = (
    "Investigation team cleared the silo breach and salvaged "
    "most of the spillage. Morale improved."
)

EVENT_RATS_SILO_SPIKE_PLAYER_RESOLVE_GROUP_CHAT = (
    "Jace: Salvage numbers match the manifest — that's the kind of proof people remember."
)

EVENT_RATS_SILO_SPIKE_SPAWN_GROUP_CHAT = (
    "Vesper: Spike signature on IR — that's not background noise, that's a corridor moving."
)

EVENT_FIRESIDE_BACKLASH_AUTO_RESOLVE_MESSAGE = (
    "Whispers about your last broadcast fade into the usual bunker noise."
)

EVENT_FIRESIDE_BACKLASH_AUTO_RESOLVE_GROUP_CHAT = (
    "Marnie: Heat's off the transcript — keep the next briefing boring on purpose."
)

EVENT_FIRESIDE_BACKLASH_SPAWN_ANNOUNCE = (
    "!!Word spreads fast: residents circulate rough transcripts and "
    "spot holes in your speech."
)

EVENT_FIRESIDE_BACKLASH_SPAWN_GROUP_CHAT = (
    "Tamsin: They're quoting you line-by-line in Corridor C — tighten the narrative or we lose them."
)

EVENT_GEIGER_EXODUS_AUTO_RESOLVE_MESSAGE = (
    "The scramble toward the hatch loses steam — whoever could bolt already did."
)

EVENT_GEIGER_EXODUS_AUTO_RESOLVE_GROUP_CHAT = (
    "Nadia: Exodus chatter peaked — those still here want a face-saving story tonight."
)

EVENT_GEIGER_RUMOR_EXODUS_BULLETIN = (
    "!!Rumors spread that Geiger readings outside are lower than what you've reported. "
    "People quietly kit up to chance it on their own; others linger, waiting on word from you."
)

EVENT_GEIGER_RUMOR_EXODUS_GROUP_CHAT = (
    "Marnie: Quiet kits by the hatch — they're comparing your numbers to scout gossip. "
    "We need alignment before this becomes a stampede."
)

# --- Focus Tree nodes ---
FOCUS_TITLE_EXPLORE_NOVEL_FOOD = "Explore Novel Food Sources"
FOCUS_DESC_EXPLORE_NOVEL_FOOD = (
    "The infestation of vermin in our food storage presents a risk and an opportunity. "
    "We need not let valuable resources go to waste."
)
FOCUS_REQ_EXPLORE_NOVEL_FOOD = "No prerequisites."

FOCUS_TITLE_FIRESIDE_CHATS = "Fireside Chats"
FOCUS_DESC_FIRESIDE_CHATS = (
    "Structured morale broadcasts beyond a single reassuring tone — rotate framing "
    "without letting rumor own the silence between drills."
)
FOCUS_REQ_FIRESIDE_CHATS = (
    "Complete Explore Novel Food Sources. Give Speech once after the rumor exodus crisis ends."
)

FOCUS_TITLE_BUNKER_SHAKESPEARE = "Found Bunker Shakespeare Company"
FOCUS_DESC_BUNKER_SHAKESPEARE = (
    "Formalize ad-hoc readings into a resident theatre cadre — boredom relief that "
    "does not pretend the outside world is hypothetical."
)
# Placeholders: loyalty_below, boredom_above (formatted with .0f in focus_tree)
FOCUS_REQ_BUNKER_SHAKESPEARE_TEMPLATE = (
    "Complete Explore Novel Food Sources. Unlocks when bunker loyalty falls below "
    "{loyalty_below:.0f}% or boredom rises above {boredom_above:.0f}."
)

FOCUS_TITLE_VENTURE_OUT = "Venture Out"
FOCUS_DESC_VENTURE_OUT = (
    "Acknowledge that some trusted voices will test the threshold themselves — "
    "and prepare the Inner Circle for harder bargains indoors."
)
FOCUS_REQ_VENTURE_OUT = (
    "Complete Fireside Chats and Found Bunker Shakespeare Company. Unlocks when "
    "population drops below two-thirds of the original headcount."
)

FOCUS_TITLE_WORSE_THAN_EXPLOITED = "The only thing worse than being exploited…"
FOCUS_DESC_WORSE_THAN_EXPLOITED = (
    "When operating cash nearly bottoms out, sanction short off-books labor — "
    "with explicit consent windows per Inner Circle member."
)
# Placeholder: cash_threshold
FOCUS_REQ_TEMP_JOB_BRANCH_TEMPLATE = (
    "Complete Venture Out. Unlocks when Inner Circle cash falls below ${cash_threshold:.0f}."
)

FOCUS_TITLE_NOT_BEING_EXPLOITED = "…is not being exploited."
FOCUS_DESC_NOT_BEING_EXPLOITED = (
    "A temp job goes sideways in public view — convert the sting into doctrine "
    "about boundaries, receipts, and rotation."
)
FOCUS_REQ_NOT_BEING_EXPLOITED = (
    "Complete Venture Out. Unlocks after a Temp Job backfires (doubt spike outcome)."
)

FOCUS_TITLE_FIRE_AND_BRIMSTONE = "Fire and Brimstone"
FOCUS_DESC_FIRE_AND_BRIMSTONE = (
    "Escalate broadcast rhetoric into explicit moral geometry — shorter cadence, "
    "sharper binaries, less room for corridor improvisation."
)
FOCUS_REQ_FIRE_AND_BRIMSTONE = "Complete both exploitation-branch focuses."

# --- Focus completion hooks (system messages) ---
MESSAGE_RAT_TRAPPERS_UNLOCKED = (
    "Quartermaster signed off on vermin-control staffing: trapper shifts are authorized "
    "under Farming allocations — recover what the infestation would waste."
)

# --- Scheduler test hook ---
TEST_SYSTEM_MESSAGE_BODY = "All systems normal."

# --- Community / Fireside panel (JSON for Grafana) ---
FIRESIDE_STOCK_LABEL_REASSURING = "Reassuring"
FIRESIDE_STOCK_LABEL_FRANK = "Frank"
FIRESIDE_STOCK_LABEL_FEARMONGERING = "Fearmongering"

FIRESIDE_PANEL_TITLE_BRIMSTONE = "Fire and Brimstone"
FIRESIDE_LABEL_BRIMSTONE_REASSURING = "Everyone outside is a sinner"
FIRESIDE_LABEL_BRIMSTONE_FRANK = "We are all sinners"
FIRESIDE_LABEL_BRIMSTONE_FEAR = "You are all sinners"

FIRESIDE_PANEL_TITLE_FIRESIDE_CHATS = "Fireside Chats"
FIRESIDE_PANEL_TITLE_GIVE_SPEECH = "Give Speech"
