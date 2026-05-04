"""official_races: add cancellation columns

Revision ID: 73cdba3af606
Revises: a203b9dcadc7
Create Date: 2026-05-05 00:00:00.000000

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "73cdba3af606"
down_revision = "a203b9dcadc7"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE official_races ADD COLUMN cancelled_at DATETIME"
    )
    op.execute(
        "ALTER TABLE official_races ADD COLUMN cancelled_by_user_id INTEGER "
        "REFERENCES users(id) ON DELETE SET NULL"
    )


def downgrade():
    op.execute("ALTER TABLE official_races DROP COLUMN cancelled_by_user_id")
    op.execute("ALTER TABLE official_races DROP COLUMN cancelled_at")
