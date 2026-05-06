"""phase 5 add `pinned` column on job

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-02

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("job") as batch:
        batch.add_column(sa.Column("pinned", sa.Boolean, nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("job") as batch:
        batch.drop_column("pinned")
