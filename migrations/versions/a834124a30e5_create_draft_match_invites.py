"""create draft_match_invites

Revision ID: a834124a30e5
Revises: 8498a58b3c7f
Create Date: 2026-05-06 21:06:29.440304

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a834124a30e5'
down_revision = '8498a58b3c7f'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'draft_match_invites',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('draft_match_id', sa.Integer(), nullable=False),
        sa.Column('inviter_user_id', sa.Integer(), nullable=False),
        sa.Column('invitee_user_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('responded_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['draft_match_id'], ['draft_matches.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['inviter_user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['invitee_user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'draft_match_id',
            'invitee_user_id',
            name='uq_draft_match_invites_match_invitee',
        ),
    )
    with op.batch_alter_table('draft_match_invites', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_draft_match_invites_draft_match_id'),
            ['draft_match_id'], unique=False,
        )
        batch_op.create_index(
            batch_op.f('ix_draft_match_invites_inviter_user_id'),
            ['inviter_user_id'], unique=False,
        )
        batch_op.create_index(
            batch_op.f('ix_draft_match_invites_invitee_user_id'),
            ['invitee_user_id'], unique=False,
        )
        batch_op.create_index(
            batch_op.f('ix_draft_match_invites_status'),
            ['status'], unique=False,
        )


def downgrade():
    with op.batch_alter_table('draft_match_invites', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_draft_match_invites_status'))
        batch_op.drop_index(batch_op.f('ix_draft_match_invites_invitee_user_id'))
        batch_op.drop_index(batch_op.f('ix_draft_match_invites_inviter_user_id'))
        batch_op.drop_index(batch_op.f('ix_draft_match_invites_draft_match_id'))
    op.drop_table('draft_match_invites')
