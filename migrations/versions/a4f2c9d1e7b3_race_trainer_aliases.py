"""race_trainer_aliases — remember trainer name -> ladder account

Revision ID: a4f2c9d1e7b3
Revises: 8b21d4e0af57
Create Date: 2026-09-07

Hand-written, CREATE TABLE only. Autogenerate wants to emit
`batch_alter_table` against pre-existing tables to reconcile unrelated
model/schema drift, and on SQLite that rebuilds the table — which is
exactly how PR-J10 wiped production. A new table needs none of that.
"""
from alembic import op
import sqlalchemy as sa

revision = "a4f2c9d1e7b3"
down_revision = "8b21d4e0af57"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "race_trainer_aliases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("trainer_name", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_race_trainer_aliases_trainer_name",
        "race_trainer_aliases",
        ["trainer_name"],
        unique=True,
    )
    op.create_index(
        "ix_race_trainer_aliases_user_id", "race_trainer_aliases", ["user_id"]
    )


def downgrade():
    op.drop_index("ix_race_trainer_aliases_user_id", table_name="race_trainer_aliases")
    op.drop_index(
        "ix_race_trainer_aliases_trainer_name", table_name="race_trainer_aliases"
    )
    op.drop_table("race_trainer_aliases")
