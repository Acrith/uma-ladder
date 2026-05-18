"""PR-A1: aptitudes column on official_race_results

Revision ID: 8fe66133a51a
Revises: e86f38b110bb
Create Date: 2026-05-18

Adds a single JSON column ``aptitudes`` to ``official_race_results``
so the per-result OCR confirm flow can persist the track / distance
/ style aptitude grades the sheet extractor already parses.

Stored shape mirrors the extractor output exactly::

    {
        "track":    {"turf": "A", "dirt": "F"},
        "distance": {"sprint": "G", "mile": "B",
                     "medium": "A", "long": "A"},
        "style":    {"front": "G", "pace": "A",
                     "late": "A", "end": "A"}
    }

Hand-written direct ``op.add_column`` — no ``batch_alter_table``.
``official_race_results`` is a CASCADE parent for
``official_race_result_skills``; per `feedback_sqlite_migrations.md`,
recreating it via batch would wipe the skills children. SQLite
supports ``ALTER TABLE ADD COLUMN`` natively, so the direct path is
safe.

Column is nullable so existing rows survive the migration without
backfill (they just won't have aptitude data — the per-result page
shows em-dashes for missing slots).
"""

import sqlalchemy as sa
from alembic import op

revision = "8fe66133a51a"
down_revision = "e86f38b110bb"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "official_race_results",
        sa.Column("aptitudes", sa.JSON(), nullable=True),
    )


def downgrade():
    op.drop_column("official_race_results", "aptitudes")
