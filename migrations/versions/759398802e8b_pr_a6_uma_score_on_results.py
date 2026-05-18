"""PR-A6: uma_score column on official_race_results

Revision ID: 759398802e8b
Revises: 4dfda67e1c43
Create Date: 2026-05-18

The OCR sheet extractor already pulls the in-game "uma score"
integer (e.g. 17,307 from a Tamamo Cross sheet) into
``UmaSheetExtract.header.uma_score`` — currently shown on the
per-result OCR confirm page but never persisted. This column lets
the per-result confirm form save it onto the result row so the
race-detail card can render the overall rank badge (G..SS+..Ug⁶,
computed via the published threshold table; see
``services/stat_ranks.rank_points_index``).

Hand-written direct ``op.add_column`` — no ``batch_alter_table``.
``official_race_results`` is a CASCADE parent for
``official_race_result_skills``; the batch path would recreate it
and wipe the children per ``feedback_sqlite_migrations.md``.
Nullable so historical rows + manual entries leave it blank.
"""

import sqlalchemy as sa
from alembic import op

revision = "759398802e8b"
down_revision = "4dfda67e1c43"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "official_race_results",
        sa.Column("uma_score", sa.Integer(), nullable=True),
    )


def downgrade():
    op.drop_column("official_race_results", "uma_score")
