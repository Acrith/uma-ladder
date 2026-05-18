"""PR-A5: row-parser fields on official_race_results

Revision ID: 4dfda67e1c43
Revises: 8fe66133a51a
Create Date: 2026-05-18

Surfaces three of the in-game-result-screen fields the OCR row
parser (services/ocr_google_vision._parse_placement_row_fields)
has been extracting all along but discarding at save time:

- ``finish_time_or_lengths`` (string up to 32 chars) — the winner's
  finish time ("3:43.8") for placement 1, or the gap to the winner
  for everyone else ("1/2 L", "3 1/2 L", "Nose", "Distance").
- ``gate`` (small int) — starting gate number 1..18.
- ``fav_rank`` (small int) — pre-race favorite number, 1..N.

All nullable so historical rows (without OCR enrichment) survive
the migration without backfill.

Hand-written direct ``op.add_column`` — no ``batch_alter_table``.
``official_race_results`` is a CASCADE parent for
``official_race_result_skills``; per `feedback_sqlite_migrations.md`,
recreating it via batch wipes the children. SQLite supports
``ALTER TABLE ADD COLUMN`` natively, so direct ops are safe for new
nullable columns.
"""

import sqlalchemy as sa
from alembic import op

revision = "4dfda67e1c43"
down_revision = "8fe66133a51a"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "official_race_results",
        sa.Column(
            "finish_time_or_lengths", sa.String(length=32), nullable=True
        ),
    )
    op.add_column(
        "official_race_results",
        sa.Column("gate", sa.Integer(), nullable=True),
    )
    op.add_column(
        "official_race_results",
        sa.Column("fav_rank", sa.Integer(), nullable=True),
    )


def downgrade():
    op.drop_column("official_race_results", "fav_rank")
    op.drop_column("official_race_results", "gate")
    op.drop_column("official_race_results", "finish_time_or_lengths")
