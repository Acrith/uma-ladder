"""users add failed_login_count and failed_login_at (PR-J10)

DB-backed lockout counter for the login route — see
services/auth.py for the threshold + window. Existing rows
default to 0 / NULL which is the unlocked state.

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
    with op.batch_alter_table("users", schema=None) as batch_op:
        # `server_default="0"` so existing rows backfill cleanly
        # without a NOT NULL violation. We strip the SQL DEFAULT
        # immediately after — the model's Python-level default(0)
        # is what we want as the source of truth going forward.
        batch_op.add_column(
            sa.Column(
                "failed_login_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column(
                "failed_login_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column("failed_login_count", server_default=None)


def downgrade():
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("failed_login_at")
        batch_op.drop_column("failed_login_count")
