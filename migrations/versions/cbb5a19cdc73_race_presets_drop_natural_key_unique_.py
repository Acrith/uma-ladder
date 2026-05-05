"""race_presets: drop natural-key unique constraint

Revision ID: cbb5a19cdc73
Revises: a4ab51a8aeb9
Create Date: 2026-05-05 21:57:15.936220

PR-G2 — RacePreset becomes one-row-per-race instead of one-row-per-
track-configuration, so multiple G1s sharing a physical course (Tokyo
Yushun + Japanese Oaks + Japan Cup at Tokyo 2400m turf left) can each
have their own row. The unique constraint was silently dropping the
extras at seed time.

The replacement index is non-unique but keeps the same column ordering
so the random-preset filter (services/draft.filter_presets) still has
a fast lookup path.
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = 'cbb5a19cdc73'
down_revision = 'a4ab51a8aeb9'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('race_presets', schema=None) as batch_op:
        batch_op.drop_constraint(
            'uq_race_presets_natural_key', type_='unique'
        )
        batch_op.create_index(
            'ix_race_presets_track_config',
            ['venue', 'surface', 'distance_meters', 'direction', 'course_variant'],
            unique=False,
        )


def downgrade():
    with op.batch_alter_table('race_presets', schema=None) as batch_op:
        batch_op.drop_index('ix_race_presets_track_config')
        batch_op.create_unique_constraint(
            'uq_race_presets_natural_key',
            ['venue', 'surface', 'distance_meters', 'direction', 'course_variant'],
        )
