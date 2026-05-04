from __future__ import annotations

from dataclasses import dataclass

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
    if user is None or not user.check_password(password):
        raise InvalidCredentialsError()
    if not user.is_active:
        raise InactiveUserError()
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
