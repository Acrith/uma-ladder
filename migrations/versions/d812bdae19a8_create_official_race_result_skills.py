"""create official_race_result_skills

Revision ID: d812bdae19a8
Revises: 777cf44e7d90
Create Date: 2026-05-05 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d812bdae19a8"
down_revision = "777cf44e7d90"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "official_race_result_skills",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "official_race_result_id",
            sa.Integer(),
            sa.ForeignKey("official_race_results.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "skill_id",
            sa.Integer(),
            sa.ForeignKey("uma_skills.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("raw_ocr_text", sa.String(length=255), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_official_race_result_skills_result",
        "official_race_result_skills",
        ["official_race_result_id"],
    )


def downgrade():
    op.drop_index(
        "ix_official_race_result_skills_result",
        table_name="official_race_result_skills",
    )
    op.drop_table("official_race_result_skills")
