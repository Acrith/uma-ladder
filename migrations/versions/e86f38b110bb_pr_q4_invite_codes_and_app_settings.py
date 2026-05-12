"""PR-Q4: invite codes + app_settings (anti-spam signup gate)

Revision ID: e86f38b110bb
Revises: 44732994ca47
Create Date: 2026-05-12

Adds three tables for the invite-only signup gate:

- `app_settings` — generic key/value store for runtime feature flags.
  We're seeding it with one row: `invite_only_enabled = false`
  so deploying this migration changes nothing until an admin flips
  the toggle in the UI.
- `invite_codes` — the codes themselves. `max_uses` NULL means
  unlimited; `expires_at` and `revoked_at` are independent reasons
  a code can stop working.
- `invite_code_uses` — claim audit trail. One row per registration
  through a code. `user_id` is SET NULL so we don't lose audit
  rows when the claimant is later hard-deleted.

Hand-written (not autogen) to stay clear of the `users` CASCADE
parent — same rule as PR-Q3a / PR-Q3b. New tables, clean
`op.create_table` only.
"""

from alembic import op
import sqlalchemy as sa


revision = "e86f38b110bb"
down_revision = "44732994ca47"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("updated_by_user_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("key"),
    )

    op.create_table(
        "invite_codes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=True),
        # NULL = unlimited uses until revoked / expired.
        sa.Column("max_uses", sa.Integer(), nullable=True),
        sa.Column("uses_count", sa.Integer(), nullable=False),
        sa.Column(
            "expires_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "revoked_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_invite_codes_code"),
    )
    op.create_index(
        op.f("ix_invite_codes_created_at"),
        "invite_codes",
        ["created_at"],
        unique=False,
    )

    op.create_table(
        "invite_code_uses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("invite_code_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column(
            "claimed_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("claimed_from_ip", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["invite_code_id"], ["invite_codes.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_invite_code_uses_invite_code_id"),
        "invite_code_uses",
        ["invite_code_id"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        op.f("ix_invite_code_uses_invite_code_id"),
        table_name="invite_code_uses",
    )
    op.drop_table("invite_code_uses")
    op.drop_index(
        op.f("ix_invite_codes_created_at"), table_name="invite_codes"
    )
    op.drop_table("invite_codes")
    op.drop_table("app_settings")
