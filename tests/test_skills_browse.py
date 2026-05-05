"""/skills browse page — paginated catalogue with search + kind filter.

Organiser-gated: this is an internal aid for correcting OCR-misread
skill names, not a public reference.
"""

from __future__ import annotations

from flask import Flask
from flask.testing import FlaskClient

from uma_ladder.extensions import db
from uma_ladder.models import Role, UmaSkill


def _login(client: FlaskClient, username: str, password: str = "password123") -> None:
    resp = client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert resp.status_code == 302


def _seed_skills(app: Flask, n: int = 5) -> None:
    with app.app_context():
        rows = [
            UmaSkill(
                gametora_id=1000 + i,
                name_en=f"Skill {i}",
                is_unique=(i == 0),
                is_inherited=(i == 1),
                rarity=2,
                enabled=True,
            )
            for i in range(n)
        ]
        # One disabled skill — must not appear in the listing.
        rows.append(
            UmaSkill(
                gametora_id=9999,
                name_en="Hidden Skill",
                enabled=False,
            )
        )
        db.session.add_all(rows)
        db.session.commit()


def test_anonymous_gets_401(client: FlaskClient, app: Flask) -> None:
    _seed_skills(app, n=1)
    resp = client.get("/skills/")
    assert resp.status_code == 401


def test_plain_user_gets_403(client: FlaskClient, app: Flask, make_user) -> None:
    _seed_skills(app, n=1)
    make_user(username="alice", role=Role.USER)
    _login(client, "alice")
    resp = client.get("/skills/")
    assert resp.status_code == 403


def test_index_lists_only_enabled_skills(
    client: FlaskClient, app: Flask, make_user
) -> None:
    _seed_skills(app, n=3)
    make_user(username="org", role=Role.ORGANIZER)
    _login(client, "org")
    resp = client.get("/skills/")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Skill 0" in body
    assert "Skill 1" in body
    assert "Hidden Skill" not in body


def test_search_filter(client: FlaskClient, app: Flask, make_user) -> None:
    _seed_skills(app, n=3)
    make_user(username="org", role=Role.ORGANIZER)
    _login(client, "org")
    resp = client.get("/skills/?q=skill+1")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Skill 1" in body
    assert "Skill 0" not in body


def test_kind_filter_unique(client: FlaskClient, app: Flask, make_user) -> None:
    _seed_skills(app, n=3)
    make_user(username="org", role=Role.ORGANIZER)
    _login(client, "org")
    resp = client.get("/skills/?kind=unique")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Skill 0" in body
    assert "Skill 1" not in body
    assert "Skill 2" not in body


def test_kind_filter_inherited(
    client: FlaskClient, app: Flask, make_user
) -> None:
    _seed_skills(app, n=3)
    make_user(username="org", role=Role.ORGANIZER)
    _login(client, "org")
    resp = client.get("/skills/?kind=inherited")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Skill 1" in body
    assert "Skill 0" not in body


def test_pagination(client: FlaskClient, app: Flask, make_user) -> None:
    _seed_skills(app, n=70)
    make_user(username="org", role=Role.ORGANIZER)
    _login(client, "org")
    resp = client.get("/skills/?page=1")
    assert resp.status_code == 200
    assert b"Page 1 / 2" in resp.data
    resp = client.get("/skills/?page=2")
    assert resp.status_code == 200
    assert b"Page 2 / 2" in resp.data
