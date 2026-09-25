"""creative_angles.observations: verified, quoted evidence behind each angle

Revision ID: b7c1e2d4a5f6
Revises: 9010f33c1603
Create Date: 2026-09-25 18:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b7c1e2d4a5f6'
down_revision: Union[str, Sequence[str], None] = '9010f33c1603'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'creative_angles',
        sa.Column(
            'observations',
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('creative_angles', 'observations')
