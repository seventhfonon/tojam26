"""SQLAlchemy models.

All player-facing game state is keyed by ``User.id`` (a UUID). New gameplay
systems should follow the same pattern: a ``user_id`` foreign key with a
cascading delete, plus a timestamped row per measurement so Grafana can chart
them as a time series.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .extensions import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(db.Model):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    #: Wall-clock instant when sweep detail is recalled from Idle (if still pending).
    investigation_busy_until: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Subsystem id (see ``app.constants.GAME_SYSTEM_IDS``) this deployment targets.
    investigation_target_system: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True
    )
    #: ``rats_silo_intro`` has fired — resident rats add ongoing fluctuating food drain.
    silo_rats_introduced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: Instantaneous extra food consumption from resident rats (food units per second).
    rat_background_consumption_ps: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    #: Rat trapper hiring unlocked after ``rats_silo_intro`` clears via farming investigation.
    rat_trappers_unlocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: Until this instant (exclusive), player-directed actions are blocked (sermon).
    sermon_busy_until: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: When true, next tick after sermon ends applies completion bonuses once.
    sermon_reward_pending: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: Wall-clock window while a Fireside Chat is active; blocks other actions (like sermon).
    fireside_busy_until: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Kind queued by HTTP action; applied during the broadcast window in ``game_tick``.
    fireside_pending_kind: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    #: Portion of the current Fireside broadcast window already credited [0, 1]; reset when chat ends.
    fireside_effect_fraction_accrued: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    #: After ``geiger_rumor_exodus`` fires once, radiation-vs-doubt crossing never retriggers it.
    geiger_rumor_crisis_triggered: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    #: Rumor panic exits planned while ``geiger_rumor_exodus`` is active (snapshot at enqueue).
    rumor_exodus_quota_initial: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rumor_exodus_quota_remaining: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    radiation_samples: Mapped[list["RadiationLevel"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    population_samples: Mapped[list["BunkerPopulation"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    loyalty_samples: Mapped[list["BunkerLoyalty"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    energy_samples: Mapped[list["EnergyReserve"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    food_samples: Mapped[list["FoodReserve"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    # One row per player each; None for legacy sessions until migration/backfill.
    bunker_lighting: Mapped[Optional["BunkerLightingSystem"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )
    bunker_power_crank: Mapped[Optional["BunkerPowerCrankSystem"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )
    bunker_farming: Mapped[Optional["BunkerFarmingSystem"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )
    bunker_theatre: Mapped[Optional["BunkerTheatreSystem"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )
    profession_lines: Mapped[list["BunkerProfession"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    profession_snapshots: Mapped[list["BunkerProfessionSnapshot"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    system_messages: Mapped[list["SystemMessage"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    narrative_deliveries: Mapped[list["UserNarrativeDelivery"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    boredom_samples: Mapped[list["BunkerBoredom"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    doubt_samples: Mapped[list["BunkerDoubt"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    bunker_social_state: Mapped[Optional["BunkerSocialState"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )
    active_game_events: Mapped[list["PlayerActiveEvent"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    crop_plots: Mapped[list["BunkerCropPlot"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    environment_pixel_noise_samples: Mapped[list["EnvironmentPixelNoiseSample"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    social_movie_pixel_samples: Mapped[list["SocialMoviePixelSample"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    movie_exhaustion_rows: Mapped[list["PlayerMovieExhaustion"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    inner_circle_members: Mapped[list["InnerCircleMember"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        order_by="InnerCircleMember.slot_index",
    )
    focus_tree_completions: Mapped[list["FocusTreeCompletion"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<User {self.id}>"


class RadiationLevel(db.Model):
    """A single point-in-time outdoor radiation measurement for one player.

    ``level`` is the smooth simulated truth used for gameplay (e.g. safe
    threshold and departures). ``level_display`` is a separate noisy reading
    (like a Geiger counter) shown in the UI only.
    """

    __tablename__ = "radiation_levels"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )
    level: Mapped[float] = mapped_column(Float, nullable=False)
    level_display: Mapped[float] = mapped_column(Float, nullable=False)

    user: Mapped[User] = relationship(back_populates="radiation_samples")

    def __repr__(self) -> str:
        return (
            f"<RadiationLevel user={self.user_id} t={self.timestamp} "
            f"level={self.level:.3f} display={self.level_display:.3f}>"
        )


class BunkerPopulation(db.Model):
    """A point-in-time headcount of people still living in the bunker.

    ``departed`` records how many left during this tick. It is zero on the
    seeding row and on any tick where nobody left (radiation still too high,
    or everyone is loyal enough to stay put). Storing it here lets us render
    an exodus-events chart in Grafana without a second table.
    """

    __tablename__ = "bunker_population"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )
    count: Mapped[int] = mapped_column(Integer, nullable=False)
    departed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    user: Mapped[User] = relationship(back_populates="population_samples")

    def __repr__(self) -> str:
        return f"<BunkerPopulation user={self.user_id} t={self.timestamp} count={self.count} departed={self.departed}>"


class BunkerLoyalty(db.Model):
    """A point-in-time loyalty reading (0–100) for the bunker population.

    100 = unwavering faith in the leader's decision to stay underground.
    0   = everyone is actively planning to leave the moment it looks safe.

    A row is written every tick so Grafana gets a continuous time series even
    before any player actions exist to change the value. When gameplay systems
    that affect loyalty are added, they should append a new row rather than
    mutating in place, preserving the full history.
    """

    __tablename__ = "bunker_loyalty"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )
    loyalty: Mapped[float] = mapped_column(Float, nullable=False)

    user: Mapped[User] = relationship(back_populates="loyalty_samples")

    def __repr__(self) -> str:
        return f"<BunkerLoyalty user={self.user_id} t={self.timestamp} loyalty={self.loyalty:.1f}>"


class BunkerBoredom(db.Model):
    """Point-in-time bunker boredom (0–100); append-only for Grafana."""

    __tablename__ = "bunker_boredom"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )
    boredom: Mapped[float] = mapped_column(Float, nullable=False)

    user: Mapped[User] = relationship(back_populates="boredom_samples")

    def __repr__(self) -> str:
        return f"<BunkerBoredom user={self.user_id} t={self.timestamp} boredom={self.boredom:.1f}>"


class BunkerDoubt(db.Model):
    """Point-in-time collective doubt (0–100); append-only for Grafana."""

    __tablename__ = "bunker_doubt"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )
    doubt: Mapped[float] = mapped_column(Float, nullable=False)

    user: Mapped[User] = relationship(back_populates="doubt_samples")

    def __repr__(self) -> str:
        return f"<BunkerDoubt user={self.user_id} t={self.timestamp} doubt={self.doubt:.1f}>"


class BunkerSocialState(db.Model):
    """Mutable social controls: cooldowns, diminishing-return counters, hidden inner circle."""

    __tablename__ = "bunker_social_state"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    inner_circle_loyalty: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    movie_action_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    speech_action_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_show_movie_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    movie_screening_movie_id: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True
    )
    movie_screening_started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: PNG frame cursor for ``record_social_movie_pixel_sample`` while a screening is active.
    movie_pixel_frame_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_give_speech_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_meet_council_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Anchor for :data:`~app.constants.FIRESIDE_CHAT_COOLDOWN_SECONDS` between chat starts.
    last_fireside_chat_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Off-books currency for Inner Circle actions (temp jobs, etc.).
    inner_circle_cash: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    #: Mandatory basket-weaving hours per resident (0..``constants.BASKET_WEAVING_HOURS_MAX``).
    basket_weaving_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    user: Mapped[User] = relationship(back_populates="bunker_social_state")

    def __repr__(self) -> str:
        return (
            f"<BunkerSocialState user={self.user_id} "
            f"inner_circle={self.inner_circle_loyalty}>"
        )


class InnerCircleMember(db.Model):
    """One influential resident the leader manages directly (no formal titles)."""

    __tablename__ = "inner_circle_members"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    slot_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    loyalty: Mapped[float] = mapped_column(Float, nullable=False)
    #: Instantaneous stress (0 calm .. 100 furious); bunker loyalty & doubt pull this around.
    frustration: Mapped[float] = mapped_column(Float, nullable=False)
    #: Static agreeableness (0 prickly .. 100 accommodating); slows loyalty tracking frustration.
    disposition: Mapped[float] = mapped_column(Float, nullable=False)
    popularity: Mapped[float] = mapped_column(Float, nullable=False)
    task_kind: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    task_started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    task_ends_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped["User"] = relationship(back_populates="inner_circle_members")


class InnerCircleCashSample(db.Model):
    """Time series of Inner Circle cash for Grafana."""

    __tablename__ = "inner_circle_cash_samples"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )
    cash: Mapped[float] = mapped_column(Float, nullable=False)

    user: Mapped["User"] = relationship()


class PlayerMovieExhaustion(db.Model):
    """Per-title screening fatigue; decays over time in ``game_tick``."""

    __tablename__ = "player_movie_exhaustion"
    __table_args__ = (
        UniqueConstraint("user_id", "movie_id", name="uq_player_movie_exhaustion_user_movie"),
    )

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    movie_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    exhaustion: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    screenings_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    user: Mapped[User] = relationship(back_populates="movie_exhaustion_rows")

    def __repr__(self) -> str:
        return (
            f"<PlayerMovieExhaustion user={self.user_id} movie={self.movie_id!r} "
            f"ex={self.exhaustion:.1f}>"
        )


class EnergyReserve(db.Model):
    """A point-in-time reading of the bunker's electrical energy reserves.

    Energy increases from crank workers and manual cranks; decreases from
    active systems (lights, HVAC, etc.). Written every tick so Grafana can
    plot a continuous curve.
    """

    __tablename__ = "energy_reserves"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )
    level: Mapped[float] = mapped_column(Float, nullable=False)

    user: Mapped[User] = relationship(back_populates="energy_samples")

    def __repr__(self) -> str:
        return f"<EnergyReserve user={self.user_id} t={self.timestamp} level={self.level:.2f}>"


class FoodReserve(db.Model):
    """Point-in-time bunker food stockpile and instantaneous economy rates.

    ``consumption_per_second`` and ``production_per_second`` mirror the values
    used for that tick's delta so Grafana can chart stores vs rates without joins.
    """

    __tablename__ = "food_reserves"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )
    level: Mapped[float] = mapped_column(Float, nullable=False)
    consumption_per_second: Mapped[float] = mapped_column(Float, nullable=False)
    production_per_second: Mapped[float] = mapped_column(Float, nullable=False)

    user: Mapped[User] = relationship(back_populates="food_samples")

    def __repr__(self) -> str:
        return (
            f"<FoodReserve user={self.user_id} t={self.timestamp} level={self.level:.1f} "
            f"cons={self.consumption_per_second:.2f}/s prod={self.production_per_second:.2f}/s>"
        )


class BunkerProfession(db.Model):
    """Mutable headcount for one profession slot (one row per user per profession).

    Worker-assigned bunker systems reference the row for ``Power crank``,
    ``Farming``, ``Rat trapping``, or ``Theatre``. ``Idle`` has no system row.
    Tick snapshots copy these counts into :class:`BunkerProfessionSnapshot`.
    """

    __tablename__ = "bunker_professions"
    __table_args__ = (
        UniqueConstraint("user_id", "profession", name="uq_bunker_professions_user_profession"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    profession: Mapped[str] = mapped_column(String(64), nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    user: Mapped[User] = relationship(back_populates="profession_lines")
    power_crank_slot: Mapped[Optional["BunkerPowerCrankSystem"]] = relationship(
        back_populates="profession_line",
        uselist=False,
    )
    farming_slot: Mapped[Optional["BunkerFarmingSystem"]] = relationship(
        back_populates="profession_line",
        foreign_keys="[BunkerFarmingSystem.profession_line_id]",
        uselist=False,
    )
    rat_trapping_slot: Mapped[Optional["BunkerFarmingSystem"]] = relationship(
        back_populates="rat_trapper_line",
        foreign_keys="[BunkerFarmingSystem.rat_trapper_line_id]",
        uselist=False,
    )
    theatre_slot: Mapped[Optional["BunkerTheatreSystem"]] = relationship(
        back_populates="profession_line",
        uselist=False,
    )

    def __repr__(self) -> str:
        return (
            f"<BunkerProfession user={self.user_id} {self.profession!r} count={self.count}>"
        )


class BunkerLightingSystem(db.Model):
    """Lighting draw toggle; one mutable row per player."""

    __tablename__ = "bunker_lighting_systems"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    lights_on: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    user: Mapped[User] = relationship(back_populates="bunker_lighting")

    def __repr__(self) -> str:
        return f"<BunkerLightingSystem user={self.user_id} lights={'on' if self.lights_on else 'off'}>"


class BunkerPowerCrankSystem(db.Model):
    """Power crank station; worker headcount lives on :attr:`profession_line`."""

    __tablename__ = "bunker_power_crank_systems"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    profession_line_id: Mapped[int] = mapped_column(
        ForeignKey("bunker_professions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    user: Mapped[User] = relationship(back_populates="bunker_power_crank")
    profession_line: Mapped[BunkerProfession] = relationship(
        back_populates="power_crank_slot",
        foreign_keys=[profession_line_id],
    )

    def __repr__(self) -> str:
        return f"<BunkerPowerCrankSystem user={self.user_id} line={self.profession_line_id}>"


class BunkerFarmingSystem(db.Model):
    """Hydroponics / farm station; farm workers on :attr:`profession_line`, trappers on :attr:`rat_trapper_line`.

    Crop timers live per plot on :class:`BunkerCropPlot`.
    """

    __tablename__ = "bunker_farming_systems"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    profession_line_id: Mapped[int] = mapped_column(
        ForeignKey("bunker_professions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    rat_trapper_line_id: Mapped[int] = mapped_column(
        ForeignKey("bunker_professions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    user: Mapped[User] = relationship(back_populates="bunker_farming")
    profession_line: Mapped[BunkerProfession] = relationship(
        back_populates="farming_slot",
        foreign_keys=[profession_line_id],
    )
    rat_trapper_line: Mapped[BunkerProfession] = relationship(
        back_populates="rat_trapping_slot",
        foreign_keys=[rat_trapper_line_id],
    )

    def __repr__(self) -> str:
        return (
            f"<BunkerFarmingSystem user={self.user_id} farm_line={self.profession_line_id} "
            f"rat_line={self.rat_trapper_line_id}>"
        )


class BunkerTheatreSystem(db.Model):
    """Community theater; actors on :attr:`profession_line`. Phases advance on tick time."""

    __tablename__ = "bunker_theatre_systems"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    profession_line_id: Mapped[int] = mapped_column(
        ForeignKey("bunker_professions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    #: idle | writing | rehearsing | ready
    phase: Mapped[str] = mapped_column(String(32), nullable=False, default="idle")
    #: Rotating script for this bunker cycle; advances after each performance.
    play_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    phase_entered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    next_performance_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    user: Mapped[User] = relationship(back_populates="bunker_theatre")
    profession_line: Mapped[BunkerProfession] = relationship(
        back_populates="theatre_slot",
        foreign_keys=[profession_line_id],
    )

    def __repr__(self) -> str:
        return (
            f"<BunkerTheatreSystem user={self.user_id} phase={self.phase!r} "
            f"next_show={self.next_performance_at}>"
        )


class BunkerCropPlot(db.Model):
    """One hydroponic plot; ``crop_ready_at`` set while crops mature."""

    __tablename__ = "bunker_crop_plots"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    plot_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    crop_ready_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    crop_planted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Riemann sum of (farm workers × Δt) while this plot is growing (reset on plant).
    growth_worker_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    user: Mapped[User] = relationship(back_populates="crop_plots")

    def __repr__(self) -> str:
        return (
            f"<BunkerCropPlot user={self.user_id} plot={self.plot_index} "
            f"ready={self.crop_ready_at}>"
        )


class BunkerProfessionSnapshot(db.Model):
    """Append-only profession counts per tick (Grafana time series)."""

    __tablename__ = "bunker_profession_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )
    profession: Mapped[str] = mapped_column(String(64), nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False)

    user: Mapped[User] = relationship(back_populates="profession_snapshots")

    def __repr__(self) -> str:
        return (
            f"<BunkerProfessionSnapshot user={self.user_id} t={self.timestamp} "
            f"{self.profession!r}={self.count}>"
        )


class SystemMessage(db.Model):
    """A one-way narrative or event message delivered to a player.

    Messages are written by server-side jobs (scripted events, game milestones,
    alerts) and displayed in the UI as a scrolling terminal log. Body text
    should be plain ASCII — no HTML, since the frontend will escape it before
    inserting into the DOM.
    """

    __tablename__ = "system_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    #: ``bulletin`` = bunker-wide Silo Bulletin; ``group_chat`` = Inner Circle log only.
    channel: Mapped[str] = mapped_column(String(32), nullable=False, default="bulletin")

    user: Mapped[User] = relationship(back_populates="system_messages")

    def __repr__(self) -> str:
        return f"<SystemMessage user={self.user_id} t={self.timestamp} body={self.body!r}>"


class EnvironmentPixelNoiseSample(db.Model):
    """Environment dashboard heatmap: timestamped vertical strips per player.

    Each game tick replaces all rows with synthetic history (see
    ``record_environment_pixel_noise_sample``). The heatmap's horizontal axis is
    **time**; each row stores one strip as ``cells``: ``list[float]`` length
    ``grid_rows`` (values in ``[0, 1]``). ``grid_cols`` is ``1`` for this layout.
    """

    __tablename__ = "environment_pixel_noise_samples"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )
    grid_cols: Mapped[int] = mapped_column(Integer, nullable=False)
    grid_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    cells: Mapped[list[Any]] = mapped_column(JSON, nullable=False)

    user: Mapped[User] = relationship(back_populates="environment_pixel_noise_samples")

    def __repr__(self) -> str:
        return (
            f"<EnvironmentPixelNoiseSample user={self.user_id} t={self.timestamp} "
            f"{self.grid_cols}x{self.grid_rows}>"
        )


class SocialMoviePixelSample(db.Model):
    """Social dashboard movie-screen strips: one ``movie_id`` channel per player per tick."""

    __tablename__ = "social_movie_pixel_samples"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )
    movie_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    grid_cols: Mapped[int] = mapped_column(Integer, nullable=False)
    grid_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    cells: Mapped[list[Any]] = mapped_column(JSON, nullable=False)

    user: Mapped[User] = relationship(back_populates="social_movie_pixel_samples")

    def __repr__(self) -> str:
        return (
            f"<SocialMoviePixelSample user={self.user_id} movie={self.movie_id!r} "
            f"t={self.timestamp} {self.grid_cols}x{self.grid_rows}>"
        )


class PlayerActiveEvent(db.Model):
    """Concurrent random gameplay events per player (one row per active definition).

    Rows are removed when the event auto-resolves (if timed) or is cleared via investigation.
    """

    __tablename__ = "player_active_events"
    __table_args__ = (
        UniqueConstraint("user_id", "kind", name="uq_player_active_events_user_kind"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    #: ``None`` — auto-resolve disabled until cleared via investigation (when tied to ``system``).
    auto_resolve_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    #: Optional subsystem this event belongs to; sweep dispatch can clear it when ids match.
    system: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    user: Mapped[User] = relationship(back_populates="active_game_events")

    def __repr__(self) -> str:
        return (
            f"<PlayerActiveEvent id={self.id!r} user={self.user_id} kind={self.kind!r} "
            f"system={self.system!r} until={self.auto_resolve_at!r}>"
        )


class FocusTreeCompletion(db.Model):
    """Player-completed node on the Grafana Focus Tree dashboard."""

    __tablename__ = "focus_tree_completions"
    __table_args__ = (
        UniqueConstraint("user_id", "node_id", name="uq_focus_tree_user_node"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    node_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    user: Mapped[User] = relationship(back_populates="focus_tree_completions")


class UserNarrativeDelivery(db.Model):
    """Records that a scripted narrative line has been delivered to a player.

    Each ``message_id`` corresponds to an entry in ``app.narrative`` (code-defined).
    A row is inserted the first time the trigger fires; subsequent ticks skip
    even if conditions would match again.
    """

    __tablename__ = "user_narrative_deliveries"
    __table_args__ = (
        UniqueConstraint("user_id", "message_id", name="uq_user_narrative_message"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    message_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    delivered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    user: Mapped[User] = relationship(back_populates="narrative_deliveries")

    def __repr__(self) -> str:
        return f"<UserNarrativeDelivery user={self.user_id} message_id={self.message_id!r}>"
