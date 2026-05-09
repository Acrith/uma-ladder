"""user_profiles: add club_id (PR-L1)

Persists the user's uma.moe `circle_id` on their UserProfile so
official-race visibility checks for Club-only matches don't have
to traverse the uma_moe_cache JSON blob on every render.

Mirror is kept in sync from `services.profiles.sync_club_id_from_trainer`,
called whenever a fresh `TrainerSummary` is fetched on profile views.
Stale by up to 12h (the cache TTL) — acceptable for a feature where
the worst case is "match invitation arrives a day late."

Plain `op.add_column` per PR-J10 lesson — `user_profiles` is the
parent of CASCADE FKs (it's actually the child of users, but
`batch_alter_table` on a child of CASCADE is also dangerous on
SQLite when FK pragma is on). No batch dance, no surprises.

Revision ID: dbe2218558bd
Revises: 9454ca774688
Create Date: 2026-05-09 14:30:00.000000
"""
import sqlalchemy as sa
from alembic import op


revision = "dbe2218558bd"
down_revision = "9454ca774688"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "user_profiles",
        sa.Column("club_id", sa.Integer(), nullable=True),
    )


def downgrade():
    op.drop_column("user_profiles", "club_id")
