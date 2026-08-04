"""Bearer tokens for the desktop race extractor (uma-race-extract).

Stored only as a SHA-256 digest — the plaintext is shown once at
creation and never again, so a DB dump alone does not grant upload
access. Same storage approach as the password-reset tokens.

These tokens are scoped to THIS site (umaladder.moe). The sibling IT
project at training.umaladder.moe has its own separate table and its
own tokens; the two are never interchangeable.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, backref, mapped_column, relationship

from ..extensions import db


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ApiToken(db.Model):
    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Human-readable label so a user with several machines can tell
    # them apart when revoking.
    name: Mapped[str] = mapped_column(
        String(64), nullable=False, default="race extractor"
    )
    token_digest: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # passive_deletes + cascade so deleting a user doesn't trip the
    # NOT NULL FK — see the ORM-cascade note in the project memory.
    user = relationship(
        "User",
        backref=backref("api_tokens", passive_deletes=True, cascade="all, delete"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<ApiToken id={self.id} user={self.user_id} name={self.name!r}>"
