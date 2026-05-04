from __future__ import annotations

import pytest
from flask import Flask

from uma_ladder.extensions import db
from uma_ladder.models import Role, User
from uma_ladder.models.users import role_rank


def test_password_round_trip(app: Flask) -> None:
    with app.app_context():
        user = User(username="bob", role=Role.USER)
        user.set_password("hunter2")
        db.session.add(user)
        db.session.commit()

        assert user.password_hash != "hunter2"
        assert user.check_password("hunter2") is True
        assert user.check_password("nope") is False


@pytest.mark.parametrize(
    "lower, higher",
    [
        (Role.USER, Role.ORGANIZER),
        (Role.ORGANIZER, Role.EDITOR),
        (Role.EDITOR, Role.ADMIN),
        (Role.ADMIN, Role.SUPERADMIN),
    ],
)
def test_role_hierarchy_is_monotonic(lower: str, higher: str) -> None:
    assert role_rank(lower) < role_rank(higher)


def test_user_has_at_least(app: Flask) -> None:
    with app.app_context():
        editor = User(username="ed", role=Role.EDITOR)
        editor.set_password("x" * 8)

        assert editor.has_at_least(Role.USER)
        assert editor.has_at_least(Role.EDITOR)
        assert not editor.has_at_least(Role.ADMIN)
