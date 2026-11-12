"""login sessions

Revision ID: f2a8b3c4d5e6
Revises: e1f7a2c3b4d5
Create Date: 2026-11-12 01:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel  # noqa: F401


# revision identifiers, used by Alembic.
revision: str = 'f2a8b3c4d5e6'
down_revision: Union[str, Sequence[str], None] = 'e1f7a2c3b4d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('login_sessions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('token_hash', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('created_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
    sa.Column('expires_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('login_sessions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_login_sessions_token_hash'), ['token_hash'], unique=True)
        batch_op.create_index(batch_op.f('ix_login_sessions_user_id'), ['user_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('login_sessions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_login_sessions_user_id'))
        batch_op.drop_index(batch_op.f('ix_login_sessions_token_hash'))

    op.drop_table('login_sessions')
