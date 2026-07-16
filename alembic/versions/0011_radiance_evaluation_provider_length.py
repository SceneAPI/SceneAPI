"""add radiance evaluations and widen provider selectors

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-01

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("radiance_field") as batch_op:
        batch_op.alter_column(
            "provider",
            existing_type=sa.String(64),
            type_=sa.String(129),
            existing_nullable=False,
        )

    op.create_table(
        "radiance_evaluation",
        sa.Column("evaluation_id", sa.CHAR(26), nullable=False),
        sa.Column("tenant_id", sa.String(26), nullable=False),
        sa.Column("radiance_field_id", sa.String(26), nullable=False),
        sa.Column("snapshot_seq", sa.Integer(), nullable=False),
        sa.Column("dataset_id", sa.String(26), nullable=True),
        sa.Column("provider", sa.String(129), nullable=False),
        sa.Column("method", sa.String(64), nullable=False),
        sa.Column("split", sa.String(32), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("metrics_json", sa.JSON(), nullable=True),
        sa.Column("artifacts_json", sa.JSON(), nullable=True),
        sa.Column("error_json", sa.JSON(), nullable=True),
        sa.Column("job_id", sa.String(26), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["radiance_field_id"],
            ["radiance_field.radiance_field_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("evaluation_id"),
    )
    op.create_index(
        "ix_radiance_evaluation_tenant_id",
        "radiance_evaluation",
        ["tenant_id"],
    )
    op.create_index(
        "ix_radiance_evaluation_radiance_field_id",
        "radiance_evaluation",
        ["radiance_field_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_radiance_evaluation_radiance_field_id",
        table_name="radiance_evaluation",
    )
    op.drop_index("ix_radiance_evaluation_tenant_id", table_name="radiance_evaluation")
    op.drop_table("radiance_evaluation")

    with op.batch_alter_table("radiance_field") as batch_op:
        batch_op.alter_column(
            "provider",
            existing_type=sa.String(129),
            type_=sa.String(64),
            existing_nullable=False,
        )
