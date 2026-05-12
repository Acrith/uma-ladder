"""PR-Q4 — invite-code mint/validate/consume service.

Code shape: ``XXXX-XXXX-XXXX`` — 12 alphanumeric chars from a
deliberately-restricted alphabet (no ``0/O/1/I/L``) split into three
groups by hyphens for readability. 32^12 ≈ 1.15e18 possibilities,
so brute force is non-viable even before the existing per-IP rate
limit on the registration endpoint.

Consumption is intentionally a small transaction with a re-validate
step: between "validate code on POST" and "commit user creation" we
re-load the row, re-check it's still valid, increment the usage
counter, and record the audit row — all in the same commit as the
user. If two requests race for the last seat on a code, the second
one's re-validate sees ``uses_count == max_uses`` and raises. SQLite
serialises writes so this is enough; on Postgres the same flow holds
because the increment goes through the ORM identity map.

All errors are subclasses of ``InviteCodeError`` so the auth route
can render a friendly form message without leaking which specific
failure occurred (the user just needs to know "this code didn't
work").
"""

from __future__ import annotations

import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select

from ..extensions import db
from ..models import InviteCode, InviteCodeUse

# ─── Errors ──────────────────────────────────────────────────────


class InviteCodeError(Exception):
    """Base — code couldn't be applied. Subclasses say why."""


class InviteCodeNotFoundError(InviteCodeError):
    pass


class InviteCodeRevokedError(InviteCodeError):
    pass


class InviteCodeExpiredError(InviteCodeError):
    pass


class InviteCodeExhaustedError(InviteCodeError):
    pass


# ─── Code generation ─────────────────────────────────────────────

# Excludes 0/O/1/I/L for human-typeable codes. Still 32 chars, so
# 32^12 ≈ 1.15e18 possible codes — brute-force is hopeless.
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def _generate_code() -> str:
    """Generate a fresh XXXX-XXXX-XXXX code from the readable alphabet."""
    chunks = ["".join(secrets.choice(_ALPHABET) for _ in range(4))]
    chunks.append("".join(secrets.choice(_ALPHABET) for _ in range(4)))
    chunks.append("".join(secrets.choice(_ALPHABET) for _ in range(4)))
    return "-".join(chunks)


def _normalize(raw: str) -> str:
    """Strip whitespace, uppercase, and accept codes pasted without
    hyphens — useful for users who copy-pasted from chat where
    dashes were stripped."""
    cleaned = "".join(ch for ch in raw.upper() if ch.isalnum())
    if len(cleaned) == 12:
        return f"{cleaned[0:4]}-{cleaned[4:8]}-{cleaned[8:12]}"
    return raw.strip().upper()


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_utc(dt: datetime) -> datetime:
    """SQLite returns DateTime(timezone=True) columns as tz-naive;
    coerce to aware UTC so comparisons against ``_utcnow()`` don't
    raise. No-op when the value is already aware."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


# ─── Mint / bulk-mint ────────────────────────────────────────────


@dataclass(frozen=True)
class MintRequest:
    max_uses: int | None = 1
    expires_at: datetime | None = None
    label: str | None = None


def mint_code(req: MintRequest, *, actor_user_id: int | None) -> InviteCode:
    """Generate a single fresh code. Retries on the (statistically
    impossible) chance of a collision against an existing row."""
    label = (req.label or "").strip() or None
    for _attempt in range(5):
        code_str = _generate_code()
        existing = db.session.scalar(
            select(InviteCode).where(InviteCode.code == code_str)
        )
        if existing is not None:
            continue
        row = InviteCode(
            code=code_str,
            label=label,
            max_uses=req.max_uses,
            uses_count=0,
            expires_at=req.expires_at,
            created_by_user_id=actor_user_id,
            created_at=_utcnow(),
        )
        db.session.add(row)
        db.session.commit()
        return row
    # 5 collisions in a 1.15e18-space means RNG is broken.
    raise InviteCodeError("could not allocate a unique invite code")


def bulk_mint(
    req: MintRequest, *, count: int, actor_user_id: int | None
) -> list[InviteCode]:
    """Mint ``count`` codes that share the same settings. Useful for
    handing N single-use codes to a club. Each gets its own row +
    audit trail; if you'd rather have one shared multi-use code,
    call ``mint_code`` with ``max_uses=count`` instead."""
    if count < 1:
        raise InviteCodeError("count must be >= 1")
    if count > 100:
        raise InviteCodeError("bulk mint capped at 100 per call")
    return [mint_code(req, actor_user_id=actor_user_id) for _ in range(count)]


# ─── Validate / consume ──────────────────────────────────────────


def validate_code(raw: str) -> InviteCode:
    """Resolve a user-typed code string into a usable ``InviteCode``
    row or raise the right error. This is a *read* — does not
    increment usage. Call ``consume`` to actually claim a seat.

    Order of checks matches user expectation: 'wrong code' first,
    then 'right code but no longer usable' (revoked > expired >
    exhausted) so the friendly error messaging picks the most
    informative failure if we ever decide to surface specifics."""
    normalized = _normalize(raw)
    if not normalized:
        raise InviteCodeNotFoundError("empty code")
    row = db.session.scalar(
        select(InviteCode).where(InviteCode.code == normalized)
    )
    if row is None:
        raise InviteCodeNotFoundError(normalized)
    if row.revoked_at is not None:
        raise InviteCodeRevokedError(normalized)
    if row.expires_at is not None and _as_utc(row.expires_at) <= _utcnow():
        raise InviteCodeExpiredError(normalized)
    if row.max_uses is not None and row.uses_count >= row.max_uses:
        raise InviteCodeExhaustedError(normalized)
    return row


def consume_code(
    raw: str,
    *,
    user_id: int,
    claimed_from_ip: str | None,
) -> InviteCodeUse:
    """Atomically claim a seat on ``raw``. Re-validates before the
    increment so a concurrent request can't double-spend the last
    seat. The increment + audit row + commit happen in one
    transaction; if the caller already added the new user to the
    session (typical for OAuth flow), this commit ships both
    together.

    Returns the new ``InviteCodeUse`` row so the caller can log it
    if they want."""
    row = validate_code(raw)
    # Re-read the live counter — we need to re-check against any
    # concurrent claim since the validate above.
    if row.max_uses is not None and row.uses_count >= row.max_uses:
        raise InviteCodeExhaustedError(row.code)
    row.uses_count += 1
    use = InviteCodeUse(
        invite_code_id=row.id,
        user_id=user_id,
        claimed_from_ip=claimed_from_ip,
        claimed_at=_utcnow(),
    )
    db.session.add(use)
    db.session.commit()
    return use


# ─── Listing / revoke ────────────────────────────────────────────


def list_codes() -> Sequence[InviteCode]:
    """Newest first — drives the admin queue UI."""
    return list(
        db.session.scalars(
            select(InviteCode).order_by(InviteCode.created_at.desc())
        )
    )


def revoke_code(code_id: int) -> InviteCode:
    """Set ``revoked_at`` on a code. Idempotent — re-revoking is a
    no-op. Does not delete the row so the audit trail stays intact."""
    row = db.session.get(InviteCode, code_id)
    if row is None:
        raise InviteCodeNotFoundError(str(code_id))
    if row.revoked_at is None:
        row.revoked_at = _utcnow()
        db.session.commit()
    return row


def code_usage_count(code_id: int) -> int:
    """Defensive read of the audit table — should match
    ``uses_count`` on the parent row; used by tests to verify the
    increment + insert stay aligned."""
    return (
        db.session.scalar(
            select(func.count())
            .select_from(InviteCodeUse)
            .where(InviteCodeUse.invite_code_id == code_id)
        )
        or 0
    )
