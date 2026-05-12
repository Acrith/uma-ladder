"""PR-Q4 — generic key/value runtime settings.

A single-row-per-key table used for admin-toggleable feature flags
that need to outlive a deploy without an env-var change. Today only
``invite_only_enabled`` lives here (PR-Q4); future flags follow the
same shape rather than each growing its own column on some other
table.

Values are stored as strings — callers parse them into the type
they need. The service layer normalises booleans to "true" / "false".
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AppSetting(db.Model):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    def __repr__(self) -> str:
        return f"<AppSetting {self.key}={self.value!r}>"
