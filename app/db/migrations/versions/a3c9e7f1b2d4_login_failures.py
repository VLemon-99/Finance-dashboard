"""login failures

Revision ID: a3c9e7f1b2d4
Revises: f2a8b3c4d5e6
Create Date: 2026-11-12 02:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel  # noqa: F401


# revision identifiers, used by Alembic.
revision: str = 'a3c9e7f1b2d4'
down_revision: Union[str, Sequence[str], None] = 'f2a8b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('login_failures',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('email', sqlmodel.sql.sqltypes.AutoString(length=320), nullable=False),
    sa.Column('failed_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('login_failures', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_login_failures_email'), ['email'], unique=False)
        batch_op.create_index(batch_op.f('ix_login_failures_failed_at'), ['failed_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('login_failures', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_login_failures_failed_at'))
        batch_op.drop_index(batch_op.f('ix_login_failures_email'))

    op.drop_table('login_failures')
