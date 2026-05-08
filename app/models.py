"""SQLAlchemy models.

All player-facing game state is keyed by ``User.id`` (a UUID). New gameplay
systems should follow the same pattern: a ``user_id`` foreign key with a
cascading delete, plus a timestamped row per measurement so Grafana can chart
them as a time series.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, Float, ForeignKey
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
