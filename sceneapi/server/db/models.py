"""ORM models — Phase 0 set.

Every domain table carries `tenant_id` (NOT NULL, default 'default') so
multi-tenant routing/quotas can be added in Phase 5 without a migration
that touches every row.

ID columns are 26-char ULIDs. JSON columns use SQLAlchemy's portable JSON
(maps to TEXT on SQLite, JSON on Postgres). We do **not** use JSONB —
parity with SQLite would be lost.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from sceneapi.server.core.ids import ID_LEN, new_id
from sceneapi.server.db.base import Base
from sceneapi.server.db.types import ULIDType


def utcnow() -> datetime:
    return datetime.now(UTC)


class Tenant(Base):
    __tablename__ = "tenant"

    tenant_id: Mapped[str] = mapped_column(ULIDType, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    quotas_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class Project(Base):
    __tablename__ = "project"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_project_tenant_id_name"),
        Index("ix_project_tenant_id", "tenant_id"),
    )

    project_id: Mapped[str] = mapped_column(ULIDType, primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(ID_LEN), nullable=False, default="default")
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class Blob(Base):
    """Content-addressed blob registry. The bytes live at
    `<blob_root>/<sha[:2]>/<sha>`; refcount tracks how many `Image`s (or
    other entities) reference this blob.
    """

    __tablename__ = "blob"

    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mime: Mapped[str | None] = mapped_column(String(127), nullable=True)
    refcount: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class ImageSource(Base):
    """Where the bytes live. `kind=upload` => bytes in the blob store;
    `kind=local` => bytes referenced via filesystem path; `kind=s3` =>
    remote, lazily cached. Source is logically immutable; mutation creates
    a new ImageSource."""

    __tablename__ = "image_source"
    __table_args__ = (Index("ix_image_source_tenant_id", "tenant_id"),)

    source_id: Mapped[str] = mapped_column(ULIDType, primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(ID_LEN), nullable=False, default="default")
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # upload | local | s3
    uri_or_root: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    fingerprint_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class Dataset(Base):
    __tablename__ = "dataset"
    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_dataset_project_id_name"),
        Index("ix_dataset_tenant_id", "tenant_id"),
    )

    dataset_id: Mapped[str] = mapped_column(ULIDType, primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(ID_LEN), nullable=False, default="default")
    project_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("project.project_id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("image_source.source_id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    camera_model: Mapped[str] = mapped_column(String(64), nullable=False, default="SIMPLE_RADIAL")
    intrinsics_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, default="single_camera"
    )
    is_spherical: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    rig_config_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    respect_exif_orientation: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    active_maskset_id: Mapped[str | None] = mapped_column(String(ID_LEN), nullable=True)

    manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class Image(Base):
    __tablename__ = "image"
    __table_args__ = (
        UniqueConstraint("dataset_id", "name", name="uq_image_dataset_id_name"),
        Index("ix_image_tenant_id", "tenant_id"),
        Index("ix_image_dataset_id", "dataset_id"),
        Index("ix_image_content_sha", "content_sha"),
    )

    image_id: Mapped[str] = mapped_column(ULIDType, primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(ID_LEN), nullable=False, default="default")
    dataset_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("dataset.dataset_id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    content_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exif_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # PosePrior wire shape — see sceneapi.server.schemas.api.scene.PosePrior. Stored
    # opaquely as JSON so adding optional fields (covariance / GPS) is a
    # schema-side concern, not a migration.
    pose_prior_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # If kind=upload, content_sha must equal a blob row. If kind=local/s3, the
    # blob may not exist locally (referenced via source). The api forms ensure
    # consistency at create time.
    source_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    rel_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class Upload(Base):
    """Chunked-upload state."""

    __tablename__ = "upload"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_upload_tenant_id_idempotency_key"
        ),
        Index("ix_upload_tenant_id", "tenant_id"),
        Index("ix_upload_expires_at", "expires_at"),
    )

    upload_id: Mapped[str] = mapped_column(ULIDType, primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(ID_LEN), nullable=False, default="default")
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expected_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    received_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(127), nullable=True)
    expected_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    state: Mapped[str] = mapped_column(String(16), default="open", nullable=False)
    blob_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class MaskSet(Base):
    __tablename__ = "maskset"
    __table_args__ = (
        UniqueConstraint("dataset_id", "name", name="uq_maskset_dataset_id_name"),
        Index("ix_maskset_tenant_id", "tenant_id"),
    )

    maskset_id: Mapped[str] = mapped_column(ULIDType, primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(ID_LEN), nullable=False, default="default")
    dataset_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("dataset.dataset_id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class Mask(Base):
    __tablename__ = "mask"
    __table_args__ = (
        UniqueConstraint("maskset_id", "image_id", name="uq_mask_maskset_id_image_id"),
        Index("ix_mask_tenant_id", "tenant_id"),
    )

    mask_id: Mapped[str] = mapped_column(ULIDType, primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(ID_LEN), nullable=False, default="default")
    maskset_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("maskset.maskset_id", ondelete="CASCADE"), nullable=False
    )
    image_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("image.image_id", ondelete="CASCADE"), nullable=False
    )
    content_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    rel_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class ModelArtifact(Base):
    __tablename__ = "model_artifact"
    __table_args__ = (
        UniqueConstraint(
            "family",
            "name",
            "version",
            name="uq_model_artifact_family_name_version",
        ),
    )

    artifact_id: Mapped[str] = mapped_column(ULIDType, primary_key=True, default=new_id)
    family: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    local_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    byte_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class Job(Base):
    __tablename__ = "job"
    __table_args__ = (
        Index("ix_job_tenant_id", "tenant_id"),
        Index("ix_job_status", "status"),
    )

    job_id: Mapped[str] = mapped_column(ULIDType, primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(ID_LEN), nullable=False, default="default")
    project_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("project.project_id", ondelete="CASCADE"), nullable=False
    )
    recipe: Mapped[str] = mapped_column(String(64), nullable=False)
    spec_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cancel_force: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(2048), nullable=True)


class Task(Base):
    __tablename__ = "task"
    __table_args__ = (
        Index("ix_task_tenant_id", "tenant_id"),
        Index("ix_task_cache_key", "cache_key"),
        Index("ix_task_status", "status"),
        Index("ix_task_lease_expires_at", "lease_expires_at"),
        # Composite index serving the janitor's hot predicates:
        # lease reclaim (status='running' AND lease_expires_at < now)
        # and the pending-task sweeps (status='pending' prefix scan).
        Index("ix_task_status_lease", "status", "lease_expires_at"),
    )

    task_id: Mapped[str] = mapped_column(ULIDType, primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(ID_LEN), nullable=False, default="default")
    job_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("job.job_id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    inputs_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    params_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    runtime_version_id: Mapped[str] = mapped_column(String(ID_LEN), nullable=False)
    cache_key: Mapped[str] = mapped_column(String(64), nullable=False)
    depends_on_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    gpu_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Pre-execution carrier: ``inputs`` + ``spec`` populated by the
    # orchestrator's ``materialize_dag``. Workers read state from
    # here. Decoupled from ``outputs_ref_json`` (post-execution
    # result) to keep the wire-side ``TaskOut.outputs_ref`` typed as
    # the result only — see ``L27`` in ``decisions.md``.
    task_state_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Post-execution result: dispatcher writes the worker's return
    # value here on success. Surfaced on the wire as
    # ``TaskOut.outputs_ref``.
    outputs_ref_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class Reconstruction(Base):
    __tablename__ = "reconstruction"
    __table_args__ = (Index("ix_reconstruction_tenant_id", "tenant_id"),)

    recon_id: Mapped[str] = mapped_column(ULIDType, primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(ID_LEN), nullable=False, default="default")
    project_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("project.project_id", ondelete="CASCADE"), nullable=False
    )
    dataset_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("dataset.dataset_id", ondelete="CASCADE"), nullable=False
    )
    dataset_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    spec_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    rv_id: Mapped[str] = mapped_column(String(ID_LEN), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="running")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class SubModel(Base):
    __tablename__ = "submodel"
    __table_args__ = (
        Index("ix_submodel_tenant_id", "tenant_id"),
        Index("ix_submodel_recon_id", "recon_id"),
    )

    submodel_id: Mapped[str] = mapped_column(ULIDType, primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(ID_LEN), nullable=False, default="default")
    recon_id: Mapped[str] = mapped_column(
        String(ID_LEN),
        ForeignKey("reconstruction.recon_id", ondelete="CASCADE"),
        nullable=False,
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_submodel_id: Mapped[str | None] = mapped_column(String(ID_LEN), nullable=True)
    summary_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    rigidity_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    sealed_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    snapshot_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class RadianceField(Base):
    __tablename__ = "radiance_field"
    __table_args__ = (
        Index("ix_radiance_field_tenant_id", "tenant_id"),
        Index("ix_radiance_field_project_id", "project_id"),
    )

    radiance_field_id: Mapped[str] = mapped_column(ULIDType, primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(ID_LEN), nullable=False, default="default")
    project_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("project.project_id", ondelete="CASCADE"), nullable=False
    )
    dataset_id: Mapped[str | None] = mapped_column(
        String(ID_LEN), ForeignKey("dataset.dataset_id", ondelete="SET NULL"), nullable=True
    )
    recon_id: Mapped[str | None] = mapped_column(
        String(ID_LEN),
        ForeignKey("reconstruction.recon_id", ondelete="SET NULL"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[str] = mapped_column(String(129), nullable=False)
    method: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="running")
    spec_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    summary_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class RadianceSnapshot(Base):
    __tablename__ = "radiance_snapshot"
    __table_args__ = (
        UniqueConstraint(
            "radiance_field_id",
            "seq",
            name="uq_radiance_snapshot_radiance_field_id_seq",
        ),
        Index("ix_radiance_snapshot_tenant_id", "tenant_id"),
        Index("ix_radiance_snapshot_radiance_field_id", "radiance_field_id"),
    )

    snapshot_id: Mapped[str] = mapped_column(ULIDType, primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(ID_LEN), nullable=False, default="default")
    radiance_field_id: Mapped[str] = mapped_column(
        String(ID_LEN),
        ForeignKey("radiance_field.radiance_field_id", ondelete="CASCADE"),
        nullable=False,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    sealed_path: Mapped[str] = mapped_column(String(2048), nullable=False)
    summary_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class RadianceEvaluation(Base):
    __tablename__ = "radiance_evaluation"
    __table_args__ = (
        Index("ix_radiance_evaluation_tenant_id", "tenant_id"),
        Index("ix_radiance_evaluation_radiance_field_id", "radiance_field_id"),
    )

    evaluation_id: Mapped[str] = mapped_column(ULIDType, primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(ID_LEN), nullable=False, default="default")
    radiance_field_id: Mapped[str] = mapped_column(
        String(ID_LEN),
        ForeignKey("radiance_field.radiance_field_id", ondelete="CASCADE"),
        nullable=False,
    )
    snapshot_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    dataset_id: Mapped[str | None] = mapped_column(String(ID_LEN), nullable=True)
    provider: Mapped[str] = mapped_column(String(129), nullable=False)
    method: Mapped[str] = mapped_column(String(64), nullable=False)
    split: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="running")
    config_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    metrics_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    artifacts_json: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    error_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(ID_LEN), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class RadianceVariant(Base):
    __tablename__ = "radiance_variant"
    __table_args__ = (
        Index("ix_radiance_variant_tenant_id", "tenant_id"),
        Index("ix_radiance_variant_snapshot_id", "snapshot_id"),
    )

    variant_id: Mapped[str] = mapped_column(ULIDType, primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(ID_LEN), nullable=False, default="default")
    snapshot_id: Mapped[str] = mapped_column(
        String(ID_LEN),
        ForeignKey("radiance_snapshot.snapshot_id", ondelete="CASCADE"),
        nullable=False,
    )
    format: Mapped[str] = mapped_column(String(32), nullable=False)
    uri: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    media_type: Mapped[str | None] = mapped_column(String(127), nullable=True)
    summary_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class StageArtifact(Base):
    __tablename__ = "stage_artifact"
    __table_args__ = (
        Index("ix_stage_artifact_tenant_id", "tenant_id"),
        Index("ix_stage_artifact_job_id", "job_id"),
        Index("ix_stage_artifact_task_id", "task_id"),
        Index("ix_stage_artifact_recon_id", "recon_id"),
        Index("ix_stage_artifact_kind", "kind"),
    )

    artifact_id: Mapped[str] = mapped_column(ULIDType, primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(ID_LEN), nullable=False, default="default")
    job_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("job.job_id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("task.task_id", ondelete="CASCADE"), nullable=False
    )
    recon_id: Mapped[str | None] = mapped_column(
        String(ID_LEN), ForeignKey("reconstruction.recon_id", ondelete="CASCADE"), nullable=True
    )
    dataset_id: Mapped[str | None] = mapped_column(
        String(ID_LEN), ForeignKey("dataset.dataset_id", ondelete="CASCADE"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(96), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    uri: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    media_type: Mapped[str | None] = mapped_column(String(127), nullable=True)
    summary_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class JobEvent(Base):
    __tablename__ = "job_event"
    __table_args__ = (Index("ix_job_event_job_id_event_id", "job_id", "event_id"),)

    event_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("job.job_id", ondelete="CASCADE"), nullable=False
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)


class ApiKey(Base):
    __tablename__ = "api_key"
    __table_args__ = (
        UniqueConstraint("key_hash", name="uq_api_key_key_hash"),
        Index("ix_api_key_tenant_id", "tenant_id"),
    )

    api_key_id: Mapped[str] = mapped_column(ULIDType, primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(ID_LEN), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TenantQuota(Base):
    __tablename__ = "tenant_quota"

    tenant_id: Mapped[str] = mapped_column(String(ID_LEN), primary_key=True)
    storage_bytes_max: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    gpu_seconds_per_day_max: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    concurrent_jobs_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    storage_bytes_used: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class GpuUsage(Base):
    __tablename__ = "gpu_usage"
    __table_args__ = (Index("ix_gpu_usage_tenant_id_started_at", "tenant_id", "started_at"),)

    usage_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(ID_LEN), nullable=False)
    project_id: Mapped[str] = mapped_column(String(ID_LEN), nullable=False)
    job_id: Mapped[str] = mapped_column(String(ID_LEN), nullable=False)
    task_id: Mapped[str] = mapped_column(String(ID_LEN), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    gpu_seconds: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class RuntimeVersion(Base):
    __tablename__ = "runtime_version"
    __table_args__ = (
        UniqueConstraint(
            "runtime_version_id",
            "seed",
            name="uq_runtime_version_tuple",
        ),
    )

    rv_id: Mapped[str] = mapped_column(ULIDType, primary_key=True, default=new_id)
    # Backend-defined freeform fingerprint string (hash of all
    # engine-specific runtime knobs). sfmapi treats it as opaque —
    # the registered backend computes it.
    runtime_version_id: Mapped[str] = mapped_column(String(128), nullable=False)
    seed: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
