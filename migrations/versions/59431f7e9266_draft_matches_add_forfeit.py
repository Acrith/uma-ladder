"""draft_matches: add forfeit columns

Revision ID: 59431f7e9266
Revises: c1b5679640df
Create Date: 2026-05-05 00:00:00.000000

"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "59431f7e9266"
down_revision = "c1b5679640df"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE draft_matches ADD COLUMN forfeit_user_id INTEGER "
        "REFERENCES users(id) ON DELETE SET NULL"
    )
    op.execute(
        "ALTER TABLE draft_matches ADD COLUMN forfeit_reason VARCHAR(64)"
    )


def downgrade():
    op.execute("ALTER TABLE draft_matches DROP COLUMN forfeit_reason")
    op.execute("ALTER TABLE draft_matches DROP COLUMN forfeit_user_id")
