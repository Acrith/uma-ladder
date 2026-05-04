from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from flask_login import UserMixin
from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column
from werkzeug.security import check_password_hash, generate_password_hash

from ..extensions import db


class Role(StrEnum):
    USER = "user"
    ORGANIZER = "organizer"
    EDITOR = "editor"
    ADMIN = "admin"
    SUPERADMIN = "superadmin"


_ROLE_ORDER: dict[str, int] = {
    Role.USER: 0,
    Role.ORGANIZER: 1,
    Role.EDITOR: 2,
    Role.ADMIN: 3,
    Role.SUPERADMIN: 4,
}


def role_rank(role: str) -> int:
    return _ROLE_ORDER[role]


def _utcnow() -> datetime:
    return datetime.now(UTC)


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default=Role.USER)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def has_role(self, *roles: str) -> bool:
        return self.role in roles

    def has_at_least(self, role: str) -> bool:
        return role_rank(self.role) >= role_rank(role)

    def __repr__(self) -> str:
        return f"<User {self.username} role={self.role}>"
