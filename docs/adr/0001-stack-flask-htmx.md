# ADR 0001 — Backend stack: Flask app-factory + SQLAlchemy + HTMX

Status: Proposed
Date: 2026-05-04
Deciders: human maintainer (review), Claude Code (drafting)

## Context

`PROJECT_INTENTIONS.md` (§4) recommends Flask app-factory + SQLAlchemy 2.x +
migrations + Jinja/HTMX, but explicitly allows Django as an alternative if
strongly justified. Uma Ladder needs:

- Server-rendered pages with a few interactive flows (draft submission,
  ban phase, room-code reveal) that fit HTMX partial swaps.
- Background-ish work for Discord webhooks, OCR parsing, and scheduled
  expiry of room codes — but nothing real-time.
- Role-based access (anonymous → user → organizer → editor → admin →
  superadmin) with server-side enforcement.
- Two ladders (points-based official, Elo-based draft) as services with
  unit tests.
- A character/preset seeding pipeline that imports from GameTora and
  must be re-runnable and idempotent.
- Eventual Fly.io deployment with PostgreSQL.

The existing prototype is a single-file Flask app (see `docs/REPO_AUDIT.md`),
so the team already has Flask familiarity. The product is closer to a CRUD
+ workflow app than to a content-heavy site.

## Decision

Adopt the recommended stack:

| Concern | Choice |
|---|---|
| Web framework | Flask 3.x with the app-factory pattern |
| ORM | SQLAlchemy 2.x (typed `Mapped[]` style) |
| Migrations | Alembic via Flask-Migrate |
| Sessions/auth | Flask-Login |
| Forms / CSRF | Flask-WTF |
| Templates | Jinja2 with HTMX for partial updates |
| Styling | Tailwind CSS via the Tailwind CLI (no Node toolchain at runtime) |
| DB (dev/test) | SQLite |
| DB (prod) | PostgreSQL |
| Tests | pytest + pytest-flask + factory_boy (or hand-rolled fixtures) |
| Lint/format | ruff (lint + format) |
| Type checks | optional mypy on `services/` only, not enforced day one |
| Background work | none initially — synchronous webhook send + retry log; revisit RQ when load demands |
| Deployment | Fly.io with a `Dockerfile` and env-var-driven config |

Project layout follows `PROJECT_INTENTIONS.md` §16 (blueprints split by
feature area, with `services.py` modules holding business logic and route
handlers kept thin).

## Why not Django

Django gives admin, auth, ORM, and migrations out of the box. For this app
those wins are real but smaller than they look:

- **Auth**: Flask-Login + Flask-WTF cover the needs (username/password,
  password reset). Django's user model would still be customized for the
  profile fields, role enum, and friend code.
- **Admin**: useful, but the app already needs custom organizer, editor,
  and admin dashboards (intentions §11, §6) with workflow-specific actions
  that don't map cleanly onto Django admin's model-CRUD UI.
- **ORM**: Django ORM is fine; SQLAlchemy 2.x is more flexible for the
  service-oriented split and integrates with Alembic, which is what the
  team's Flask code already used.
- **Templating + HTMX**: works equally well in either; not a deciding
  factor.

The deciding factors against Django:

1. **Continuity**: the existing prototype is Flask. Code reading skills,
   debugging instincts, and deployment recipes carry over. Switching
   frameworks adds risk without proportional benefit.
2. **Service-first structure**: intentions §16 wants business logic in
   service modules separate from routes. Flask's lighter bias makes that
   structure feel native; Django's "fat model + view" defaults push the
   other way and the team would constantly fight them.
3. **HTMX-style partial endpoints**: trivial in Flask; in Django typically
   needs `django-htmx` and view-class gymnastics.

If a future requirement (e.g. heavy admin tooling, multi-tenant content
management) shifts the calculus, revisit by writing ADR 0002.

## Consequences

Positive:

- Smaller framework surface area to learn or debug.
- Clean separation between routes and services from day one.
- HTMX + Jinja keeps the frontend cheap and reviewable.
- Tailwind CLI gives modern styling without a JS toolchain in production.

Negative / costs we accept:

- We build our own admin views (no Django admin freebies).
- We must wire CSRF, password reset, role checks ourselves — but the
  prototype already does, so this is known territory.
- Alembic requires care for enum changes and constraint renames; we will
  add a CI check that `flask db upgrade` runs cleanly on a fresh DB.

## Implications for the next PR

PR 1 (foundation, see `docs/IMPLEMENTATION_PLAN.md`) installs only what
this ADR commits to: Flask, SQLAlchemy 2.x, Flask-Migrate, Flask-Login,
Flask-WTF, Jinja, HTMX (CDN tag in base layout), Tailwind CLI, pytest,
ruff. No domain logic. No models beyond an empty `db = SQLAlchemy()`
extension wired into the factory.
