"""Issue / verify / revoke bearer tokens for the race extractor.

Tokens are 32-byte urlsafe strings shown exactly once at creation;
only the SHA-256 digest is persisted. Verification hashes the incoming
plaintext and looks it up by the unique digest index.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime

from sqlalchemy import select

from ..extensions import db
from ..models import ApiToken, User


def _digest(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def issue_token(user: User, *, name: str = "race extractor") -> tuple[ApiToken, str]:
    """Create a token, returning ``(row, plaintext)``.

    The plaintext is knowable only at this moment — show it to the user
    now, because it cannot be recovered afterwards.
    """
    plaintext = secrets.token_urlsafe(32)
    row = ApiToken(
        user_id=user.id,
        name=(name or "race extractor")[:64],
        token_digest=_digest(plaintext),
    )
    db.session.add(row)
    db.session.commit()
    return row, plaintext


def verify_token(plaintext: str) -> User | None:
    """Owning user for a valid, unrevoked token; None otherwise.

    Stamps ``last_used_at`` on success so the UI can show when a
    machine last uploaded — and so an unexpected timestamp is a visible
    signal that a token leaked.
    """
    if not plaintext:
        return None
    row = db.session.scalars(
        select(ApiToken).where(ApiToken.token_digest == _digest(plaintext))
    ).first()
    if row is None or row.revoked_at is not None:
        return None
    # A soft-deleted account must not keep uploading.
    if row.user is None or row.user.disabled_at is not None:
        return None
    row.last_used_at = datetime.now(UTC)
    db.session.commit()
    return row.user


def revoke_token(user: User, token_id: int) -> bool:
    row = db.session.get(ApiToken, token_id)
    if row is None or row.user_id != user.id or row.revoked_at is not None:
        return False
    row.revoked_at = datetime.now(UTC)
    db.session.commit()
    return True


def list_tokens_for_user(user: User) -> list[ApiToken]:
    return list(
        db.session.scalars(
            select(ApiToken)
            .where(ApiToken.user_id == user.id)
            .order_by(ApiToken.created_at.desc())
        )
    )
