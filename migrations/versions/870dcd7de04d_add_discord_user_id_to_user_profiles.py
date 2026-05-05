"""add discord_user_id to user_profiles

Revision ID: 870dcd7de04d
Revises: 11146ca0c232
Create Date: 2026-05-05 19:22:29.804786

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '870dcd7de04d'
down_revision = '11146ca0c232'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user_profiles', schema=None) as batch_op:
        batch_op.add_column(sa.Column('discord_user_id', sa.String(length=32), nullable=True))


def downgrade():
    with op.batch_alter_table('user_profiles', schema=None) as batch_op:
        batch_op.drop_column('discord_user_id')
