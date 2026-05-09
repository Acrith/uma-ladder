"""create auth_identities (PR-K1)

Provider-agnostic identity table backing OAuth login. One user can
have multiple identities (Discord today, Google later); each row
maps an external provider account to a local user.

Following PR-J10 lesson: this PR adds a NEW table only — no
batch_alter_table on `users`, no schema changes on parent tables
of CASCADE FKs. The single FK from auth_identities.user_id back to
users is plain ON DELETE CASCADE (delete the user, drop their
identities) and is created as part of the new table, not retrofit
onto an existing one.

Revision ID: 9454ca774688
Revises: 4514fcfa99f8
Create Date: 2026-05-09 12:00:00.000000

"""
import sqlalchemy as sa
from alembic import op


revision = "9454ca774688"
down_revision = "4514fcfa99f8"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "auth_identities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        # external_id = Discord snowflake / Google sub. Stored as
        # text so we never lose precision on 64-bit IDs.
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column("external_username", sa.String(length=128), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("avatar_url", sa.String(length=512), nullable=True),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider",
            "external_id",
            name="uq_auth_identities_provider_external_id",
        ),
    )
    with op.batch_alter_table(
        "auth_identities", schema=None
    ) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_auth_identities_user_id"),
            ["user_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_auth_identities_provider"),
            ["provider"],
            unique=False,
        )


def downgrade():
    with op.batch_alter_table(
        "auth_identities", schema=None
    ) as batch_op:
        batch_op.drop_index(batch_op.f("ix_auth_identities_provider"))
        batch_op.drop_index(batch_op.f("ix_auth_identities_user_id"))
    op.drop_table("auth_identities")
