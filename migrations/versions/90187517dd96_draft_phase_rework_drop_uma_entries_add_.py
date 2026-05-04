"""draft phase rework: drop uma entries, add character refs to bans/results

Revision ID: 90187517dd96
Revises: 6decd77fd2f3
Create Date: 2026-05-04 19:29:54.236655

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "90187517dd96"
down_revision = "6decd77fd2f3"
branch_labels = None
depends_on = None


def upgrade():
    """Rebuild draft_match_bans and draft_race_results without the FK to the
    dropped draft_match_uma_entries table, then drop that table.

    Uses the SQLite "table rebuild" pattern so it works even if the FK target
    is missing (e.g. a dev DB that lost the table during prior tinkering).
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # ---------- draft_match_bans rebuild ----------
    op.execute("PRAGMA foreign_keys = OFF")
    op.execute(
        """
        CREATE TABLE draft_match_bans_new (
            id INTEGER NOT NULL PRIMARY KEY,
            draft_match_id INTEGER NOT NULL REFERENCES draft_matches(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            ban_type VARCHAR(32) NOT NULL,
            uma_character_id INTEGER REFERENCES uma_characters(id) ON DELETE SET NULL,
            condition_key VARCHAR(64),
            locked_at DATETIME,
            created_at DATETIME NOT NULL
        )
        """
    )
    op.execute(
        """
        INSERT INTO draft_match_bans_new
            (id, draft_match_id, user_id, ban_type, uma_character_id, condition_key, locked_at, created_at)
        SELECT id, draft_match_id, user_id, ban_type, NULL, condition_key, locked_at, created_at
        FROM draft_match_bans
        """
    )
    op.execute("DROP TABLE draft_match_bans")
    op.execute("ALTER TABLE draft_match_bans_new RENAME TO draft_match_bans")
    op.execute(
        "CREATE INDEX ix_draft_match_bans_draft_match_id ON draft_match_bans (draft_match_id)"
    )
    op.execute("CREATE INDEX ix_draft_match_bans_user_id ON draft_match_bans (user_id)")

    # ---------- draft_race_results rebuild ----------
    op.execute(
        """
        CREATE TABLE draft_race_results_new (
            id INTEGER NOT NULL PRIMARY KEY,
            draft_match_id INTEGER NOT NULL REFERENCES draft_matches(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            uma_character_id INTEGER REFERENCES uma_characters(id) ON DELETE SET NULL,
            custom_uma_name VARCHAR(128),
            placement INTEGER NOT NULL,
            strategy VARCHAR(32),
            speed INTEGER,
            stamina INTEGER,
            power INTEGER,
            guts INTEGER,
            wisdom INTEGER,
            confirmed_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            created_at DATETIME NOT NULL
        )
        """
    )
    op.execute(
        """
        INSERT INTO draft_race_results_new
            (id, draft_match_id, user_id, uma_character_id, custom_uma_name,
             placement, strategy, speed, stamina, power, guts, wisdom,
             confirmed_by_user_id, created_at)
        SELECT id, draft_match_id, user_id, NULL, NULL,
               placement, strategy, speed, stamina, power, guts, wisdom,
               confirmed_by_user_id, created_at
        FROM draft_race_results
        """
    )
    op.execute("DROP TABLE draft_race_results")
    op.execute("ALTER TABLE draft_race_results_new RENAME TO draft_race_results")
    op.execute(
        "CREATE INDEX ix_draft_race_results_draft_match_id ON draft_race_results (draft_match_id)"
    )
    op.execute(
        "CREATE INDEX ix_draft_race_results_user_id ON draft_race_results (user_id)"
    )

    # ---------- drop the orphan table if still present ----------
    if "draft_match_uma_entries" in inspector.get_table_names():
        op.execute("DROP TABLE draft_match_uma_entries")

    # ---------- draft_matches: add ready flags ----------
    op.execute(
        "ALTER TABLE draft_matches ADD COLUMN host_ready BOOLEAN NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE draft_matches ADD COLUMN opponent_ready BOOLEAN NOT NULL DEFAULT 0"
    )

    op.execute("PRAGMA foreign_keys = ON")


def downgrade():
    """Best-effort downgrade: recreate the dropped table and reverse the columns.

    Rebuild is one-way for dev convenience; the downgrade leaves data on the
    floor for the renamed columns.
    """
    op.execute("PRAGMA foreign_keys = OFF")

    op.execute("ALTER TABLE draft_matches DROP COLUMN opponent_ready")
    op.execute("ALTER TABLE draft_matches DROP COLUMN host_ready")

    op.create_table(
        "draft_match_uma_entries",
        sa.Column("id", sa.INTEGER(), nullable=False),
        sa.Column("draft_match_id", sa.INTEGER(), nullable=False),
        sa.Column("user_id", sa.INTEGER(), nullable=False),
        sa.Column("uma_character_id", sa.INTEGER(), nullable=True),
        sa.Column("custom_uma_name", sa.VARCHAR(length=128), nullable=True),
        sa.Column("build_nickname", sa.VARCHAR(length=128), nullable=True),
        sa.Column("screenshot_url", sa.VARCHAR(length=512), nullable=True),
        sa.Column("notes", sa.TEXT(), nullable=True),
        sa.Column("is_banned", sa.BOOLEAN(), nullable=False),
        sa.Column("locked_at", sa.DATETIME(), nullable=True),
        sa.Column("created_at", sa.DATETIME(), nullable=False),
        sa.Column("updated_at", sa.DATETIME(), nullable=False),
        sa.ForeignKeyConstraint(
            ["draft_match_id"], ["draft_matches.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["uma_character_id"], ["uma_characters.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute("ALTER TABLE draft_race_results DROP COLUMN custom_uma_name")
    op.execute("ALTER TABLE draft_race_results DROP COLUMN uma_character_id")
    op.execute(
        "ALTER TABLE draft_race_results ADD COLUMN uma_entry_id INTEGER "
        "REFERENCES draft_match_uma_entries(id) ON DELETE SET NULL"
    )
    op.execute("ALTER TABLE draft_match_bans DROP COLUMN uma_character_id")
    op.execute(
        "ALTER TABLE draft_match_bans ADD COLUMN banned_uma_entry_id INTEGER "
        "REFERENCES draft_match_uma_entries(id) ON DELETE SET NULL"
    )

    op.execute("PRAGMA foreign_keys = ON")
