"""create uma_moe_cache

Revision ID: fbecb5472eb3
Revises: 59431f7e9266
Create Date: 2026-05-05 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "fbecb5472eb3"
down_revision = "59431f7e9266"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "uma_moe_cache",
        sa.Column("friend_code", sa.String(length=32), primary_key=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("payload_json", sa.Text(), nullable=True),
    )


def downgrade():
    op.drop_table("uma_moe_cache")
