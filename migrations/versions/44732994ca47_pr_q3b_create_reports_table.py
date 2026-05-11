"""PR-Q3b: create reports table

Revision ID: 44732994ca47
Revises: d3c716239547
Create Date: 2026-05-11 21:10:22.550305

User-on-user moderation reports. The autogenerate pass detected a
pile of FK/index naming drift on other tables — same harmless
cosmetic churn we stripped from PR-Q3a's migration. Keeping only
the actual change (new table + indexes) avoids batch_alter_table
on the CASCADE-parent `users` table (the rule from
`feedback_sqlite_migrations.md`: never recreate a CASCADE parent,
it wipes children).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '44732994ca47'
down_revision = 'd3c716239547'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "reports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("reporter_user_id", sa.Integer(), nullable=False),
        sa.Column("reported_user_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("context", sa.String(length=256), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("resolved_by_user_id", sa.Integer(), nullable=True),
        sa.Column(
            "resolved_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("resolution_notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["reported_user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["reporter_user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_reports_created_at"), "reports", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_reports_reported_user_id"),
        "reports",
        ["reported_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_reports_reporter_user_id"),
        "reports",
        ["reporter_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_reports_status"), "reports", ["status"], unique=False
    )


def downgrade():
    op.drop_index(op.f("ix_reports_status"), table_name="reports")
    op.drop_index(
        op.f("ix_reports_reporter_user_id"), table_name="reports"
    )
    op.drop_index(
        op.f("ix_reports_reported_user_id"), table_name="reports"
    )
    op.drop_index(op.f("ix_reports_created_at"), table_name="reports")
    op.drop_table("reports")
