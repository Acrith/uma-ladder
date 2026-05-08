"""official_races visibility + invitees (PR-J13)

Adds:
- `official_races.visibility` column (`public` default; `private`
  + `club` reserved for current and future targeted-match work).
- New `official_race_invitees` table — explicit allowlist for
  Private races. Presence of a row gates both view and registration.

Following PR-J10 lesson: NO `batch_alter_table` on
`official_races`. Plain `op.add_column` is enough for adding a
nullable / DEFAULTed column on SQLite, and avoids the recreate-
dance that would CASCADE-wipe child tables (registrations,
results, etc.) if FKs were on at the wrong moment.

Revision ID: 4514fcfa99f8
Revises: 2aaec748316c
Create Date: 2026-05-08 23:16:08.266065

"""
import sqlalchemy as sa
from alembic import op


revision = "4514fcfa99f8"
down_revision = "2aaec748316c"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "official_race_invitees",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("official_race_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("invited_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["invited_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["official_race_id"], ["official_races.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "official_race_id",
            "user_id",
            name="uq_official_race_invitees_race_user",
        ),
    )
    with op.batch_alter_table(
        "official_race_invitees", schema=None
    ) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_official_race_invitees_official_race_id"),
            ["official_race_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_official_race_invitees_user_id"),
            ["user_id"],
            unique=False,
        )

    # PR-J10 lesson: do NOT wrap in batch_alter_table for plain
    # ADD COLUMN. SQLite supports it natively; the batch dance
    # would DROP TABLE official_races mid-flight and trigger
    # implicit DELETEs that CASCADE to registrations / results /
    # invitees / result_skills.
    op.add_column(
        "official_races",
        sa.Column(
            "visibility",
            sa.String(length=16),
            server_default="public",
            nullable=False,
        ),
    )
    op.create_index(
        op.f("ix_official_races_visibility"),
        "official_races",
        ["visibility"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        op.f("ix_official_races_visibility"), table_name="official_races"
    )
    op.drop_column("official_races", "visibility")

    with op.batch_alter_table(
        "official_race_invitees", schema=None
    ) as batch_op:
        batch_op.drop_index(batch_op.f("ix_official_race_invitees_user_id"))
        batch_op.drop_index(
            batch_op.f("ix_official_race_invitees_official_race_id")
        )
    op.drop_table("official_race_invitees")
