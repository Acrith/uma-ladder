from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db


def _utcnow() -> datetime:
    return datetime.now(UTC)


class UmaMoeCache(db.Model):
    """Server-side cache of uma.moe `/api/v4/user/profile/<viewer_id>`
    responses, keyed by 12-digit friend_code (which IS the viewer_id).

    Kept separate from `UserProfile` so:
    - Cache pruning / TTL refresh doesn't cause spurious updates on user
      records.
    - Two users who paste the same friend code share one cache row
      naturally.
    - When uma.moe v4 changes shape (or shuts down) we can drop this
      table without touching any user data.

    `status` is the upstream HTTP status (200 / 404 / 0 for network
    error) — lets us cache negatives so a typo'd friend code doesn't
    keep hammering uma.moe on every page view.
    """

    __tablename__ = "uma_moe_cache"

    friend_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<UmaMoeCache {self.friend_code} status={self.status}>"
