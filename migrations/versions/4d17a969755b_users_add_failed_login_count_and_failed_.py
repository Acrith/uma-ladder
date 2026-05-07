"""users add failed_login_count and failed_login_at (PR-J10)

DB-backed lockout counter for the login route — see
services/auth.py for the threshold + window. Existing rows
default to 0 / NULL which is the unlocked state.

POST-MORTEM (2026-05-07): the original version of this migration
used `op.batch_alter_table("users")` which Alembic auto-generated
without thought. SQLite's `batch_alter_table` recreate-dance does
`DROP TABLE users` mid-flight, and SQLite documents that DROP
TABLE with `PRAGMA foreign_keys = ON` performs an implicit
`DELETE FROM` first — which CASCADEd to every child row with
ON DELETE CASCADE on user_id (user_profiles, draft_matches,
draft_match_bans, draft_race_results, draft_match_invites,
official_race_registrations / results).

Production data was wiped on first deploy. Recovery was via the
2h Fly volume snapshot.

Fix: use plain `op.add_column`. SQLite has supported
`ALTER TABLE ADD COLUMN` for nullable / DEFAULTed columns since
forever — no recreate dance, no DROP, no cascade.

The follow-up "alter_column to strip server_default" that the
old migration ran is dropped entirely. The Python-level
`default=0` on the User model is the source of truth going
forward; the SQL DEFAULT clause persisting in the schema is
harmless (SQLAlchemy ORM inserts include all mapped columns
explicitly, so the DEFAULT is never consulted).

Revision ID: 4d17a969755b
Revises: c5ce51843265
Create Date: 2026-05-07 21:06:47.077378

"""
import sqlalchemy as sa
from alembic import op


revision = "4d17a969755b"
down_revision = "c5ce51843265"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users",
        sa.Column(
            "failed_login_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "failed_login_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade():
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("failed_login_at")
        batch_op.drop_column("failed_login_count")
