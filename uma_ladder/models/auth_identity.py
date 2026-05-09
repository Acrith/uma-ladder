from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AuthIdentity(db.Model):
    """External-provider login identity bound to a local User.

    One User can own many identities (e.g. Discord today, Google
    later). Login resolution looks up by `(provider, external_id)`,
    which is unique. `user_id` ON DELETE CASCADE so deleting a user
    drops their identity rows automatically.

    The model is intentionally provider-agnostic: only the columns
    every provider can populate live here. Provider-specific extras
    (Discord guild membership, Google hosted-domain) belong in a
    side table or a JSON blob, not here — none are needed today.
    """

    __tablename__ = "auth_identities"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "external_id",
            name="uq_auth_identities_provider_external_id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True
    )
    # Discord snowflake or Google sub. Text so 64-bit IDs survive
    # JSON / DB roundtrips without precision loss.
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    external_username: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(
        String(512), nullable=True
    )
    linked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped[User] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "User", backref="auth_identities", lazy="joined"
    )

    def __repr__(self) -> str:
        return (
            f"<AuthIdentity provider={self.provider} "
            f"external_id={self.external_id} user_id={self.user_id}>"
        )
