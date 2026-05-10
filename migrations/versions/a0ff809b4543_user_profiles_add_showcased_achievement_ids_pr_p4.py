"""user_profiles: add showcased_achievement_ids (PR-P4)

Adds a nullable JSON column for the user-pinnable achievement
showcase rendered at the bottom of the Overview hero. Stores an
ordered list of ``Achievement.id`` ints (e.g. ``[3, 1, 7]``).
SQLAlchemy's ``JSON`` column maps to TEXT in SQLite and a
real JSON column in Postgres — same model code either way.

Cap (SHOWCASE_MAX = 6) is enforced at the service / form layer,
not the schema, so we can tune it later without a migration.

Stale ids (achievement deleted or disabled) are filtered at
render time by the service helper, so an out-of-sync list never
500s; users just see fewer chips than they pinned.

Plain ``op.add_column`` per the J10 lesson — ``user_profiles`` is
the parent of CASCADE FKs but adding a single nullable column
is the safe shape under SQLite + FK pragma.

Revision ID: a0ff809b4543
Revises: 58f6e4ad7227
Create Date: 2026-05-10 22:00:00.000000
"""
import sqlalchemy as sa
from alembic import op


revision = "a0ff809b4543"
down_revision = "58f6e4ad7227"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "user_profiles",
        sa.Column("showcased_achievement_ids", sa.JSON(), nullable=True),
    )


def downgrade():
    op.drop_column("user_profiles", "showcased_achievement_ids")
