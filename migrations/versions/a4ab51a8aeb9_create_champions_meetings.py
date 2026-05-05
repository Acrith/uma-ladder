"""create champions_meetings

Revision ID: a4ab51a8aeb9
Revises: 870dcd7de04d
Create Date: 2026-05-05 21:30:34.426111

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a4ab51a8aeb9'
down_revision = '870dcd7de04d'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'champions_meetings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('starts_on', sa.Date(), nullable=False),
        sa.Column('ends_on', sa.Date(), nullable=True),
        sa.Column('preset_id', sa.Integer(), nullable=False),
        sa.Column('override_venue', sa.String(length=32), nullable=True),
        sa.Column('override_surface', sa.String(length=8), nullable=True),
        sa.Column('override_distance_meters', sa.Integer(), nullable=True),
        sa.Column('override_distance_category', sa.String(length=8), nullable=True),
        sa.Column('override_direction', sa.String(length=16), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('source_url', sa.String(length=512), nullable=True),
        sa.Column('created_by_user_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['preset_id'], ['race_presets.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('champions_meetings', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_champions_meetings_preset_id'), ['preset_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_champions_meetings_starts_on'), ['starts_on'], unique=False)


def downgrade():
    with op.batch_alter_table('champions_meetings', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_champions_meetings_starts_on'))
        batch_op.drop_index(batch_op.f('ix_champions_meetings_preset_id'))
    op.drop_table('champions_meetings')
