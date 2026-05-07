"""add draft_match_id to ocr_parse_attempts (PR-J4)

Links a confirmed OCR parse to the draft match whose results it
seeded so the completed-match card can surface the source
screenshots and so opponents (not just the uploader) can view them.

Revision ID: c5a8733b5f04
Revises: a834124a30e5
Create Date: 2026-05-07 16:25:35.178547

"""
import sqlalchemy as sa
from alembic import op


revision = "c5a8733b5f04"
down_revision = "a834124a30e5"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("ocr_parse_attempts", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("draft_match_id", sa.Integer(), nullable=True)
        )
        batch_op.create_index(
            batch_op.f("ix_ocr_parse_attempts_draft_match_id"),
            ["draft_match_id"],
            unique=False,
        )
        batch_op.create_foreign_key(
            "fk_ocr_parse_attempts_draft_match_id",
            "draft_matches",
            ["draft_match_id"],
            ["id"],
            ondelete="CASCADE",
        )


def downgrade():
    with op.batch_alter_table("ocr_parse_attempts", schema=None) as batch_op:
        batch_op.drop_constraint(
            "fk_ocr_parse_attempts_draft_match_id", type_="foreignkey"
        )
        batch_op.drop_index(
            batch_op.f("ix_ocr_parse_attempts_draft_match_id")
        )
        batch_op.drop_column("draft_match_id")
