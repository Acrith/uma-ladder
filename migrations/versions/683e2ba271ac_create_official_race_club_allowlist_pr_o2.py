"""create official_race_club_allowlist (PR-O2)

Promotes PR-L1's single-club CLUB visibility to a multi-club
allowlist for allied-club tournaments and cross-club friendlies.

The organizer's own club stays implicitly allowed (handled in the
visibility check, not via a row here). This table only tracks
*additional* clubs the organizer has explicitly added.

`club_circle_id` FKs the first-class `clubs` table from PR-M1 —
only clubs we already have a metadata row for can be added (the
service layer lazy-creates the Club row from uma.moe before
inserting). `added_by_user_id` is nullable + SET NULL so a
deleted user doesn't cascade-delete an allowlist row and silently
expand a race's audience.

Composite primary key (race_id, club_circle_id) prevents duplicate
allowlist entries without a separate UniqueConstraint.

Plain `op.create_table` per the J10 lesson — green-field, no
parent mutations, no batch_alter dance.

Revision ID: 683e2ba271ac
Revises: 82a4696ebc5a
Create Date: 2026-05-10 14:30:00.000000
"""
import sqlalchemy as sa
from alembic import op


revision = "683e2ba271ac"
down_revision = "82a4696ebc5a"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "official_race_club_allowlist",
        sa.Column("official_race_id", sa.Integer(), nullable=False),
        sa.Column("club_circle_id", sa.Integer(), nullable=False),
        sa.Column("added_by_user_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["official_race_id"],
            ["official_races.id"],
            name="fk_official_race_club_allowlist_race",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["club_circle_id"],
            ["clubs.circle_id"],
            name="fk_official_race_club_allowlist_club",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["added_by_user_id"],
            ["users.id"],
            name="fk_official_race_club_allowlist_added_by",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint(
            "official_race_id",
            "club_circle_id",
            name="pk_official_race_club_allowlist",
        ),
    )
    op.create_index(
        "ix_official_race_club_allowlist_official_race_id",
        "official_race_club_allowlist",
        ["official_race_id"],
    )
    op.create_index(
        "ix_official_race_club_allowlist_club_circle_id",
        "official_race_club_allowlist",
        ["club_circle_id"],
    )


def downgrade():
    op.drop_index(
        "ix_official_race_club_allowlist_club_circle_id",
        table_name="official_race_club_allowlist",
    )
    op.drop_index(
        "ix_official_race_club_allowlist_official_race_id",
        table_name="official_race_club_allowlist",
    )
    op.drop_table("official_race_club_allowlist")
