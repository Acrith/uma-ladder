"""create discord_notification_attempts

Revision ID: 54e34e41ab47
Revises: 90187517dd96
Create Date: 2026-05-04 19:41:19.525374

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "54e34e41ab47"
down_revision = "90187517dd96"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "discord_notification_attempts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("target_name", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("response_code", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("discord_notification_attempts", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_discord_notification_attempts_event_type"),
            ["event_type"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_discord_notification_attempts_status"),
            ["status"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_discord_notification_attempts_target_name"),
            ["target_name"],
            unique=False,
        )


def downgrade():
    with op.batch_alter_table("discord_notification_attempts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_discord_notification_attempts_target_name"))
        batch_op.drop_index(batch_op.f("ix_discord_notification_attempts_status"))
        batch_op.drop_index(batch_op.f("ix_discord_notification_attempts_event_type"))
    op.drop_table("discord_notification_attempts")
