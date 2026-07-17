"""Tenant-aware workspace path builder."""

from __future__ import annotations

from pathlib import Path

from sfmapi.server.core.config import Settings, get_settings


class Paths:
    def __init__(self, settings: Settings | None = None) -> None:
        self.s = settings or get_settings()

    @property
    def workspace_root(self) -> Path:
        """Direct accessor for the configured workspace root.

        Worker tasks construct per-task staging dirs underneath
        ``workspace_root / "_stage" / task_id``; expose it here so
        they don't have to reach into ``paths.s.workspace_root``.
        """
        return self.s.workspace_root

    def tenant_root(self, tenant_id: str) -> Path:
        return self.s.workspace_root / tenant_id

    def project_root(self, tenant_id: str, project_id: str) -> Path:
        return self.tenant_root(tenant_id) / "projects" / project_id

    def dataset_root(self, tenant_id: str, project_id: str, dataset_id: str) -> Path:
        return self.project_root(tenant_id, project_id) / "datasets" / dataset_id

    def maskset_root(
        self, tenant_id: str, project_id: str, dataset_id: str, maskset_id: str
    ) -> Path:
        return self.dataset_root(tenant_id, project_id, dataset_id) / "masks" / maskset_id

    def reconstruction_root(self, tenant_id: str, project_id: str, reconstruction_id: str) -> Path:
        return self.project_root(tenant_id, project_id) / "reconstructions" / reconstruction_id

    def radiance_field_root(self, tenant_id: str, project_id: str, radiance_field_id: str) -> Path:
        return self.project_root(tenant_id, project_id) / "radiance_fields" / radiance_field_id

    def snapshot_root(
        self, tenant_id: str, project_id: str, reconstruction_id: str, seq: int
    ) -> Path:
        return (
            self.reconstruction_root(tenant_id, project_id, reconstruction_id)
            / "snapshots"
            / f"{seq:08d}"
        )

    def job_root(self, tenant_id: str, project_id: str, job_id: str) -> Path:
        return self.project_root(tenant_id, project_id) / "jobs" / job_id

    def blob_path(self, sha: str) -> Path:
        return self.s.blob_root / sha[:2] / sha

    def upload_temp_path(self, upload_id: str) -> Path:
        return self.s.workspace_root / "_uploads" / upload_id

    def s3_cache_path(self, bucket: str, key_hash: str) -> Path:
        return self.s.s3_cache_root / bucket / key_hash
