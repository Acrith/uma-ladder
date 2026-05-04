"""draft_matches: add cancellation columns

Revision ID: a203b9dcadc7
Revises: 543cdd21ac39
Create Date: 2026-05-05 00:00:00.000000

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "a203b9dcadc7"
down_revision = "543cdd21ac39"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE draft_matches ADD COLUMN cancelled_at DATETIME"
    )
    op.execute(
        "ALTER TABLE draft_matches ADD COLUMN cancelled_by_user_id INTEGER "
        "REFERENCES users(id) ON DELETE SET NULL"
    )


def downgrade():
    op.execute("ALTER TABLE draft_matches DROP COLUMN cancelled_by_user_id")
    op.execute("ALTER TABLE draft_matches DROP COLUMN cancelled_at")
