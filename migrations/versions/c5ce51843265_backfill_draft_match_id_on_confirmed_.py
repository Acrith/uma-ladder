"""backfill draft_match_id on confirmed ocr attempts (PR-J4 follow-up)

PR-J4 added draft_match_id but only stamped it on NEW confirmations.
Existing completed matches had OCR attempts with the column null, so
the new completed-card screenshot strip rendered empty for them.

The heuristic + commit is shared with the `ocr-backfill-draft-links`
CLI so admins can re-run if data drifts (e.g. a delayed import).

Revision ID: c5ce51843265
Revises: c5a8733b5f04
Create Date: 2026-05-07 16:41:57.952025

"""

revision = "c5ce51843265"
down_revision = "c5a8733b5f04"
branch_labels = None
depends_on = None


def upgrade():
    # Flask-Migrate runs inside an app context, so the service layer
    # is reachable. Self-contained raw SQL would be more "portable"
    # for an Alembic purist but the columns we touch are stable and
    # the helper has unit-test coverage.
    from uma_ladder.services import ocr as ocr_service

    ocr_service.backfill_draft_match_links()


def downgrade():
    # Backfill is lossy to reverse — clearing the column would also
    # wipe links from any post-J4 confirmations that landed in the
    # same range. Leave as no-op; the schema downgrade in
    # c5a8733b5f04 drops the column entirely.
    pass
