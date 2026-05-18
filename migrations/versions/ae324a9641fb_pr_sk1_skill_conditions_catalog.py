"""PR-SK1: skill_conditions catalog table

Scaffolds the table consumed by item 5 (gray-out non-applicable
skills) and item 6 (apply green-skill stat buffs to displayed
results). One row per UmaSkill, unique on skill_id.

Predicate columns are nullable — NULL means "skill doesn't care
about this dimension". For a skill to "apply" to a race, every
non-NULL predicate must match the race context. Buff columns are
signed ints so × tier variants (debuffs) store as negative values.

Created table only, no FK on a CASCADE parent — `op.create_table`
is fine here.

Revision ID: ae324a9641fb
Revises: 759398802e8b
Create Date: 2026-05-19 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "ae324a9641fb"
down_revision = "759398802e8b"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "skill_conditions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "skill_id",
            sa.Integer(),
            sa.ForeignKey("uma_skills.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        # ─── Race-context predicates ────────────────────────────
        sa.Column("direction", sa.String(length=8), nullable=True),
        sa.Column("surface", sa.String(length=8), nullable=True),
        sa.Column("weather", sa.String(length=8), nullable=True),
        sa.Column("season", sa.String(length=8), nullable=True),
        sa.Column("distance_category", sa.String(length=8), nullable=True),
        sa.Column("strategy", sa.String(length=8), nullable=True),
        sa.Column("venue", sa.String(length=32), nullable=True),
        sa.Column("is_standard_distance", sa.Boolean(), nullable=True),
        # ─── Stat buffs when predicates match ───────────────────
        sa.Column("buff_speed", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("buff_stamina", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("buff_power", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("buff_guts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("buff_wisdom", sa.Integer(), nullable=False, server_default=sa.text("0")),
        # ─── Catalog metadata ───────────────────────────────────
        sa.Column(
            "is_dynamic",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("notes", sa.String(length=256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("skill_conditions")
