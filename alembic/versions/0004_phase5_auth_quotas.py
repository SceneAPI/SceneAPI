"""phase 5 api_keys, tenant_quotas, gpu_usage

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-01

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ID_LEN = 26


def upgrade() -> None:
    op.create_table(
        "api_key",
        sa.Column("api_key_id", sa.CHAR(ID_LEN), nullable=False),
        sa.Column("tenant_id", sa.String(ID_LEN), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("api_key_id", name="pk_api_key"),
        sa.UniqueConstraint("key_hash", name="uq_api_key_key_hash"),
    )
    op.create_index("ix_api_key_tenant_id", "api_key", ["tenant_id"])

    op.create_table(
        "tenant_quota",
        sa.Column("tenant_id", sa.String(ID_LEN), nullable=False),
        sa.Column("storage_bytes_max", sa.BigInteger, nullable=True),
        sa.Column("gpu_seconds_per_day_max", sa.BigInteger, nullable=True),
        sa.Column("concurrent_jobs_max", sa.Integer, nullable=True),
        sa.Column("storage_bytes_used", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", name="pk_tenant_quota"),
    )

    op.create_table(
        "gpu_usage",
        sa.Column("usage_id", sa.BigInteger, autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(ID_LEN), nullable=False),
        sa.Column("project_id", sa.String(ID_LEN), nullable=False),
        sa.Column("job_id", sa.String(ID_LEN), nullable=False),
        sa.Column("task_id", sa.String(ID_LEN), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("gpu_seconds", sa.BigInteger, nullable=True),
        sa.PrimaryKeyConstraint("usage_id", name="pk_gpu_usage"),
    )
    op.create_index("ix_gpu_usage_tenant_id_started_at", "gpu_usage", ["tenant_id", "started_at"])


def downgrade() -> None:
    op.drop_index("ix_gpu_usage_tenant_id_started_at", table_name="gpu_usage")
    op.drop_table("gpu_usage")
    op.drop_table("tenant_quota")
    op.drop_index("ix_api_key_tenant_id", table_name="api_key")
    op.drop_table("api_key")
