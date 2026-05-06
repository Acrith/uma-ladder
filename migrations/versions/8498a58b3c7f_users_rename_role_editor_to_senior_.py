"""users: rename role editor to senior_organizer

Revision ID: 8498a58b3c7f
Revises: 79e56ce92cf0
Create Date: 2026-05-06 09:35:16.004059

PR-G4 — the `editor` role string never reflected what the role
actually does (organizer-plus, can cancel races + matches). Rename
it to `senior_organizer`. Pure data rewrite — schema, rank order,
and FKs all unchanged.
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = '8498a58b3c7f'
down_revision = '79e56ce92cf0'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE users SET role = 'senior_organizer' WHERE role = 'editor'")


def downgrade():
    op.execute("UPDATE users SET role = 'editor' WHERE role = 'senior_organizer'")
