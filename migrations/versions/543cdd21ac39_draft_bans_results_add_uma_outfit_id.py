"""draft bans + results: add uma_outfit_id

Revision ID: 543cdd21ac39
Revises: e7094d277db5
Create Date: 2026-05-04 22:56:11.983276

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "543cdd21ac39"
down_revision = "e7094d277db5"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE draft_match_bans ADD COLUMN uma_outfit_id INTEGER "
        "REFERENCES uma_outfits(id) ON DELETE SET NULL"
    )
    op.execute(
        "ALTER TABLE draft_race_results ADD COLUMN uma_outfit_id INTEGER "
        "REFERENCES uma_outfits(id) ON DELETE SET NULL"
    )


def downgrade():
    op.execute("ALTER TABLE draft_race_results DROP COLUMN uma_outfit_id")
    op.execute("ALTER TABLE draft_match_bans DROP COLUMN uma_outfit_id")
