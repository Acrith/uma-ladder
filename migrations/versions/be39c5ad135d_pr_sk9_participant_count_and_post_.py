"""PR-SK9: participant_count + post_number bounds

Three new columns:

- `official_races.participant_count` (nullable int): number of
  uma at the start of the race (players + CPU). Set by the
  organizer when submitting results. Used to compute each
  result's gate bracket (`post_number` in GameTora parlance)
  via the published assignment rule.

- `skill_conditions.min_post_number` / `max_post_number`
  (nullable int): bracket bounds for skills like Inner Post
  Proficiency (`post_number<=3`) and Outer Post Proficiency
  (`>=6`) and Lucky Seven (`==7`).

`official_races` is a CASCADE parent of several child tables —
plain `op.add_column` is safe for adding a nullable column;
the SQLite migrations rule only flags `batch_alter_table` on
CASCADE parents.

Revision ID: be39c5ad135d
Revises: 7f2d08d275c6
Create Date: 2026-05-19 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "be39c5ad135d"
down_revision = "7f2d08d275c6"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "official_races",
        sa.Column("participant_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "skill_conditions",
        sa.Column("min_post_number", sa.Integer(), nullable=True),
    )
    op.add_column(
        "skill_conditions",
        sa.Column("max_post_number", sa.Integer(), nullable=True),
    )


def downgrade():
    op.drop_column("skill_conditions", "max_post_number")
    op.drop_column("skill_conditions", "min_post_number")
    op.drop_column("official_races", "participant_count")
