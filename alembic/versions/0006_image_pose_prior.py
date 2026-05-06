"""phase 6 add `pose_prior_json` on image

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-02

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("image") as batch:
        batch.add_column(sa.Column("pose_prior_json", sa.JSON, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("image") as batch:
        batch.drop_column("pose_prior_json")
