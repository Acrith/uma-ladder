"""create uploaded_images and ocr_parse_attempts

Revision ID: 7ccc066dea1a
Revises: 54e34e41ab47
Create Date: 2026-05-04 19:49:39.244862

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "7ccc066dea1a"
down_revision = "54e34e41ab47"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "uploaded_images",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uploader_user_id", sa.Integer(), nullable=True),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=True),
        sa.Column("mime_type", sa.String(length=64), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["uploader_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_table(
        "ocr_parse_attempts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uploaded_image_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("parsed_json", sa.JSON(), nullable=True),
        sa.Column("confidence_json", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("confirmed_by_user_id", sa.Integer(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["confirmed_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_image_id"], ["uploaded_images.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("ocr_parse_attempts", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_ocr_parse_attempts_status"), ["status"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_ocr_parse_attempts_uploaded_image_id"),
            ["uploaded_image_id"],
            unique=False,
        )


def downgrade():
    with op.batch_alter_table("ocr_parse_attempts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_ocr_parse_attempts_uploaded_image_id"))
        batch_op.drop_index(batch_op.f("ix_ocr_parse_attempts_status"))
    op.drop_table("ocr_parse_attempts")
    op.drop_table("uploaded_images")
