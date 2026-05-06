"""Synchronous SDK client.

Idiomatic usage:

    with SfmApiClient("http://localhost:8080", api_key=os.environ["SFMAPI_KEY"]) as c:
        proj = c.create_project("my-proj")
        sha = c.upload_bytes(b"...", name="img.jpg")
        ds = c.create_dataset(proj.project_id, name="ds",
                              source={"kind": "upload",
                                      "entries": [{"name": "img.jpg",
                                                   "blob_sha": sha}]})
        job = c.run_pipeline(
            proj.project_id, dataset_id=ds.dataset_id,
            image_root="...", image_list=["img.jpg"],
            spec=IncrementalSpec(),
        )
"""

from __future__ import annotations

import io
from typing import Any, BinaryIO

import httpx

from sfmapi_client._transport import auth_headers, stream_sha256
from sfmapi_client.errors import raise_for_response
from sfmapi_client.models import (
    ApiKey,
    ApiKeyCreated,
    BatchCreateImagesRequest,
    BatchCreateImagesResponse,
    Capabilities,
    CorrespondenceGraphFile,
    Dataset,
    DatasetPatch,
    DenseManifestFile,
    FeaturesSpec,
    Image,
    ImageObservation,
    Job,
    JobDetail,
    JobSubmitResponse,
    LocalizationResult,
    MatcherSpec,
    MeshFile,
    Page,
    PairsSpec,
    PipelineSpec,
    PointObservation,
    PosePrior,
    Project,
    ProjectPatch,
    Reconstruction,
    Sim3,
    SubModel,
    TilesIndex,
    TwoViewGeometriesFile,
    Upload,
    VerifySpec,
    VersionResponse,
)


def _spec_dict(spec: Any) -> Any:
    """Serialize a Pydantic model or pass through a dict."""
    return spec.model_dump(mode="json") if hasattr(spec, "model_dump") else spec


class SfmApiClient:
    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        timeout: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._owned_client = client is None
        self._client = client or httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers=auth_headers(api_key),
        )

    def __enter__(self) -> SfmApiClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owned_client:
            self._client.close()

    # ---- low-level ----

    def _req(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        resp = self._client.request(method, path, **kwargs)
        raise_for_response(resp)
        return resp

    # ---- meta ----

    def healthz(self) -> dict:
        return self._req("GET", "/healthz").json()

    def version(self) -> VersionResponse:
        return VersionResponse.model_validate(self._req("GET", "/version").json())

    # ---- projects ----

    def create_project(self, name: str, *, description: str | None = None) -> Project:
        body = {"name": name, "description": description}
        return Project.model_validate(self._req("POST", "/v1/projects", json=body).json())

    def get_project(self, project_id: str) -> Project:
        return Project.model_validate(self._req("GET", f"/v1/projects/{project_id}").json())

    def list_projects(self, *, page_size: int = 50, page_token: str | None = None) -> Page[Project]:
        params: dict[str, Any] = {"page_size": page_size}
        if page_token:
            params["page_token"] = page_token
        return Page[Project].model_validate(self._req("GET", "/v1/projects", params=params).json())

    def delete_project(self, project_id: str) -> None:
        self._req("DELETE", f"/v1/projects/{project_id}")

    # ---- uploads ----

    def init_upload(
        self,
        *,
        expected_size: int,
        content_type: str | None = None,
        expected_sha: str | None = None,
        idempotency_key: str | None = None,
    ) -> Upload:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        body = {
            "expected_size": expected_size,
            "content_type": content_type,
            "expected_sha": expected_sha,
        }
        return Upload.model_validate(
            self._req("POST", "/v1/uploads", json=body, headers=headers).json()
        )

    def patch_chunk(self, upload_id: str, *, offset: int, data: bytes) -> Upload:
        end = offset + len(data) - 1
        headers = {"Content-Range": f"bytes {offset}-{end}/{offset + len(data)}"}
        return Upload.model_validate(
            self._req(
                "PATCH",
                f"/v1/uploads/{upload_id}",
                content=data,
                headers=headers,
            ).json()
        )

    def finalize_upload(self, upload_id: str, *, client_sha: str | None = None) -> Upload:
        headers = {"X-Content-SHA256": client_sha} if client_sha else {}
        return Upload.model_validate(
            self._req(
                "POST",
                f"/v1/uploads/{upload_id}:finalize",
                json={},
                headers=headers,
            ).json()
        )

    def upload_bytes(
        self,
        data: bytes,
        *,
        chunk_size: int = 1 * 1024 * 1024,
        content_type: str | None = None,
        idempotency_key: str | None = None,
    ) -> str:
        """High-level helper: init → PATCH → finalize → returns blob sha."""
        sha, _ = stream_sha256(io.BytesIO(data))
        upload = self.init_upload(
            expected_size=len(data),
            expected_sha=sha,
            content_type=content_type,
            idempotency_key=idempotency_key,
        )
        offset = 0
        while offset < len(data):
            chunk = data[offset : offset + chunk_size]
            self.patch_chunk(upload.upload_id, offset=offset, data=chunk)
            offset += len(chunk)
        result = self.finalize_upload(upload.upload_id, client_sha=sha)
        assert result.blob_sha == sha
        return sha

    def upload_file(self, file: BinaryIO, *, content_type: str | None = None) -> str:
        return self.upload_bytes(file.read(), content_type=content_type)

    # ---- datasets ----

    def create_dataset(
        self,
        project_id: str,
        *,
        name: str,
        source: dict[str, Any],
        camera_model: str = "SIMPLE_RADIAL",
        intrinsics_mode: str = "single_camera",
        is_spherical: bool = False,
        rig_config: dict | None = None,
        respect_exif_orientation: bool = False,
    ) -> Dataset:
        body = {
            "name": name,
            "source": source,
            "camera_model": camera_model,
            "intrinsics_mode": intrinsics_mode,
            "is_spherical": is_spherical,
            "rig_config": rig_config,
            "respect_exif_orientation": respect_exif_orientation,
        }
        return Dataset.model_validate(
            self._req("POST", f"/v1/projects/{project_id}/datasets", json=body).json()
        )

    def get_dataset(self, project_id: str, dataset_id: str) -> Dataset:
        return Dataset.model_validate(
            self._req("GET", f"/v1/projects/{project_id}/datasets/{dataset_id}").json()
        )

    def list_datasets(self, project_id: str) -> list[Dataset]:
        rows = self._req("GET", f"/v1/projects/{project_id}/datasets").json()
        return [Dataset.model_validate(r) for r in rows]

    def add_image(
        self,
        dataset_id: str,
        *,
        name: str,
        blob_sha: str | None = None,
        rel_path: str | None = None,
        width: int | None = None,
        height: int | None = None,
        exif: dict | None = None,
    ) -> Image:
        body = {
            "name": name,
            "blob_sha": blob_sha,
            "rel_path": rel_path,
            "width": width,
            "height": height,
            "exif": exif,
        }
        return Image.model_validate(
            self._req("POST", f"/v1/datasets/{dataset_id}/images", json=body).json()
        )

    def list_images(
        self, dataset_id: str, *, page_size: int = 100, page_token: str | None = None
    ) -> Page[Image]:
        params: dict[str, Any] = {"page_size": page_size}
        if page_token:
            params["page_token"] = page_token
        return Page[Image].model_validate(
            self._req("GET", f"/v1/datasets/{dataset_id}/images", params=params).json()
        )

    # ---- SfM stages ----

    def submit_features(
        self,
        dataset_id: str,
        *,
        spec: FeaturesSpec | dict | None = None,
    ) -> JobSubmitResponse:
        body = {
            "spec": (spec.model_dump(mode="json") if hasattr(spec, "model_dump") else (spec or {})),
        }
        return JobSubmitResponse.model_validate(
            self._req("POST", f"/v1/datasets/{dataset_id}/features", json=body).json()
        )

    def submit_matches(
        self,
        dataset_id: str,
        *,
        pairs: PairsSpec | dict | None = None,
        matcher: MatcherSpec | dict | None = None,
    ) -> JobSubmitResponse:
        def _dump(x: Any) -> Any:
            return x.model_dump(mode="json") if hasattr(x, "model_dump") else x

        body: dict[str, Any] = {}
        if pairs is not None:
            body["pairs"] = _dump(pairs)
        if matcher is not None:
            body["matcher"] = _dump(matcher)
        return JobSubmitResponse.model_validate(
            self._req("POST", f"/v1/datasets/{dataset_id}/matches", json=body).json()
        )

    def submit_verify(
        self, dataset_id: str, *, spec: VerifySpec | dict | None = None
    ) -> JobSubmitResponse:
        body = {
            "spec": (spec.model_dump(mode="json") if hasattr(spec, "model_dump") else (spec or {})),
        }
        return JobSubmitResponse.model_validate(
            self._req("POST", f"/v1/datasets/{dataset_id}/verify", json=body).json()
        )

    def run_pipeline(
        self,
        project_id: str,
        *,
        dataset_id: str,
        spec: PipelineSpec,
        features: FeaturesSpec | dict | None = None,
        pairs: PairsSpec | dict | None = None,
        matcher: MatcherSpec | dict | None = None,
        verify: VerifySpec | dict | None = None,
    ) -> JobSubmitResponse:
        def _dump(x: Any) -> Any:
            return x.model_dump(mode="json") if hasattr(x, "model_dump") else x

        body: dict[str, Any] = {
            "dataset_id": dataset_id,
            "spec": _dump(spec),
        }
        if features is not None:
            body["features"] = _dump(features)
        if pairs is not None:
            body["pairs"] = _dump(pairs)
        if matcher is not None:
            body["matcher"] = _dump(matcher)
        if verify is not None:
            body["verify"] = _dump(verify)
        kind = body["spec"]["kind"] if isinstance(body["spec"], dict) else body["spec"].kind
        return JobSubmitResponse.model_validate(
            self._req("POST", f"/v1/projects/{project_id}/pipelines/{kind}", json=body).json()
        )

    # ---- jobs ----

    def get_job(self, job_id: str) -> JobDetail:
        return JobDetail.model_validate(self._req("GET", f"/v1/jobs/{job_id}").json())

    def cancel_job(self, job_id: str, *, force: bool = False) -> Job:
        params = {"force": "true"} if force else {}
        return Job.model_validate(
            self._req("POST", f"/v1/jobs/{job_id}:cancel", params=params).json()
        )

    def resume_job(self, job_id: str) -> Job:
        return Job.model_validate(self._req("POST", f"/v1/jobs/{job_id}:resume").json())

    def stream_events(self, job_id: str, *, last_event_id: int | None = None):
        """Yield SSE events as `dict` payloads. Generator; runs until disconnect."""
        headers: dict[str, str] = {"Accept": "text/event-stream"}
        if last_event_id is not None:
            headers["Last-Event-ID"] = str(last_event_id)
        with self._client.stream(
            "GET", f"/v1/jobs/{job_id}/events", headers=headers, timeout=None
        ) as resp:
            raise_for_response(resp)
            data: list[str] = []
            for line in resp.iter_lines():
                if line.startswith("data: "):
                    data.append(line[len("data: ") :])
                elif line == "" and data:
                    import json as _json

                    yield _json.loads("\n".join(data))
                    data = []

    # ---- reconstructions / submodels ----

    def get_reconstruction(self, recon_id: str) -> Reconstruction:
        return Reconstruction.model_validate(
            self._req("GET", f"/v1/reconstructions/{recon_id}").json()
        )

    def list_submodels(self, recon_id: str) -> list[SubModel]:
        rows = self._req("GET", f"/v1/reconstructions/{recon_id}/submodels").json()
        return [SubModel.model_validate(r) for r in rows]

    def list_snapshots(self, recon_id: str) -> list[int]:
        body = self._req("GET", f"/v1/reconstructions/{recon_id}/snapshots").json()
        return list(body.get("seqs", []))

    def read_snapshot_file(self, recon_id: str, seq: int, name: str) -> bytes:
        return self._req("GET", f"/v1/reconstructions/{recon_id}/snapshots/{seq}/{name}").content

    # ---- capabilities ----

    def capabilities(self) -> Capabilities:
        return Capabilities.model_validate(self._req("GET", "/v1/capabilities").json())

    # ---- datasets (extended) ----

    def delete_dataset(self, project_id: str, dataset_id: str) -> None:
        self._req("DELETE", f"/v1/projects/{project_id}/datasets/{dataset_id}")

    def submit_matches_split(
        self,
        dataset_id: str,
        *,
        pairs: PairsSpec | dict,
        matcher: MatcherSpec | dict,
    ) -> JobSubmitResponse:
        body = {"pairs": _spec_dict(pairs), "matcher": _spec_dict(matcher)}
        return JobSubmitResponse.model_validate(
            self._req("POST", f"/v1/datasets/{dataset_id}/matches", json=body).json()
        )

    # ---- similarity ----

    def similarity_neighbors(
        self,
        dataset_id: str,
        *,
        image_id: str,
        k: int = 5,
        strategy: str = "dhash",
        include_self: bool = False,
    ) -> dict:
        params = {
            "image_id": image_id,
            "k": k,
            "strategy": strategy,
            "include_self": str(include_self).lower(),
        }
        return self._req("GET", f"/v1/datasets/{dataset_id}/similarity", params=params).json()

    def build_similarity_index(
        self, dataset_id: str, *, strategy: str = "dhash", force: bool = True
    ) -> dict:
        params = {"strategy": strategy, "force": str(force).lower()}
        return self._req(
            "POST", f"/v1/datasets/{dataset_id}/similarity:build", params=params
        ).json()

    # ---- pose priors ----

    def get_pose_prior(self, image_id: str) -> PosePrior | None:
        body = self._req("GET", f"/v1/images/{image_id}/pose_prior").json()
        return PosePrior.model_validate(body) if body is not None else None

    def put_pose_prior(self, image_id: str, prior: PosePrior | dict) -> PosePrior:
        return PosePrior.model_validate(
            self._req("PUT", f"/v1/images/{image_id}/pose_prior", json=_spec_dict(prior)).json()
        )

    def delete_pose_prior(self, image_id: str) -> None:
        self._req("DELETE", f"/v1/images/{image_id}/pose_prior")

    def list_pose_priors(self, dataset_id: str) -> dict[str, dict]:
        return (
            self._req("GET", f"/v1/datasets/{dataset_id}/pose_priors").json().get("pose_priors", {})
        )

    def bulk_set_pose_priors(self, dataset_id: str, priors: dict[str, PosePrior | dict]) -> int:
        body = {k: _spec_dict(v) for k, v in priors.items()}
        return int(
            self._req("PUT", f"/v1/datasets/{dataset_id}/pose_priors", json=body)
            .json()
            .get("written", 0)
        )

    # ---- localize / georegister / cubemap / dense / mesh ----

    def submit_localize(
        self, recon_id: str, *, blob_sha: str, sift: dict | None = None
    ) -> JobSubmitResponse:
        body: dict[str, Any] = {"blob_sha": blob_sha}
        if sift is not None:
            body["sift"] = sift
        return JobSubmitResponse.model_validate(
            self._req("POST", f"/v1/reconstructions/{recon_id}/localize", json=body).json()
        )

    def submit_georegister(self, recon_id: str, *, sim3: Sim3 | dict) -> JobSubmitResponse:
        return JobSubmitResponse.model_validate(
            self._req(
                "POST",
                f"/v1/reconstructions/{recon_id}/georegister",
                json=_spec_dict(sim3),
            ).json()
        )

    def submit_to_cubemap(self, recon_id: str) -> JobSubmitResponse:
        return JobSubmitResponse.model_validate(
            self._req("POST", f"/v1/reconstructions/{recon_id}:to_cubemap").json()
        )

    def submit_render_cubemap(
        self, dataset_id: str, *, face_size: int | None = None
    ) -> JobSubmitResponse:
        params = {"face_size": face_size} if face_size else {}
        return JobSubmitResponse.model_validate(
            self._req("POST", f"/v1/datasets/{dataset_id}:render_cubemap", params=params).json()
        )

    def submit_dense(self, recon_id: str) -> JobSubmitResponse:
        return JobSubmitResponse.model_validate(
            self._req("POST", f"/v1/reconstructions/{recon_id}/dense").json()
        )

    def submit_mesh(
        self,
        recon_id: str,
        *,
        method: str = "poisson",
        options: dict | None = None,
    ) -> JobSubmitResponse:
        body = {"method": method, "options": options or {}}
        return JobSubmitResponse.model_validate(
            self._req("POST", f"/v1/reconstructions/{recon_id}/mesh", json=body).json()
        )

    def submit_merge_recons(
        self,
        *,
        target_recon_id: str,
        source_recon_ids: list[str],
        sim3_aligners: list[dict] | None = None,
    ) -> JobSubmitResponse:
        body = {
            "target_recon_id": target_recon_id,
            "source_recon_ids": source_recon_ids,
            "sim3_aligners": sim3_aligners,
        }
        return JobSubmitResponse.model_validate(
            self._req("POST", "/v1/reconstructions:merge", json=body).json()
        )

    # ---- ingest helpers ----

    def submit_video_frames(
        self,
        project_id: str,
        *,
        video_path: str,
        fps: float = 2.0,
        max_frames: int = 1000,
    ) -> JobSubmitResponse:
        body = {"video_path": video_path, "fps": fps, "max_frames": max_frames}
        return JobSubmitResponse.model_validate(
            self._req("POST", f"/v1/projects/{project_id}/datasets:from_video", json=body).json()
        )

    def submit_kapture_import(self, project_id: str, *, archive_path: str) -> JobSubmitResponse:
        body = {"archive_path": archive_path}
        return JobSubmitResponse.model_validate(
            self._req(
                "POST",
                f"/v1/projects/{project_id}/datasets:import_kapture",
                json=body,
            ).json()
        )

    # ---- reconstruction-level + snapshot reads ----

    def read_two_view_geometries(self, recon_id: str) -> TwoViewGeometriesFile:
        return TwoViewGeometriesFile.model_validate(
            self._req("GET", f"/v1/reconstructions/{recon_id}/two_view_geometries.json").json()
        )

    def read_correspondence_graph(self, recon_id: str) -> CorrespondenceGraphFile:
        return CorrespondenceGraphFile.model_validate(
            self._req("GET", f"/v1/reconstructions/{recon_id}/correspondence_graph.json").json()
        )

    def read_dense_index(self, recon_id: str, seq: int) -> DenseManifestFile:
        return DenseManifestFile.model_validate(
            self._req(
                "GET",
                f"/v1/reconstructions/{recon_id}/snapshots/{seq}/dense/index.json",
            ).json()
        )

    def read_dense_fused(self, recon_id: str, seq: int) -> bytes:
        return self._req(
            "GET", f"/v1/reconstructions/{recon_id}/snapshots/{seq}/dense/fused.bin"
        ).content

    def read_depth_map(self, recon_id: str, seq: int, image_name: str) -> bytes:
        return self._req(
            "GET",
            f"/v1/reconstructions/{recon_id}/snapshots/{seq}/dense/depth_maps/{image_name}.bin",
        ).content

    def read_normal_map(self, recon_id: str, seq: int, image_name: str) -> bytes:
        return self._req(
            "GET",
            f"/v1/reconstructions/{recon_id}/snapshots/{seq}/dense/normal_maps/{image_name}.bin",
        ).content

    def read_mesh_manifest(self, recon_id: str, seq: int) -> MeshFile:
        return MeshFile.model_validate(
            self._req("GET", f"/v1/reconstructions/{recon_id}/snapshots/{seq}/mesh.json").json()
        )

    def read_mesh_ply(self, recon_id: str, seq: int) -> bytes:
        return self._req("GET", f"/v1/reconstructions/{recon_id}/snapshots/{seq}/mesh.ply").content

    def get_localization_result(self, job_id: str) -> LocalizationResult:
        job = self.get_job(job_id)
        for t in job.tasks:
            if t.kind == "localize" and t.outputs_ref:
                return LocalizationResult.model_validate(t.outputs_ref)
        raise ValueError(f"job {job_id} has no completed localize task")

    # ---- meta (extended) ----

    def readyz(self) -> dict:
        return self._req("GET", "/readyz").json()

    def spec(self) -> dict:
        return self._req("GET", "/spec").json()

    def openapi(self) -> dict:
        return self._req("GET", "/openapi.json").json()

    def metrics(self) -> str:
        return self._req("GET", "/metrics").text

    # ---- projects (extended) ----

    def patch_project(self, project_id: str, patch: ProjectPatch | dict) -> Project:
        body = (
            patch.model_dump(mode="json", exclude_unset=True)
            if hasattr(patch, "model_dump")
            else patch
        )
        return Project.model_validate(
            self._req("PATCH", f"/v1/projects/{project_id}", json=body).json()
        )

    # ---- datasets (extended) ----

    def patch_dataset(
        self, project_id: str, dataset_id: str, patch: DatasetPatch | dict
    ) -> Dataset:
        body = (
            patch.model_dump(mode="json", exclude_unset=True)
            if hasattr(patch, "model_dump")
            else patch
        )
        return Dataset.model_validate(
            self._req(
                "PATCH",
                f"/v1/projects/{project_id}/datasets/{dataset_id}",
                json=body,
            ).json()
        )

    # ---- images (extended) ----

    def batch_create_images(
        self, dataset_id: str, requests: BatchCreateImagesRequest | dict
    ) -> BatchCreateImagesResponse:
        """AIP-231 batch-create. ``requests`` is a list of
        ``ImageCreate`` payloads (or a ``BatchCreateImagesRequest``)."""
        return BatchCreateImagesResponse.model_validate(
            self._req(
                "POST",
                f"/v1/datasets/{dataset_id}/images:batchCreate",
                json=_spec_dict(requests),
            ).json()
        )

    def delete_image(self, dataset_id: str, name: str) -> None:
        self._req("DELETE", f"/v1/datasets/{dataset_id}/images/{name}")

    def get_image(self, image_id: str) -> Image:
        return Image.model_validate(self._req("GET", f"/v1/images/{image_id}").json())

    def get_image_bytes(self, image_id: str, *, download: bool = False) -> bytes:
        params = {"download": "true"} if download else {}
        return self._req("GET", f"/v1/images/{image_id}/bytes", params=params).content

    def get_image_thumbnail(self, image_id: str, *, size: int | None = None) -> bytes:
        params: dict[str, Any] = {}
        if size is not None:
            params["size"] = size
        return self._req("GET", f"/v1/images/{image_id}/thumbnail", params=params).content

    def get_image_exif(self, image_id: str) -> dict:
        return self._req("GET", f"/v1/images/{image_id}/exif").json()

    # ---- uploads (extended) ----

    def get_upload(self, upload_id: str) -> Upload:
        return Upload.model_validate(self._req("GET", f"/v1/uploads/{upload_id}").json())

    # ---- reconstructions / submodels (extended) ----

    def get_submodel(self, submodel_id: str) -> SubModel:
        return SubModel.model_validate(self._req("GET", f"/v1/submodels/{submodel_id}").json())

    # ---- snapshot inspection ----

    def read_image_observations(
        self, recon_id: str, seq: int, image_id: str
    ) -> list[ImageObservation]:
        body = self._req(
            "GET",
            f"/v1/reconstructions/{recon_id}/snapshots/{seq}/images/{image_id}/observations",
        ).json()
        return [ImageObservation.model_validate(o) for o in body.get("observations", [])]

    def read_point_visibility(
        self, recon_id: str, seq: int, point3d_id: str
    ) -> list[PointObservation]:
        body = self._req(
            "GET",
            f"/v1/reconstructions/{recon_id}/snapshots/{seq}/points/{point3d_id}/visibility",
        ).json()
        return [PointObservation.model_validate(o) for o in body.get("observations", [])]

    def read_tiles_index(
        self, recon_id: str, seq: int, *, max_level: int | None = None
    ) -> TilesIndex:
        params: dict[str, Any] = {}
        if max_level is not None:
            params["max_level"] = max_level
        return TilesIndex.model_validate(
            self._req(
                "GET",
                f"/v1/reconstructions/{recon_id}/snapshots/{seq}/tiles/index.json",
                params=params,
            ).json()
        )

    def read_tile(self, recon_id: str, seq: int, level: int, x: int, y: int, z: int) -> bytes:
        return self._req(
            "GET",
            f"/v1/reconstructions/{recon_id}/snapshots/{seq}/tiles/{level}/{x}/{y}/{z}.bin",
        ).content

    # ---- admin: api keys ----

    def list_api_keys(self) -> list[ApiKey]:
        return [ApiKey.model_validate(k) for k in self._req("GET", "/v1/admin/api-keys").json()]

    def create_api_key(self, *, label: str | None = None) -> ApiKeyCreated:
        body = {"label": label} if label is not None else {}
        return ApiKeyCreated.model_validate(
            self._req("POST", "/v1/admin/api-keys", json=body).json()
        )

    def delete_api_key(self, api_key_id: str) -> None:
        self._req("DELETE", f"/v1/admin/api-keys/{api_key_id}")
