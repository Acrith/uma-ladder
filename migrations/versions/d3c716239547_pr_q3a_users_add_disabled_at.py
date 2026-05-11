"""PR-Q3a: users add disabled_at

Revision ID: d3c716239547
Revises: a0ff809b4543
Create Date: 2026-05-11 20:41:25.577767

Soft-delete tombstone column on the users table.

Implementation note: this migration deliberately AVOIDS
`batch_alter_table('users', ...)`. The users table is the parent of
several CASCADE FKs (auth_identities, user_profiles, draft_matches,
official_race_registrations, etc.). On SQLite, batch_alter_table
recreates the parent table with a temporary name, copies rows, and
drops the original — which fires every CASCADE child and wipes
their data. This exact pattern took prod down once (PR-J10), the
incident is captured in `feedback_sqlite_migrations.md`, and the
rule is: never batch_alter_table on a CASCADE-parent.

`op.add_column` runs a direct `ALTER TABLE ... ADD COLUMN` which
SQLite has supported natively since 3.35; safe for both SQLite and
Postgres without table recreation.

The autogenerate run also detected a pile of FK / index naming
drift (constraints automatically named at boot, but Alembic sees a
mismatch each generation). That drift is harmless cosmetic naming
and is intentionally NOT included here — touching it would force
table recreation across half the schema for zero functional
benefit.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd3c716239547'
down_revision = 'a0ff809b4543'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users",
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_column("users", "disabled_at")
