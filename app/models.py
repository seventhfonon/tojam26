"""SQLAlchemy models.

All player-facing game state is keyed by ``User.id`` (a UUID). New gameplay
systems should follow the same pattern: a ``user_id`` foreign key with a
cascading delete, plus a timestamped row per measurement so Grafana can chart
them as a time series.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, Float, ForeignKey, Integer
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

    def __repr__(self) -> str:
        return f"<User {self.id}>"


class RadiationLevel(db.Model):
    """A single point-in-time outdoor radiation measurement for one player."""

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

    user: Mapped[User] = relationship(back_populates="radiation_samples")

    def __repr__(self) -> str:
        return f"<RadiationLevel user={self.user_id} t={self.timestamp} level={self.level:.3f}>"


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
