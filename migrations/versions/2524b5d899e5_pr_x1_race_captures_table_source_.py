"""PR-X1: race_captures table (source-agnostic race data ingest)

Revision ID: 2524b5d899e5
Revises: be39c5ad135d
Create Date: 2026-08-04 13:40:35.356850

Hand-trimmed from the autogenerate output. Alembic also wanted to
rewrite foreign keys on `official_races`, `draft_matches`,
`draft_match_bans` and `draft_race_results` (pre-existing naming drift
between the models and the deployed schema — not something this change
introduces). Those `batch_alter_table` calls rebuild the table in
SQLite, which drops the rows of anything hanging off a CASCADE foreign
key: that is what wiped production in PR-J10. This migration therefore
creates the new table and nothing else. The drift is real but has to
be addressed deliberately, with a snapshot, on its own.
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "2524b5d899e5"
down_revision = "be39c5ad135d"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "race_captures",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("submitted_by_user_id", sa.Integer(), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("saved_room_id", sa.Integer(), nullable=True),
        sa.Column("race_instance_id", sa.Integer(), nullable=True),
        sa.Column("room_name", sa.String(length=128), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("participant_count", sa.Integer(), nullable=True),
        sa.Column("official_race_id", sa.Integer(), nullable=True),
        sa.Column("draft_match_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("confirmed_by_user_id", sa.Integer(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["confirmed_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["draft_match_id"], ["draft_matches.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["official_race_id"], ["official_races.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["submitted_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Plain create_index — the table was just created and nothing points
    # at it, so there is no reason to reach for batch_alter_table here.
    op.create_index(
        "ix_race_captures_draft_match_id",
        "race_captures",
        ["draft_match_id"],
        unique=False,
    )
    op.create_index(
        "ix_race_captures_official_race_id",
        "race_captures",
        ["official_race_id"],
        unique=False,
    )
    # Unique: saved_room_id is the idempotency key for a room result that
    # several participants may each upload. NULLs stay distinct in both
    # SQLite and Postgres, so non-room captures are unconstrained.
    op.create_index(
        "ix_race_captures_saved_room_id",
        "race_captures",
        ["saved_room_id"],
        unique=True,
    )
    op.create_index(
        "ix_race_captures_status", "race_captures", ["status"], unique=False
    )
    op.create_index(
        "ix_race_captures_submitted_by_user_id",
        "race_captures",
        ["submitted_by_user_id"],
        unique=False,
    )


def downgrade():
    op.drop_index("ix_race_captures_submitted_by_user_id", table_name="race_captures")
    op.drop_index("ix_race_captures_status", table_name="race_captures")
    op.drop_index("ix_race_captures_saved_room_id", table_name="race_captures")
    op.drop_index("ix_race_captures_official_race_id", table_name="race_captures")
    op.drop_index("ix_race_captures_draft_match_id", table_name="race_captures")
    op.drop_table("race_captures")
