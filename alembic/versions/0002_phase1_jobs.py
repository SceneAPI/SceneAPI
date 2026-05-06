"""phase 1 jobs + tasks + reconstructions

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-01

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ID_LEN = 26


def upgrade() -> None:
    op.create_table(
        "job",
        sa.Column("job_id", sa.CHAR(ID_LEN), nullable=False),
        sa.Column("tenant_id", sa.String(ID_LEN), nullable=False, server_default="default"),
        sa.Column("project_id", sa.String(ID_LEN), nullable=False),
        sa.Column("recipe", sa.String(64), nullable=False),
        sa.Column("spec_json", sa.JSON, nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("cancel_requested", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("cancel_force", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_class", sa.String(64), nullable=True),
        sa.Column("error_message", sa.String(2048), nullable=True),
        sa.PrimaryKeyConstraint("job_id", name="pk_job"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.project_id"],
            ondelete="CASCADE",
            name="fk_job_project_id_project",
        ),
    )
    op.create_index("ix_job_tenant_id", "job", ["tenant_id"])
    op.create_index("ix_job_status", "job", ["status"])

    op.create_table(
        "task",
        sa.Column("task_id", sa.CHAR(ID_LEN), nullable=False),
        sa.Column("tenant_id", sa.String(ID_LEN), nullable=False, server_default="default"),
        sa.Column("job_id", sa.String(ID_LEN), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("inputs_hash", sa.String(64), nullable=False),
        sa.Column("params_hash", sa.String(64), nullable=False),
        sa.Column("runtime_version_id", sa.String(ID_LEN), nullable=False),
        sa.Column("cache_key", sa.String(64), nullable=False),
        sa.Column("depends_on_json", sa.JSON, nullable=True),
        sa.Column("gpu_required", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("worker_id", sa.String(64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outputs_ref_json", sa.JSON, nullable=True),
        sa.Column("error_class", sa.String(64), nullable=True),
        sa.Column("error_message", sa.String(2048), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("task_id", name="pk_task"),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["job.job_id"],
            ondelete="CASCADE",
            name="fk_task_job_id_job",
        ),
    )
    op.create_index("ix_task_tenant_id", "task", ["tenant_id"])
    op.create_index("ix_task_cache_key", "task", ["cache_key"])
    op.create_index("ix_task_status", "task", ["status"])
    op.create_index("ix_task_lease_expires_at", "task", ["lease_expires_at"])

    op.create_table(
        "reconstruction",
        sa.Column("recon_id", sa.CHAR(ID_LEN), nullable=False),
        sa.Column("tenant_id", sa.String(ID_LEN), nullable=False, server_default="default"),
        sa.Column("project_id", sa.String(ID_LEN), nullable=False),
        sa.Column("dataset_id", sa.String(ID_LEN), nullable=False),
        sa.Column("dataset_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("spec_json", sa.JSON, nullable=False),
        sa.Column("rv_id", sa.String(ID_LEN), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("recon_id", name="pk_reconstruction"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.project_id"],
            ondelete="CASCADE",
            name="fk_reconstruction_project_id_project",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["dataset.dataset_id"],
            ondelete="CASCADE",
            name="fk_reconstruction_dataset_id_dataset",
        ),
    )
    op.create_index("ix_reconstruction_tenant_id", "reconstruction", ["tenant_id"])

    op.create_table(
        "submodel",
        sa.Column("submodel_id", sa.CHAR(ID_LEN), nullable=False),
        sa.Column("tenant_id", sa.String(ID_LEN), nullable=False, server_default="default"),
        sa.Column("recon_id", sa.String(ID_LEN), nullable=False),
        sa.Column("idx", sa.Integer, nullable=False),
        sa.Column("parent_submodel_id", sa.String(ID_LEN), nullable=True),
        sa.Column("summary_json", sa.JSON, nullable=True),
        sa.Column("rigidity_json", sa.JSON, nullable=True),
        sa.Column("sealed_path", sa.String(2048), nullable=True),
        sa.Column("snapshot_seq", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("submodel_id", name="pk_submodel"),
        sa.ForeignKeyConstraint(
            ["recon_id"],
            ["reconstruction.recon_id"],
            ondelete="CASCADE",
            name="fk_submodel_recon_id_reconstruction",
        ),
    )
    op.create_index("ix_submodel_tenant_id", "submodel", ["tenant_id"])
    op.create_index("ix_submodel_recon_id", "submodel", ["recon_id"])

    op.create_table(
        "job_event",
        sa.Column("event_id", sa.BigInteger, autoincrement=True, nullable=False),
        sa.Column("job_id", sa.String(ID_LEN), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_json", sa.JSON, nullable=False),
        sa.PrimaryKeyConstraint("event_id", name="pk_job_event"),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["job.job_id"],
            ondelete="CASCADE",
            name="fk_job_event_job_id_job",
        ),
    )
    op.create_index("ix_job_event_job_id_event_id", "job_event", ["job_id", "event_id"])


def downgrade() -> None:
    op.drop_index("ix_job_event_job_id_event_id", table_name="job_event")
    op.drop_table("job_event")
    op.drop_index("ix_submodel_recon_id", table_name="submodel")
    op.drop_index("ix_submodel_tenant_id", table_name="submodel")
    op.drop_table("submodel")
    op.drop_index("ix_reconstruction_tenant_id", table_name="reconstruction")
    op.drop_table("reconstruction")
    op.drop_index("ix_task_lease_expires_at", table_name="task")
    op.drop_index("ix_task_status", table_name="task")
    op.drop_index("ix_task_cache_key", table_name="task")
    op.drop_index("ix_task_tenant_id", table_name="task")
    op.drop_table("task")
    op.drop_index("ix_job_status", table_name="job")
    op.drop_index("ix_job_tenant_id", table_name="job")
    op.drop_table("job")
