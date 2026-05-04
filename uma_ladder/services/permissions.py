from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any

from flask import abort
from flask_login import current_user

from ..models.users import role_rank


def has_role(user: Any, *roles: str) -> bool:
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    return user.role in roles


def has_at_least(user: Any, role: str) -> bool:
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    return role_rank(user.role) >= role_rank(role)


def role_required(*roles: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator: 401 if anonymous, 403 if role not in allow-list."""

    def decorator(view: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(view)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not getattr(current_user, "is_authenticated", False):
                abort(401)
            if current_user.role not in roles:
                abort(403)
            return view(*args, **kwargs)

        return wrapper

    return decorator


def min_role_required(min_role: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator: 401 if anonymous, 403 if rank below min_role."""

    def decorator(view: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(view)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not getattr(current_user, "is_authenticated", False):
                abort(401)
            if not has_at_least(current_user, min_role):
                abort(403)
            return view(*args, **kwargs)

        return wrapper

    return decorator
