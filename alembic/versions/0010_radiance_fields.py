"""add radiance field resources

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-25

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "radiance_field",
        sa.Column("radiance_field_id", sa.CHAR(26), nullable=False),
        sa.Column("tenant_id", sa.String(26), nullable=False),
        sa.Column("project_id", sa.String(26), nullable=False),
        sa.Column("dataset_id", sa.String(26), nullable=True),
        sa.Column("recon_id", sa.String(26), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("method", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("spec_json", sa.JSON(), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["dataset.dataset_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["project.project_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recon_id"], ["reconstruction.recon_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("radiance_field_id"),
    )
    op.create_index("ix_radiance_field_tenant_id", "radiance_field", ["tenant_id"])
    op.create_index("ix_radiance_field_project_id", "radiance_field", ["project_id"])

    op.create_table(
        "radiance_snapshot",
        sa.Column("snapshot_id", sa.CHAR(26), nullable=False),
        sa.Column("tenant_id", sa.String(26), nullable=False),
        sa.Column("radiance_field_id", sa.String(26), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("sealed_path", sa.String(2048), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["radiance_field_id"],
            ["radiance_field.radiance_field_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("snapshot_id"),
        sa.UniqueConstraint(
            "radiance_field_id",
            "seq",
            name="uq_radiance_snapshot_radiance_field_id_seq",
        ),
    )
    op.create_index("ix_radiance_snapshot_tenant_id", "radiance_snapshot", ["tenant_id"])
    op.create_index(
        "ix_radiance_snapshot_radiance_field_id",
        "radiance_snapshot",
        ["radiance_field_id"],
    )

    op.create_table(
        "radiance_variant",
        sa.Column("variant_id", sa.CHAR(26), nullable=False),
        sa.Column("tenant_id", sa.String(26), nullable=False),
        sa.Column("snapshot_id", sa.String(26), nullable=False),
        sa.Column("format", sa.String(32), nullable=False),
        sa.Column("uri", sa.String(2048), nullable=True),
        sa.Column("media_type", sa.String(127), nullable=True),
        sa.Column("summary_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["radiance_snapshot.snapshot_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("variant_id"),
    )
    op.create_index("ix_radiance_variant_tenant_id", "radiance_variant", ["tenant_id"])
    op.create_index("ix_radiance_variant_snapshot_id", "radiance_variant", ["snapshot_id"])


def downgrade() -> None:
    op.drop_index("ix_radiance_variant_snapshot_id", table_name="radiance_variant")
    op.drop_index("ix_radiance_variant_tenant_id", table_name="radiance_variant")
    op.drop_table("radiance_variant")
    op.drop_index("ix_radiance_snapshot_radiance_field_id", table_name="radiance_snapshot")
    op.drop_index("ix_radiance_snapshot_tenant_id", table_name="radiance_snapshot")
    op.drop_table("radiance_snapshot")
    op.drop_index("ix_radiance_field_project_id", table_name="radiance_field")
    op.drop_index("ix_radiance_field_tenant_id", table_name="radiance_field")
    op.drop_table("radiance_field")
