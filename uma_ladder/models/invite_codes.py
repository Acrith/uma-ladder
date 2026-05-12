"""PR-Q4 — Invite-only signup gate.

`InviteCode` is a redeemable token an admin mints out-of-band and
hands to a prospective user. `InviteCodeUse` is the audit trail —
one row per successful redemption. The gate itself is governed by
``invite_only_enabled`` in `app_settings`; when that flag is OFF
this table is just sitting there, harmless.

Lifecycle: mint → (optional revoke / expiry) → consume on signup.
A code stops working as soon as ANY of:
- ``revoked_at`` is set, or
- ``expires_at`` has passed, or
- ``max_uses`` is reached (NULL = unlimited).

FKs:
- ``created_by_user_id``: SET NULL so deleting the minting admin
  preserves the code; useful if their account is later disabled.
- ``InviteCodeUse.user_id``: SET NULL so a hard-deleted user
  doesn't wipe the audit row (we still want to know "code X was
  consumed N times in the past").
- ``InviteCodeUse.invite_code_id``: CASCADE — if we ever do delete
  a code row, the audit rows for it go with it.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db


def _utcnow() -> datetime:
    return datetime.now(UTC)


class InviteCode(db.Model):
    __tablename__ = "invite_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Stored in canonical uppercase XXXX-XXXX-XXXX form. Lookup is
    # case-insensitive via the service layer's `.upper()` normalize.
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # NULL = unlimited until revoke / expiry.
    max_uses: Mapped[int | None] = mapped_column(Integer, nullable=True)
    uses_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, index=True
    )

    created_by = relationship(
        "User", lazy="joined", foreign_keys=[created_by_user_id]
    )
    uses = relationship(
        "InviteCodeUse",
        back_populates="invite_code",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (UniqueConstraint("code", name="uq_invite_codes_code"),)

    def __repr__(self) -> str:
        return (
            f"<InviteCode {self.code} "
            f"{self.uses_count}/{self.max_uses or '∞'}>"
        )


class InviteCodeUse(db.Model):
    __tablename__ = "invite_code_uses"

    id: Mapped[int] = mapped_column(primary_key=True)
    invite_code_id: Mapped[int] = mapped_column(
        ForeignKey("invite_codes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # SET NULL preserves the audit row when the claiming user is
    # later hard-deleted. The count of claims-on-this-code is
    # `uses_count` on the parent row, not this query.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    claimed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    claimed_from_ip: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )

    invite_code = relationship("InviteCode", back_populates="uses")
    user = relationship("User", lazy="joined", foreign_keys=[user_id])

    def __repr__(self) -> str:
        return (
            f"<InviteCodeUse code_id={self.invite_code_id} "
            f"user_id={self.user_id}>"
        )
