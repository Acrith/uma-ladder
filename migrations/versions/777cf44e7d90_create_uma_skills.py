"""create uma_skills table

Revision ID: 777cf44e7d90
Revises: 73cdba3af606
Create Date: 2026-05-05 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "777cf44e7d90"
down_revision = "73cdba3af606"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "uma_skills",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("gametora_id", sa.Integer(), nullable=False, unique=True),
        sa.Column("name_en", sa.String(length=255), nullable=False),
        sa.Column("name_jp", sa.String(length=255), nullable=True),
        sa.Column("description_en", sa.Text(), nullable=True),
        sa.Column("description_jp", sa.Text(), nullable=True),
        sa.Column("icon_id", sa.Integer(), nullable=True),
        sa.Column("rarity", sa.Integer(), nullable=True),
        sa.Column("is_unique", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_inherited", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("parent_gametora_id", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_uma_skills_gametora_id", "uma_skills", ["gametora_id"], unique=True
    )
    op.create_index("ix_uma_skills_name_en", "uma_skills", ["name_en"])


def downgrade():
    op.drop_index("ix_uma_skills_name_en", table_name="uma_skills")
    op.drop_index("ix_uma_skills_gametora_id", table_name="uma_skills")
    op.drop_table("uma_skills")
