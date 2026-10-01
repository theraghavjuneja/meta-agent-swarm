"""creative_specs.typography_style: overlay type system chosen with the spec

Revision ID: c3d9f0a1b2e7
Revises: b7c1e2d4a5f6
Create Date: 2026-09-25 19:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c3d9f0a1b2e7'
down_revision: Union[str, Sequence[str], None] = 'b7c1e2d4a5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'creative_specs',
        sa.Column('typography_style', sa.String(length=32), server_default='modern_clean', nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('creative_specs', 'typography_style')
