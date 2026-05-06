"""phase 0 initial schema

Revision ID: 0001
Revises:
Create Date: 2026-05-01

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ID_LEN = 26


def upgrade() -> None:
    op.create_table(
        "tenant",
        sa.Column("tenant_id", sa.CHAR(ID_LEN), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("quotas_json", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", name="pk_tenant"),
        sa.UniqueConstraint("name", name="uq_tenant_name"),
    )

    op.create_table(
        "project",
        sa.Column("project_id", sa.CHAR(ID_LEN), nullable=False),
        sa.Column("tenant_id", sa.String(ID_LEN), nullable=False, server_default="default"),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.String(1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("project_id", name="pk_project"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_project_tenant_id_name"),
    )
    op.create_index("ix_project_tenant_id", "project", ["tenant_id"])

    op.create_table(
        "blob",
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.BigInteger, nullable=False),
        sa.Column("mime", sa.String(127), nullable=True),
        sa.Column("refcount", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("sha256", name="pk_blob"),
    )

    op.create_table(
        "image_source",
        sa.Column("source_id", sa.CHAR(ID_LEN), nullable=False),
        sa.Column("tenant_id", sa.String(ID_LEN), nullable=False, server_default="default"),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("uri_or_root", sa.String(2048), nullable=True),
        sa.Column("fingerprint_json", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("source_id", name="pk_image_source"),
    )
    op.create_index("ix_image_source_tenant_id", "image_source", ["tenant_id"])

    op.create_table(
        "dataset",
        sa.Column("dataset_id", sa.CHAR(ID_LEN), nullable=False),
        sa.Column("tenant_id", sa.String(ID_LEN), nullable=False, server_default="default"),
        sa.Column("project_id", sa.String(ID_LEN), nullable=False),
        sa.Column("source_id", sa.String(ID_LEN), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("camera_model", sa.String(64), nullable=False, server_default="SIMPLE_RADIAL"),
        sa.Column("intrinsics_mode", sa.String(32), nullable=False, server_default="single_camera"),
        sa.Column("is_spherical", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("rig_config_json", sa.JSON, nullable=True),
        sa.Column(
            "respect_exif_orientation", sa.Boolean, nullable=False, server_default=sa.false()
        ),
        sa.Column("active_maskset_id", sa.String(ID_LEN), nullable=True),
        sa.Column("manifest_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("dataset_id", name="pk_dataset"),
        sa.UniqueConstraint("project_id", "name", name="uq_dataset_project_id_name"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.project_id"],
            ondelete="CASCADE",
            name="fk_dataset_project_id_project",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["image_source.source_id"],
            ondelete="RESTRICT",
            name="fk_dataset_source_id_image_source",
        ),
    )
    op.create_index("ix_dataset_tenant_id", "dataset", ["tenant_id"])

    op.create_table(
        "image",
        sa.Column("image_id", sa.CHAR(ID_LEN), nullable=False),
        sa.Column("tenant_id", sa.String(ID_LEN), nullable=False, server_default="default"),
        sa.Column("dataset_id", sa.String(ID_LEN), nullable=False),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("content_sha", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.BigInteger, nullable=True),
        sa.Column("width", sa.Integer, nullable=True),
        sa.Column("height", sa.Integer, nullable=True),
        sa.Column("exif_json", sa.JSON, nullable=True),
        sa.Column("source_kind", sa.String(16), nullable=False),
        sa.Column("rel_path", sa.String(1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("image_id", name="pk_image"),
        sa.UniqueConstraint("dataset_id", "name", name="uq_image_dataset_id_name"),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["dataset.dataset_id"],
            ondelete="CASCADE",
            name="fk_image_dataset_id_dataset",
        ),
    )
    op.create_index("ix_image_tenant_id", "image", ["tenant_id"])
    op.create_index("ix_image_dataset_id", "image", ["dataset_id"])
    op.create_index("ix_image_content_sha", "image", ["content_sha"])

    op.create_table(
        "upload",
        sa.Column("upload_id", sa.CHAR(ID_LEN), nullable=False),
        sa.Column("tenant_id", sa.String(ID_LEN), nullable=False, server_default="default"),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
        sa.Column("expected_size", sa.BigInteger, nullable=False),
        sa.Column("received_bytes", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("content_type", sa.String(127), nullable=True),
        sa.Column("expected_sha", sa.String(64), nullable=True),
        sa.Column("state", sa.String(16), nullable=False, server_default="open"),
        sa.Column("blob_sha", sa.String(64), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("upload_id", name="pk_upload"),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_upload_tenant_id_idempotency_key",
        ),
    )
    op.create_index("ix_upload_tenant_id", "upload", ["tenant_id"])
    op.create_index("ix_upload_expires_at", "upload", ["expires_at"])

    op.create_table(
        "runtime_version",
        sa.Column("rv_id", sa.CHAR(ID_LEN), nullable=False),
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


def downgrade() -> None:
    op.drop_table("runtime_version")
    op.drop_index("ix_upload_expires_at", table_name="upload")
    op.drop_index("ix_upload_tenant_id", table_name="upload")
    op.drop_table("upload")
    op.drop_index("ix_image_content_sha", table_name="image")
    op.drop_index("ix_image_dataset_id", table_name="image")
    op.drop_index("ix_image_tenant_id", table_name="image")
    op.drop_table("image")
    op.drop_index("ix_dataset_tenant_id", table_name="dataset")
    op.drop_table("dataset")
    op.drop_index("ix_image_source_tenant_id", table_name="image_source")
    op.drop_table("image_source")
    op.drop_table("blob")
    op.drop_index("ix_project_tenant_id", table_name="project")
    op.drop_table("project")
    op.drop_table("tenant")
