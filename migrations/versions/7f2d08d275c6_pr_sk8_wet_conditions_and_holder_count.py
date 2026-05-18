"""PR-SK8: ground_condition_exclude + min_holders / max_holders

Three new columns on skill_conditions:

- `ground_condition_exclude` (nullable str): skill applies UNLESS
  the race's ground_condition equals this value. Handles the Wet
  Conditions family (gametora predicate
  `ground_condition==2@==3@==4` = anything but Firm), which our
  single-value `ground_condition` column couldn't express.

- `min_holders` / `max_holders` (nullable int): skill applies only
  when the count of umas in the race who have this exact skill
  falls in [min, max]. Handles Sympathy (gametora predicate
  `same_skill_horse_count>=5` → min_holders=5) and Lone Wolf
  (`==1` → min=max=1).

Adding nullable columns to a non-CASCADE-parent table — plain
`op.add_column` is safe.

Revision ID: 7f2d08d275c6
Revises: 8bc50427e5b7
Create Date: 2026-05-19 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "7f2d08d275c6"
down_revision = "8bc50427e5b7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "skill_conditions",
        sa.Column(
            "ground_condition_exclude", sa.String(length=8), nullable=True
        ),
    )
    op.add_column(
        "skill_conditions",
        sa.Column("min_holders", sa.Integer(), nullable=True),
    )
    op.add_column(
        "skill_conditions",
        sa.Column("max_holders", sa.Integer(), nullable=True),
    )


def downgrade():
    op.drop_column("skill_conditions", "max_holders")
    op.drop_column("skill_conditions", "min_holders")
    op.drop_column("skill_conditions", "ground_condition_exclude")
