from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Achievement(db.Model):
    """Catalogue of earnable achievements (PR-P2).

    Definitions are seeded via migration; new ones land via
    follow-up migrations (or admin tooling later if it pays back).
    The ``key`` is the stable identifier service code uses for
    auto-grant lookups (e.g. ``link_discord``, ``first_race``);
    the display name + description + icon are the user-facing
    fields.

    ``tier`` is purely cosmetic — drives the badge color (slate /
    bronze / silver / gold). It does NOT carry behavioural
    semantics; rare achievements aren't gated by tier alone.

    Schema designed to also host border-unlock definitions in
    Layer 2 of the cosmetic-progression backlog by adding a
    ``kind`` column or a sibling table — for now this is
    achievement-only to keep the surface small.
    """

    __tablename__ = "achievements"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    # Single character / short string. Today: emoji or single
    # letter. Future: an icon-set key once we standardise on a
    # particular badge sprite library.
    icon: Mapped[str] = mapped_column(String(16), nullable=False, default="★")
    # "slate" | "bronze" | "silver" | "gold" — drives badge tone.
    tier: Mapped[str] = mapped_column(
        String(16), nullable=False, default="slate"
    )
    # Short hint like "oauth_link" / "race_result" / "season_close" /
    # "manual" — purely informational for admin / debugging today;
    # auto-grant logic dispatches on `key`, not `source_kind`.
    source_kind: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )
    # Lower numbers render first; same number → name asc.
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100
    )
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False)

    def __repr__(self) -> str:
        return f"<Achievement {self.key}>"


class UserAchievement(db.Model):
    """One row per user-achievement unlock (PR-P2).

    Composite PK on ``(user_id, achievement_id)`` enforces the
    once-per-user invariant without a separate UniqueConstraint.
    ``unlocked_at`` is the timestamp the user earned it; ``source``
    is free-form context for audit / debug ("oauth_callback",
    "admin:<admin_username>", etc.).

    No status column — rows are always "earned." If we ever need
    revocation, that's a delete (or a soft-delete column added
    later).
    """

    __tablename__ = "user_achievements"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    achievement_id: Mapped[int] = mapped_column(
        ForeignKey("achievements.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    unlocked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)

    achievement = relationship("Achievement", lazy="joined")
    user = relationship("User", lazy="joined")

    def __repr__(self) -> str:
        return (
            f"<UserAchievement user={self.user_id} "
            f"achievement={self.achievement_id}>"
        )
