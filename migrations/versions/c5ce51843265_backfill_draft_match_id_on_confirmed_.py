"""backfill draft_match_id on confirmed ocr attempts (PR-J4 follow-up)

PR-J4 added draft_match_id but only stamped it on NEW confirmations.
Existing completed matches had OCR attempts with the column null, so
the new completed-card screenshot strip rendered empty for them.

Revision ID: c5ce51843265
Revises: c5a8733b5f04
Create Date: 2026-05-07 16:41:57.952025

"""

from datetime import timedelta

import sqlalchemy as sa
from alembic import op

revision = "c5ce51843265"
down_revision = "c5a8733b5f04"
branch_labels = None
depends_on = None

_WINDOW = timedelta(seconds=600)


def upgrade():
    """Raw SQL on purpose — do NOT call the service layer from here.

    This originally delegated to `ocr_service.backfill_draft_match_links`.
    That reads through the ORM, and the ORM describes the models at
    HEAD: its SELECT listed `users.failed_login_count`, `disabled_at`
    and friends, which later migrations add. Running the chain against
    a fresh database therefore died here with "no such column:
    users_1.failed_login_count" — so a new deploy, a restore onto an
    empty volume, or a Postgres cutover could never be built from
    migrations, only inherited from a database that predated those
    columns.

    Raw SQL pins this step to the schema as it exists at THIS revision.
    The service-layer version stays for the `ocr-backfill-draft-links`
    CLI, where running against HEAD models is correct.
    """
    conn = op.get_bind()
    attempts = conn.execute(
        sa.text(
            """
            SELECT id, confirmed_by_user_id, confirmed_at
            FROM ocr_parse_attempts
            WHERE status = 'confirmed'
              AND draft_match_id IS NULL
              AND confirmed_by_user_id IS NOT NULL
              AND confirmed_at IS NOT NULL
            """
        )
    ).fetchall()

    for attempt_id, user_id, confirmed_at in attempts:
        if confirmed_at is None:
            continue
        candidates = conn.execute(
            sa.text(
                """
                SELECT id, completed_at
                FROM draft_matches
                WHERE status = 'completed'
                  AND completed_at IS NOT NULL
                  AND (host_user_id = :uid OR opponent_user_id = :uid)
                  AND completed_at >= :lo
                  AND completed_at <= :hi
                """
            ),
            {
                "uid": user_id,
                "lo": confirmed_at - _WINDOW,
                "hi": confirmed_at + _WINDOW,
            },
        ).fetchall()
        if not candidates:
            continue
        # Closest completion to the confirmation wins.
        best_id = min(
            candidates, key=lambda row: abs(row[1] - confirmed_at)
        )[0]
        conn.execute(
            sa.text(
                "UPDATE ocr_parse_attempts SET draft_match_id = :mid "
                "WHERE id = :aid"
            ),
            {"mid": best_id, "aid": attempt_id},
        )


def downgrade():
    # Backfill is lossy to reverse — clearing the column would also
    # wipe links from any post-J4 confirmations that landed in the
    # same range. Leave as no-op; the schema downgrade in
    # c5a8733b5f04 drops the column entirely.
    pass
