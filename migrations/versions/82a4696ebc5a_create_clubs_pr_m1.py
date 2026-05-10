"""create clubs (PR-M1)

Promotes uma.moe `circle_id` to a first-class `clubs` table backing
per-club surfaces (member roster on `/clubs/<id>`, future per-club
ladder, future multi-club race allowlist).

Primary key IS uma.moe's `circle_id` — already globally unique;
using it directly avoids a synthetic id that would complicate
JOINs from `UserProfile.club_id` and the future race-allowlist FK.

`UserProfile.club_id` (PR-L1) deliberately stays as a bare integer
with no FK constraint: it's a snapshot from the uma.moe trainer
cache, the authoritative state lives upstream, and a user whose
profile sync precedes the corresponding club sync still has a
sensible mirror even if we haven't ingested the Club row yet.

Plain `op.create_table` — green-field, no parent mutations, no
batch_alter_table dance. Per the J10 lesson, that's the safe
pattern under SQLite + CASCADE FKs.

Revision ID: 82a4696ebc5a
Revises: dbe2218558bd
Create Date: 2026-05-10 12:00:00.000000
"""
import sqlalchemy as sa
from alembic import op


revision = "82a4696ebc5a"
down_revision = "dbe2218558bd"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "clubs",
        sa.Column("circle_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=True),
        sa.Column(
            "cached_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("member_count", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("circle_id", name="pk_clubs"),
    )


def downgrade():
    op.drop_table("clubs")
