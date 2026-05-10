"""add first-class stage artifacts

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-09

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "stage_artifact",
        sa.Column("artifact_id", sa.CHAR(26), nullable=False),
        sa.Column("tenant_id", sa.String(26), nullable=False),
        sa.Column("job_id", sa.String(26), nullable=False),
        sa.Column("task_id", sa.String(26), nullable=False),
        sa.Column("recon_id", sa.String(26), nullable=True),
        sa.Column("dataset_id", sa.String(26), nullable=True),
        sa.Column("kind", sa.String(96), nullable=False),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("uri", sa.String(2048), nullable=True),
        sa.Column("media_type", sa.String(127), nullable=True),
        sa.Column("summary_json", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["dataset.dataset_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["job.job_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recon_id"], ["reconstruction.recon_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["task.task_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("artifact_id"),
    )
    op.create_index("ix_stage_artifact_tenant_id", "stage_artifact", ["tenant_id"])
    op.create_index("ix_stage_artifact_job_id", "stage_artifact", ["job_id"])
    op.create_index("ix_stage_artifact_task_id", "stage_artifact", ["task_id"])
    op.create_index("ix_stage_artifact_recon_id", "stage_artifact", ["recon_id"])
    op.create_index("ix_stage_artifact_kind", "stage_artifact", ["kind"])


def downgrade() -> None:
    op.drop_index("ix_stage_artifact_kind", table_name="stage_artifact")
    op.drop_index("ix_stage_artifact_recon_id", table_name="stage_artifact")
    op.drop_index("ix_stage_artifact_task_id", table_name="stage_artifact")
    op.drop_index("ix_stage_artifact_job_id", table_name="stage_artifact")
    op.drop_index("ix_stage_artifact_tenant_id", table_name="stage_artifact")
    op.drop_table("stage_artifact")
