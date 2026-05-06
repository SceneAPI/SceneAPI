"""phase 3 masksets + masks + model_artifact

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-01

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ID_LEN = 26


def upgrade() -> None:
    op.create_table(
        "maskset",
        sa.Column("maskset_id", sa.CHAR(ID_LEN), nullable=False),
        sa.Column("tenant_id", sa.String(ID_LEN), nullable=False, server_default="default"),
        sa.Column("dataset_id", sa.String(ID_LEN), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("maskset_id", name="pk_maskset"),
        sa.UniqueConstraint("dataset_id", "name", name="uq_maskset_dataset_id_name"),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["dataset.dataset_id"],
            ondelete="CASCADE",
            name="fk_maskset_dataset_id_dataset",
        ),
    )
    op.create_index("ix_maskset_tenant_id", "maskset", ["tenant_id"])

    op.create_table(
        "mask",
        sa.Column("mask_id", sa.CHAR(ID_LEN), nullable=False),
        sa.Column("tenant_id", sa.String(ID_LEN), nullable=False, server_default="default"),
        sa.Column("maskset_id", sa.String(ID_LEN), nullable=False),
        sa.Column("image_id", sa.String(ID_LEN), nullable=False),
        sa.Column("content_sha", sa.String(64), nullable=False),
        sa.Column("rel_path", sa.String(1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("mask_id", name="pk_mask"),
        sa.UniqueConstraint("maskset_id", "image_id", name="uq_mask_maskset_id_image_id"),
        sa.ForeignKeyConstraint(
            ["maskset_id"],
            ["maskset.maskset_id"],
            ondelete="CASCADE",
            name="fk_mask_maskset_id_maskset",
        ),
        sa.ForeignKeyConstraint(
            ["image_id"],
            ["image.image_id"],
            ondelete="CASCADE",
            name="fk_mask_image_id_image",
        ),
    )
    op.create_index("ix_mask_tenant_id", "mask", ["tenant_id"])

    op.create_table(
        "model_artifact",
        sa.Column("artifact_id", sa.CHAR(ID_LEN), nullable=False),
        sa.Column("family", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("local_path", sa.String(2048), nullable=True),
        sa.Column("source_url", sa.String(2048), nullable=True),
        sa.Column("byte_size", sa.BigInteger, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("artifact_id", name="pk_model_artifact"),
        sa.UniqueConstraint(
            "family",
            "name",
            "version",
            name="uq_model_artifact_family_name_version",
        ),
    )


def downgrade() -> None:
    op.drop_table("model_artifact")
    op.drop_index("ix_mask_tenant_id", table_name="mask")
    op.drop_table("mask")
    op.drop_index("ix_maskset_tenant_id", table_name="maskset")
    op.drop_table("maskset")
