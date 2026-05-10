"""user_profiles: add avatar_border (PR-P1)

Adds a nullable string column for user-selectable avatar border
tone. Layer 1 of the cosmetic-progression backlog; constrained at
the service layer to a fixed palette allowlist (no free-form CSS
injection — see project_profile_customization_backlog.md).

When NULL the avatar renders with its current default border
(`border-slate-800`); when set, the value is a palette key (e.g.
"cyan", "fuchsia") that the template maps to a Tailwind ring
class. Oshi ring (PR-J7 era) keeps priority over avatar_border
when both apply.

Plain `op.add_column` per the J10 lesson — `user_profiles` is a
child of CASCADE FKs but adding a single nullable column is the
safe shape under SQLite + FK pragma.

Revision ID: 21d593ba5697
Revises: 683e2ba271ac
Create Date: 2026-05-10 16:00:00.000000
"""
import sqlalchemy as sa
from alembic import op


revision = "21d593ba5697"
down_revision = "683e2ba271ac"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "user_profiles",
        sa.Column("avatar_border", sa.String(length=16), nullable=True),
    )


def downgrade():
    op.drop_column("user_profiles", "avatar_border")
