from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select

from ..extensions import db
from ..models import Role, User


class AuthError(Exception):
    pass


class UsernameTakenError(AuthError):
    pass


class InvalidCredentialsError(AuthError):
    pass


class InactiveUserError(AuthError):
    pass


class InvalidResetTokenError(AuthError):
    pass


class RateLimitedError(AuthError):
    """Raised when an account has hit MAX_FAILED_LOGINS within the
    lockout window. Carries `retry_after_seconds` so the route can
    surface a useful "try again in N min" message without leaking
    whether the username actually exists (usernames are public on
    /profiles anyway, so this isn't a real enumeration concern —
    just keeping the failure shape uniform)."""

    def __init__(self, *, retry_after_seconds: int) -> None:
        super().__init__(
            f"too many failed attempts; retry after {retry_after_seconds}s"
        )
        self.retry_after_seconds = retry_after_seconds


# PR-J10 — login lockout tuning. 10 failures in a row triggers a
# 15-minute lockout. The window is generous enough that a fat-
# fingering user clears it by waiting briefly, but tight enough
# that an online brute-force tops out at ~960 guesses/day.
MAX_FAILED_LOGINS = 10
LOCKOUT_DURATION = timedelta(minutes=15)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _ensure_aware(dt: datetime) -> datetime:
    """SQLite drops tzinfo on read — coerce naive datetimes back to
    UTC so timedelta arithmetic doesn't raise."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _is_locked_out(user: User) -> bool:
    if user.failed_login_count < MAX_FAILED_LOGINS:
        return False
    if user.failed_login_at is None:
        return False
    elapsed = _utcnow() - _ensure_aware(user.failed_login_at)
    return elapsed < LOCKOUT_DURATION


def _retry_after_seconds(user: User) -> int:
    if user.failed_login_at is None:
        return 0
    elapsed = _utcnow() - _ensure_aware(user.failed_login_at)
    return max(1, int((LOCKOUT_DURATION - elapsed).total_seconds()))


def _record_failed_login(user: User) -> None:
    """Increment counter; if the previous lockout window has fully
    elapsed since the last failure, treat this attempt as a fresh
    start (count goes to 1) so the lockout doesn't latch forever."""
    now = _utcnow()
    elapsed = (
        now - _ensure_aware(user.failed_login_at)
        if user.failed_login_at
        else None
    )
    if elapsed is not None and elapsed >= LOCKOUT_DURATION:
        user.failed_login_count = 1
    else:
        user.failed_login_count += 1
    user.failed_login_at = now
    db.session.commit()


def _reset_failed_logins(user: User) -> None:
    user.failed_login_count = 0
    user.failed_login_at = None
    db.session.commit()


@dataclass(frozen=True)
class RegistrationRequest:
    username: str
    password: str
    role: str = Role.USER


def _normalize_username(username: str) -> str:
    return username.strip().lower()


def find_user_by_username(username: str) -> User | None:
    stmt = select(User).where(User.username == _normalize_username(username))
    return db.session.scalars(stmt).first()


def register_user(req: RegistrationRequest) -> User:
    username = _normalize_username(req.username)
    if not username:
        raise AuthError("username is required")
    if not req.password:
        raise AuthError("password is required")
    if find_user_by_username(username) is not None:
        raise UsernameTakenError(username)

    user = User(username=username, role=req.role)
    user.set_password(req.password)
    db.session.add(user)
    db.session.commit()
    return user


def authenticate(username: str, password: str) -> User:
    user = find_user_by_username(username)

    # PR-J10 — lockout check fires before password verification so
    # a locked account can't be probed with new guesses every
    # request. Triggered after MAX_FAILED_LOGINS consecutive
    # failures within LOCKOUT_DURATION; auto-clears on a successful
    # login or once the window naturally elapses.
    if user is not None and _is_locked_out(user):
        raise RateLimitedError(
            retry_after_seconds=_retry_after_seconds(user)
        )

    if user is None or not user.check_password(password):
        if user is not None:
            _record_failed_login(user)
        raise InvalidCredentialsError()
    if not user.is_active:
        raise InactiveUserError()
    if user.failed_login_count or user.failed_login_at is not None:
        _reset_failed_logins(user)
    return user


_RESET_SALT = "uma-ladder.password-reset"


def _serializer(secret_key: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_key, salt=_RESET_SALT)


def issue_reset_token(secret_key: str, user: User) -> str:
    return _serializer(secret_key).dumps({"uid": user.id})


def consume_reset_token(secret_key: str, token: str, max_age_seconds: int = 3600) -> User:
    try:
        data = _serializer(secret_key).loads(token, max_age=max_age_seconds)
    except SignatureExpired as exc:
        raise InvalidResetTokenError("token expired") from exc
    except BadSignature as exc:
        raise InvalidResetTokenError("invalid token") from exc
    user = db.session.get(User, data.get("uid"))
    if user is None or not user.is_active:
        raise InvalidResetTokenError("user not found")
    return user


def set_password(user: User, new_password: str) -> None:
    if not new_password:
        raise AuthError("password is required")
    user.set_password(new_password)
    db.session.commit()
