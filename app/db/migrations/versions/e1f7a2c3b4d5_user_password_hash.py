"""user password hash

Revision ID: e1f7a2c3b4d5
Revises: 92cad3ca91ff
Create Date: 2026-11-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel  # noqa: F401


# revision identifiers, used by Alembic.
revision: str = 'e1f7a2c3b4d5'
down_revision: Union[str, Sequence[str], None] = '92cad3ca91ff'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('password_hash', sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True)
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('password_hash')
