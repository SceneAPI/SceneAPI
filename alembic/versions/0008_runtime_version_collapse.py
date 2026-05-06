"""phase 8 collapse runtime_version columns to a single backend-defined string

sfmapi ships no concrete backend — engine-specific fingerprint columns
(``colmap_sha``, ``baxx_sha``, ``cudss_ver``, ``cuda_arch``,
``sam_model_sha``) are replaced with a single freeform
``runtime_version_id`` that the registered backend computes. Pre-
release: no in-flight production data, so we drop+recreate the table.

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-06

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("runtime_version")
    op.create_table(
        "runtime_version",
        sa.Column("rv_id", sa.CHAR(26), nullable=False),
        sa.Column("runtime_version_id", sa.String(128), nullable=False),
        sa.Column("seed", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("rv_id", name="pk_runtime_version"),
        sa.UniqueConstraint(
            "runtime_version_id",
            "seed",
            name="uq_runtime_version_tuple",
        ),
    )


def downgrade() -> None:
    op.drop_table("runtime_version")
    op.create_table(
        "runtime_version",
        sa.Column("rv_id", sa.CHAR(26), nullable=False),
        sa.Column("colmap_sha", sa.String(64), nullable=False),
        sa.Column("baxx_sha", sa.String(64), nullable=False),
        sa.Column("cudss_ver", sa.String(64), nullable=False),
        sa.Column("cuda_arch", sa.String(32), nullable=False),
        sa.Column("sam_model_sha", sa.String(64), nullable=False),
        sa.Column("seed", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("rv_id", name="pk_runtime_version"),
        sa.UniqueConstraint(
            "colmap_sha",
            "baxx_sha",
            "cudss_ver",
            "cuda_arch",
            "sam_model_sha",
            "seed",
            name="uq_runtime_version_tuple",
        ),
    )
