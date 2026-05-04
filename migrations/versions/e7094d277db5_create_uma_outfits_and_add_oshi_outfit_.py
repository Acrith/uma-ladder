"""create uma_outfits and add oshi_outfit_id to user_profiles

Revision ID: e7094d277db5
Revises: 7ccc066dea1a
Create Date: 2026-05-04 21:56:33.735481

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "e7094d277db5"
down_revision = "7ccc066dea1a"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "uma_outfits",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uma_character_id", sa.Integer(), nullable=False),
        sa.Column("costume_id", sa.Integer(), nullable=False),
        sa.Column("title_en", sa.String(length=128), nullable=True),
        sa.Column("title_jp", sa.String(length=128), nullable=True),
        sa.Column("image_url", sa.String(length=512), nullable=True),
        sa.Column("rarity", sa.Integer(), nullable=True),
        sa.Column(
            "released_globally",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "source",
            sa.String(length=32),
            nullable=False,
            server_default="gametora",
        ),
        sa.Column(
            "enabled", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["uma_character_id"], ["uma_characters.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "uma_character_id",
            "costume_id",
            name="uq_uma_outfits_character_costume",
        ),
    )
    op.create_index(
        "ix_uma_outfits_uma_character_id",
        "uma_outfits",
        ["uma_character_id"],
        unique=False,
    )
    with op.batch_alter_table("user_profiles", schema=None) as batch_op:
        batch_op.add_column(sa.Column("oshi_outfit_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_user_profiles_oshi_outfit_id",
            "uma_outfits",
            ["oshi_outfit_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade():
    with op.batch_alter_table("user_profiles", schema=None) as batch_op:
        batch_op.drop_constraint(
            "fk_user_profiles_oshi_outfit_id", type_="foreignkey"
        )
        batch_op.drop_column("oshi_outfit_id")
    op.drop_index("ix_uma_outfits_uma_character_id", table_name="uma_outfits")
    op.drop_table("uma_outfits")
