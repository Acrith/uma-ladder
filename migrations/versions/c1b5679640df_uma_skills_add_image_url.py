"""uma_skills: add image_url

Revision ID: c1b5679640df
Revises: d812bdae19a8
Create Date: 2026-05-05 00:00:00.000000

"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "c1b5679640df"
down_revision = "d812bdae19a8"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE uma_skills ADD COLUMN image_url VARCHAR(512)")


def downgrade():
    op.execute("ALTER TABLE uma_skills DROP COLUMN image_url")
