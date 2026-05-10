"""achievements: emoji glyphs → icon keys (PR-P2.1)

PR-P2 seeded the catalogue with emoji glyphs in the `icon`
column. The user has standing instruction (see
`feedback_no_emojis_in_ui.md`) to use stroke SVG icons instead.
This migration:

1. UPDATEs each seed row to use a stable icon key string (e.g.
   "trophy" / "crown" / "star") that the template's
   ``achievement_icon`` resolver maps to a Lucide-style SVG
   macro at render time.
2. Leaves any user_achievements rows untouched — the icon
   column is on the catalogue table only; grants reference by
   FK and don't carry the icon.
3. Is idempotent on re-run: the WHERE clauses match by `key`,
   not by the previous icon value, so partial-deploy retries
   converge on the same end state.

Future achievements can be seeded directly with icon keys; the
emoji-to-key migration is a one-time cleanup.

Revision ID: 58f6e4ad7227
Revises: 6f86401b628f
Create Date: 2026-05-10 19:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "58f6e4ad7227"
down_revision = "6f86401b628f"
branch_labels = None
depends_on = None


# Mapping: achievement key → new icon key (must be one of the
# resolver branches in templates/_icons.html `achievement_icon`).
_KEY_TO_ICON: dict[str, str] = {
    "founding_member": "star",
    "link_discord": "link",
    "link_google": "link",
    "first_official_race": "flag",
    "first_official_podium": "award",
    "first_official_win": "trophy",
    "first_draft_match": "dice",
    "first_draft_win": "swords",
    "season_top_3": "award",
    "season_champion": "crown",
}


def upgrade():
    # Issue one UPDATE per row — the alternative (CASE expression
    # in a single UPDATE) reads worse and saves no time on a
    # 10-row table.
    bind = op.get_bind()
    for key, icon in _KEY_TO_ICON.items():
        bind.execute(
            sa.text(
                "UPDATE achievements SET icon = :icon WHERE key = :key"
            ),
            {"icon": icon, "key": key},
        )


def downgrade():
    # Restore the PR-P2 emoji glyphs. Kept symmetrical with
    # upgrade so a forced rollback can recover the prior state,
    # though we'd never actually want to.
    bind = op.get_bind()
    emoji_map = {
        "founding_member": "★",
        "link_discord": "🔗",
        "link_google": "🔗",
        "first_official_race": "🏁",
        "first_official_podium": "🥉",
        "first_official_win": "🥇",
        "first_draft_match": "🎲",
        "first_draft_win": "🥇",
        "season_top_3": "🏆",
        "season_champion": "👑",
    }
    for key, icon in emoji_map.items():
        bind.execute(
            sa.text(
                "UPDATE achievements SET icon = :icon WHERE key = :key"
            ),
            {"icon": icon, "key": key},
        )
