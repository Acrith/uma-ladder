"""PR-X6: did this skill fire during the race?

Revision ID: 8b21d4e0af57
Revises: 3f61c07a9d24

Plain add_column, deliberately not batch_alter_table: SQLite supports
ADD COLUMN natively, so there is no table rebuild and nothing hanging
off a CASCADE foreign key can be dropped (the PR-J10 failure mode).
"""
import sqlalchemy as sa
from alembic import op

revision = "8b21d4e0af57"
down_revision = "3f61c07a9d24"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "official_race_result_skills",
        sa.Column("activated", sa.Boolean(), nullable=True),
    )


def downgrade():
    op.drop_column("official_race_result_skills", "activated")
