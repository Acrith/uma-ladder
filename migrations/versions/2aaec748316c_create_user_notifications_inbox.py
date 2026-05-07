"""create user_notifications inbox (PR-J12)

User-facing in-app notification surface. Distinct from
discord_notification_attempts (admin-only audit log of outbound
webhooks). See uma_ladder/models/inbox.py for column rationale.

Revision ID: 2aaec748316c
Revises: 4d17a969755b
Create Date: 2026-05-07 21:46:53.981317

"""
import sqlalchemy as sa
from alembic import op


revision = "2aaec748316c"
down_revision = "4d17a969755b"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_notifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("user_notifications", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_user_notifications_user_id"),
            ["user_id"],
            unique=False,
        )
        # Composite index for the hot "unread for this user" path.
        batch_op.create_index(
            "ix_user_notifications_user_unread",
            ["user_id", "read_at"],
            unique=False,
        )


def downgrade():
    with op.batch_alter_table("user_notifications", schema=None) as batch_op:
        batch_op.drop_index("ix_user_notifications_user_unread")
        batch_op.drop_index(batch_op.f("ix_user_notifications_user_id"))
    op.drop_table("user_notifications")
