"""race conditions: season weather ground

Revision ID: 79e56ce92cf0
Revises: cbb5a19cdc73
Create Date: 2026-05-05 22:09:40.971704

PR-G3 — adds race_season / weather / ground_condition to the three
race-bearing tables. Nullable so existing rows survive; new entries
populated via the relevant admin/organizer form, or auto-rolled in
the draft randomizer.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '79e56ce92cf0'
down_revision = 'cbb5a19cdc73'
branch_labels = None
depends_on = None


_TABLES = ("champions_meetings", "official_races", "draft_matches")
_COLUMN_NAMES = ("race_season", "weather", "ground_condition")


def upgrade():
    for table in _TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
            for name in _COLUMN_NAMES:
                batch_op.add_column(
                    sa.Column(name, sa.String(length=8), nullable=True)
                )


def downgrade():
    for table in reversed(_TABLES):
        with op.batch_alter_table(table, schema=None) as batch_op:
            for name in reversed(_COLUMN_NAMES):
                batch_op.drop_column(name)
