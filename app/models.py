"""SQLAlchemy models.

All player-facing game state is keyed by ``User.id`` (a UUID). New gameplay
systems should follow the same pattern: a ``user_id`` foreign key with a
cascading delete, plus a timestamped row per measurement so Grafana can chart
them as a time series.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
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
    # One row per player; None for legacy users created before this feature.
    bunker_systems: Mapped[Optional["BunkerSystems"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )
    system_messages: Mapped[list["SystemMessage"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    narrative_deliveries: Mapped[list["UserNarrativeDelivery"]] = relationship(
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


class BunkerSystems(db.Model):
    """Current operational state of the bunker's controllable systems.

    Unlike the time-series tables, this is a single mutable row per player —
    the game's "control panel." Player actions update it in place; the game
    tick reads it to compute energy deltas and loyalty effects.

    New systems (HVAC, water reclamation, etc.) should be added as bool
    columns here with their draw rate defined in config.
    """

    __tablename__ = "bunker_systems"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    lights_on: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    crank_workers: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    food_workers: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    crop_ready_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    user: Mapped[User] = relationship(back_populates="bunker_systems")

    def __repr__(self) -> str:
        return (
            f"<BunkerSystems user={self.user_id} "
            f"lights={'on' if self.lights_on else 'off'} "
            f"crank_workers={self.crank_workers} food_workers={self.food_workers}>"
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

    user: Mapped[User] = relationship(back_populates="system_messages")

    def __repr__(self) -> str:
        return f"<SystemMessage user={self.user_id} t={self.timestamp} body={self.body!r}>"


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
