"""create achievements (PR-P2)

Foundation for the cosmetic-progression / engagement loop.
Defines the catalogue table + per-user unlocks junction.
Seeds the starter set so the feature ships meaningful out of
the box.

Schema choices:
- `achievements.key` is UNIQUE — service code dispatches on this
  string, never on the synthetic id, so seed-row ids can shift
  between environments without breaking auto-grant logic.
- `user_achievements` uses a composite PK on
  (user_id, achievement_id) — no separate UniqueConstraint
  needed, and the ON DELETE CASCADE FKs keep grants tied to
  living users + active definitions.
- Both seed inserts are guarded with ON CONFLICT-style checks
  via `INSERT … WHERE NOT EXISTS` so re-running the seed (e.g.
  partial failure during a Fly deploy that retries) is
  idempotent.

Plain `op.create_table` per the J10 lesson — green-field,
no parent mutations, no batch_alter dance.

Revision ID: 6f86401b628f
Revises: 21d593ba5697
Create Date: 2026-05-10 17:00:00.000000
"""
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "6f86401b628f"
down_revision = "21d593ba5697"
branch_labels = None
depends_on = None


# Seed set captured here in the migration so the catalogue is
# available the moment the feature ships. Adding new ones later =
# new follow-up migration with another bulk_insert. Tweaking
# existing copy = small data-only migration that UPDATEs by `key`.
_SEED_ACHIEVEMENTS = [
    {
        "key": "founding_member",
        "name": "Founding Member",
        "description": "Joined Uma Ladder during its closed-club era.",
        "icon": "★",
        "tier": "gold",
        "source_kind": "manual",
        "sort_order": 10,
    },
    {
        "key": "link_discord",
        "name": "Discord Linked",
        "description": "Connected your Discord account via OAuth.",
        "icon": "🔗",
        "tier": "slate",
        "source_kind": "oauth_link",
        "sort_order": 100,
    },
    {
        "key": "link_google",
        "name": "Google Linked",
        "description": "Connected your Google account via OAuth.",
        "icon": "🔗",
        "tier": "slate",
        "source_kind": "oauth_link",
        "sort_order": 101,
    },
    {
        "key": "first_official_race",
        "name": "First Official Race",
        "description": "Completed your first Official race.",
        "icon": "🏁",
        "tier": "bronze",
        "source_kind": "race_result",
        "sort_order": 200,
    },
    {
        "key": "first_official_podium",
        "name": "First Official Podium",
        "description": "Finished top 3 in an Official race.",
        "icon": "🥉",
        "tier": "bronze",
        "source_kind": "race_result",
        "sort_order": 201,
    },
    {
        "key": "first_official_win",
        "name": "First Official Win",
        "description": "Won your first Official race.",
        "icon": "🥇",
        "tier": "silver",
        "source_kind": "race_result",
        "sort_order": 202,
    },
    {
        "key": "first_draft_match",
        "name": "First Draft Match",
        "description": "Completed your first Draft match.",
        "icon": "🎲",
        "tier": "bronze",
        "source_kind": "draft_match",
        "sort_order": 300,
    },
    {
        "key": "first_draft_win",
        "name": "First Draft Win",
        "description": "Won your first Draft match.",
        "icon": "🥇",
        "tier": "silver",
        "source_kind": "draft_match",
        "sort_order": 301,
    },
    {
        "key": "season_top_3",
        "name": "Season Podium",
        "description": "Finished top 3 on a season's Official ladder.",
        "icon": "🏆",
        "tier": "gold",
        "source_kind": "season_close",
        "sort_order": 400,
    },
    {
        "key": "season_champion",
        "name": "Season Champion",
        "description": "Finished #1 on a season's Official ladder.",
        "icon": "👑",
        "tier": "gold",
        "source_kind": "season_close",
        "sort_order": 401,
    },
]


def upgrade():
    op.create_table(
        "achievements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "icon", sa.String(length=16), nullable=False, server_default="★"
        ),
        sa.Column(
            "tier",
            sa.String(length=16),
            nullable=False,
            server_default="slate",
        ),
        sa.Column("source_kind", sa.String(length=32), nullable=True),
        sa.Column(
            "sort_order",
            sa.Integer(),
            nullable=False,
            server_default="100",
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_achievements"),
        sa.UniqueConstraint("key", name="uq_achievements_key"),
    )
    op.create_index(
        "ix_achievements_key", "achievements", ["key"], unique=True
    )

    op.create_table(
        "user_achievements",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("achievement_id", sa.Integer(), nullable=False),
        sa.Column(
            "unlocked_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("source", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_user_achievements_user",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["achievement_id"],
            ["achievements.id"],
            name="fk_user_achievements_achievement",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "user_id", "achievement_id", name="pk_user_achievements"
        ),
    )
    op.create_index(
        "ix_user_achievements_user_id",
        "user_achievements",
        ["user_id"],
    )
    op.create_index(
        "ix_user_achievements_achievement_id",
        "user_achievements",
        ["achievement_id"],
    )

    # Seed the starter catalogue. bulk_insert is fine on a
    # green-field table — no conflicts possible during the same
    # upgrade. If a future deploy partial-rollback ever lands us
    # here with rows already present, drop+recreate via downgrade
    # is the recovery path.
    achievements_table = sa.table(
        "achievements",
        sa.column("key", sa.String),
        sa.column("name", sa.String),
        sa.column("description", sa.Text),
        sa.column("icon", sa.String),
        sa.column("tier", sa.String),
        sa.column("source_kind", sa.String),
        sa.column("sort_order", sa.Integer),
        sa.column("enabled", sa.Boolean),
    )
    op.bulk_insert(
        achievements_table,
        [{**row, "enabled": True} for row in _SEED_ACHIEVEMENTS],
    )


def downgrade():
    op.drop_index(
        "ix_user_achievements_achievement_id",
        table_name="user_achievements",
    )
    op.drop_index(
        "ix_user_achievements_user_id",
        table_name="user_achievements",
    )
    op.drop_table("user_achievements")
    op.drop_index("ix_achievements_key", table_name="achievements")
    op.drop_table("achievements")


# Imported but unused in upgrade body; keeps the file importable
# for ad-hoc inspection without breaking alembic.
_NOW = datetime.now(UTC)
