"""PR-SK7: add ground_condition to skill_conditions

Skill catalog extension so the Firm Conditions / Firm Course
Menace family (gametora predicate `ground_condition==1`) can be
evaluated against `OfficialRace.ground_condition`. Adding a
nullable string column to a non-CASCADE-parent table — safe with
plain `op.add_column`.

Revision ID: 8bc50427e5b7
Revises: ae324a9641fb
Create Date: 2026-05-19 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "8bc50427e5b7"
down_revision = "ae324a9641fb"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "skill_conditions",
        sa.Column("ground_condition", sa.String(length=8), nullable=True),
    )


def downgrade():
    op.drop_column("skill_conditions", "ground_condition")
