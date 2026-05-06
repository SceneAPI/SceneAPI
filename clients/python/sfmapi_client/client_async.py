"""Asynchronous SDK client. Same surface as `SfmApiClient` but `async`."""

from __future__ import annotations

import io
import json as _json
from collections.abc import AsyncIterator
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


class AsyncSfmApiClient:
    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._owned_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            headers=auth_headers(api_key),
        )

    async def __aenter__(self) -> AsyncSfmApiClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owned_client:
            await self._client.aclose()

    async def _req(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        resp = await self._client.request(method, path, **kwargs)
        raise_for_response(resp)
        return resp

    # meta -------------------------------------------------------------

    async def healthz(self) -> dict:
        r = await self._req("GET", "/healthz")
        return r.json()

    async def version(self) -> VersionResponse:
        r = await self._req("GET", "/version")
        return VersionResponse.model_validate(r.json())

    # projects ---------------------------------------------------------

    async def create_project(self, name: str, *, description: str | None = None) -> Project:
        r = await self._req("POST", "/v1/projects", json={"name": name, "description": description})
        return Project.model_validate(r.json())

    async def get_project(self, project_id: str) -> Project:
        r = await self._req("GET", f"/v1/projects/{project_id}")
        return Project.model_validate(r.json())

    async def list_projects(
        self, *, page_size: int = 50, page_token: str | None = None
    ) -> Page[Project]:
        params: dict[str, Any] = {"page_size": page_size}
        if page_token:
            params["page_token"] = page_token
        r = await self._req("GET", "/v1/projects", params=params)
        return Page[Project].model_validate(r.json())

    async def delete_project(self, project_id: str) -> None:
        await self._req("DELETE", f"/v1/projects/{project_id}")

    # uploads ----------------------------------------------------------

    async def init_upload(
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
        r = await self._req("POST", "/v1/uploads", json=body, headers=headers)
        return Upload.model_validate(r.json())

    async def patch_chunk(self, upload_id: str, *, offset: int, data: bytes) -> Upload:
        end = offset + len(data) - 1
        headers = {"Content-Range": f"bytes {offset}-{end}/{offset + len(data)}"}
        r = await self._req("PATCH", f"/v1/uploads/{upload_id}", content=data, headers=headers)
        return Upload.model_validate(r.json())

    async def finalize_upload(self, upload_id: str, *, client_sha: str | None = None) -> Upload:
        headers = {"X-Content-SHA256": client_sha} if client_sha else {}
        r = await self._req("POST", f"/v1/uploads/{upload_id}:finalize", json={}, headers=headers)
        return Upload.model_validate(r.json())

    async def upload_bytes(
        self,
        data: bytes,
        *,
        chunk_size: int = 1 * 1024 * 1024,
        content_type: str | None = None,
        idempotency_key: str | None = None,
    ) -> str:
        sha, _ = stream_sha256(io.BytesIO(data))
        upload = await self.init_upload(
            expected_size=len(data),
            expected_sha=sha,
            content_type=content_type,
            idempotency_key=idempotency_key,
        )
        offset = 0
        while offset < len(data):
            chunk = data[offset : offset + chunk_size]
            await self.patch_chunk(upload.upload_id, offset=offset, data=chunk)
            offset += len(chunk)
        await self.finalize_upload(upload.upload_id, client_sha=sha)
        return sha

    async def upload_file(self, file: BinaryIO, *, content_type: str | None = None) -> str:
        return await self.upload_bytes(file.read(), content_type=content_type)

    # datasets ---------------------------------------------------------

    async def create_dataset(
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
        r = await self._req("POST", f"/v1/projects/{project_id}/datasets", json=body)
        return Dataset.model_validate(r.json())

    async def get_dataset(self, project_id: str, dataset_id: str) -> Dataset:
        r = await self._req("GET", f"/v1/projects/{project_id}/datasets/{dataset_id}")
        return Dataset.model_validate(r.json())

    async def list_datasets(self, project_id: str) -> list[Dataset]:
        r = await self._req("GET", f"/v1/projects/{project_id}/datasets")
        return [Dataset.model_validate(x) for x in r.json()]

    async def add_image(
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
        r = await self._req("POST", f"/v1/datasets/{dataset_id}/images", json=body)
        return Image.model_validate(r.json())

    async def list_images(
        self, dataset_id: str, *, page_size: int = 100, page_token: str | None = None
    ) -> Page[Image]:
        params: dict[str, Any] = {"page_size": page_size}
        if page_token:
            params["page_token"] = page_token
        r = await self._req("GET", f"/v1/datasets/{dataset_id}/images", params=params)
        return Page[Image].model_validate(r.json())

    # SfM stages -------------------------------------------------------

    async def submit_features(
        self,
        dataset_id: str,
        *,
        spec: FeaturesSpec | dict | None = None,
    ) -> JobSubmitResponse:
        body = {
            "spec": (spec.model_dump(mode="json") if hasattr(spec, "model_dump") else (spec or {})),
        }
        r = await self._req("POST", f"/v1/datasets/{dataset_id}/features", json=body)
        return JobSubmitResponse.model_validate(r.json())

    async def submit_matches(
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
        r = await self._req("POST", f"/v1/datasets/{dataset_id}/matches", json=body)
        return JobSubmitResponse.model_validate(r.json())

    async def submit_verify(
        self,
        dataset_id: str,
        *,
        spec: VerifySpec | dict | None = None,
    ) -> JobSubmitResponse:
        body = {
            "spec": (spec.model_dump(mode="json") if hasattr(spec, "model_dump") else (spec or {})),
        }
        r = await self._req("POST", f"/v1/datasets/{dataset_id}/verify", json=body)
        return JobSubmitResponse.model_validate(r.json())

    async def run_pipeline(
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
        r = await self._req("POST", f"/v1/projects/{project_id}/pipelines/{kind}", json=body)
        return JobSubmitResponse.model_validate(r.json())

    # jobs -------------------------------------------------------------

    async def get_job(self, job_id: str) -> JobDetail:
        r = await self._req("GET", f"/v1/jobs/{job_id}")
        return JobDetail.model_validate(r.json())

    async def cancel_job(self, job_id: str, *, force: bool = False) -> Job:
        params = {"force": "true"} if force else {}
        r = await self._req("POST", f"/v1/jobs/{job_id}:cancel", params=params)
        return Job.model_validate(r.json())

    async def resume_job(self, job_id: str) -> Job:
        r = await self._req("POST", f"/v1/jobs/{job_id}:resume")
        return Job.model_validate(r.json())

    async def stream_events(
        self, job_id: str, *, last_event_id: int | None = None
    ) -> AsyncIterator[dict]:
        headers: dict[str, str] = {"Accept": "text/event-stream"}
        if last_event_id is not None:
            headers["Last-Event-ID"] = str(last_event_id)
        async with self._client.stream(
            "GET", f"/v1/jobs/{job_id}/events", headers=headers, timeout=None
        ) as resp:
            raise_for_response(resp)
            data: list[str] = []
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    data.append(line[len("data: ") :])
                elif line == "" and data:
                    yield _json.loads("\n".join(data))
                    data = []

    # reconstructions / submodels --------------------------------------

    async def get_reconstruction(self, recon_id: str) -> Reconstruction:
        r = await self._req("GET", f"/v1/reconstructions/{recon_id}")
        return Reconstruction.model_validate(r.json())

    async def list_submodels(self, recon_id: str) -> list[SubModel]:
        r = await self._req("GET", f"/v1/reconstructions/{recon_id}/submodels")
        return [SubModel.model_validate(x) for x in r.json()]

    async def list_snapshots(self, recon_id: str) -> list[int]:
        r = await self._req("GET", f"/v1/reconstructions/{recon_id}/snapshots")
        return list(r.json().get("seqs", []))

    # capabilities -----------------------------------------------------

    async def capabilities(self) -> Capabilities:
        r = await self._req("GET", "/v1/capabilities")
        return Capabilities.model_validate(r.json())

    # datasets (extended) ----------------------------------------------

    async def delete_dataset(self, project_id: str, dataset_id: str) -> None:
        await self._req("DELETE", f"/v1/projects/{project_id}/datasets/{dataset_id}")

    async def submit_matches_split(
        self,
        dataset_id: str,
        *,
        pairs: PairsSpec | dict,
        matcher: MatcherSpec | dict,
    ) -> JobSubmitResponse:
        """New-style match submission with separate ``pairs`` and
        ``matcher`` sub-specs (capabilities ``pairs.{strategy}`` and
        ``matchers.{type}``)."""
        body = {"pairs": _spec_dict(pairs), "matcher": _spec_dict(matcher)}
        r = await self._req("POST", f"/v1/datasets/{dataset_id}/matches", json=body)
        return JobSubmitResponse.model_validate(r.json())

    # similarity -------------------------------------------------------

    async def similarity_neighbors(
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
        r = await self._req("GET", f"/v1/datasets/{dataset_id}/similarity", params=params)
        return r.json()

    async def build_similarity_index(
        self, dataset_id: str, *, strategy: str = "dhash", force: bool = True
    ) -> dict:
        params = {"strategy": strategy, "force": str(force).lower()}
        r = await self._req("POST", f"/v1/datasets/{dataset_id}/similarity:build", params=params)
        return r.json()

    # pose priors ------------------------------------------------------

    async def get_pose_prior(self, image_id: str) -> PosePrior | None:
        r = await self._req("GET", f"/v1/images/{image_id}/pose_prior")
        body = r.json()
        return PosePrior.model_validate(body) if body is not None else None

    async def put_pose_prior(self, image_id: str, prior: PosePrior | dict) -> PosePrior:
        r = await self._req("PUT", f"/v1/images/{image_id}/pose_prior", json=_spec_dict(prior))
        return PosePrior.model_validate(r.json())

    async def delete_pose_prior(self, image_id: str) -> None:
        await self._req("DELETE", f"/v1/images/{image_id}/pose_prior")

    async def list_pose_priors(self, dataset_id: str) -> dict[str, dict]:
        r = await self._req("GET", f"/v1/datasets/{dataset_id}/pose_priors")
        return r.json().get("pose_priors", {})

    async def bulk_set_pose_priors(
        self, dataset_id: str, priors: dict[str, PosePrior | dict]
    ) -> int:
        body = {k: _spec_dict(v) for k, v in priors.items()}
        r = await self._req("PUT", f"/v1/datasets/{dataset_id}/pose_priors", json=body)
        return int(r.json().get("written", 0))

    # localize / georegister / cubemap / dense / mesh ------------------

    async def submit_localize(
        self, recon_id: str, *, blob_sha: str, sift: dict | None = None
    ) -> JobSubmitResponse:
        body: dict[str, Any] = {"blob_sha": blob_sha}
        if sift is not None:
            body["sift"] = sift
        r = await self._req("POST", f"/v1/reconstructions/{recon_id}/localize", json=body)
        return JobSubmitResponse.model_validate(r.json())

    async def submit_georegister(self, recon_id: str, *, sim3: Sim3 | dict) -> JobSubmitResponse:
        r = await self._req(
            "POST",
            f"/v1/reconstructions/{recon_id}/georegister",
            json=_spec_dict(sim3),
        )
        return JobSubmitResponse.model_validate(r.json())

    async def submit_to_cubemap(self, recon_id: str) -> JobSubmitResponse:
        r = await self._req("POST", f"/v1/reconstructions/{recon_id}:to_cubemap")
        return JobSubmitResponse.model_validate(r.json())

    async def submit_render_cubemap(
        self, dataset_id: str, *, face_size: int | None = None
    ) -> JobSubmitResponse:
        params = {"face_size": face_size} if face_size else {}
        r = await self._req("POST", f"/v1/datasets/{dataset_id}:render_cubemap", params=params)
        return JobSubmitResponse.model_validate(r.json())

    async def submit_dense(self, recon_id: str) -> JobSubmitResponse:
        r = await self._req("POST", f"/v1/reconstructions/{recon_id}/dense")
        return JobSubmitResponse.model_validate(r.json())

    async def submit_mesh(
        self,
        recon_id: str,
        *,
        method: str = "poisson",
        options: dict | None = None,
    ) -> JobSubmitResponse:
        body = {"method": method, "options": options or {}}
        r = await self._req("POST", f"/v1/reconstructions/{recon_id}/mesh", json=body)
        return JobSubmitResponse.model_validate(r.json())

    async def submit_merge_recons(
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
        r = await self._req("POST", "/v1/reconstructions:merge", json=body)
        return JobSubmitResponse.model_validate(r.json())

    # ingest helpers ---------------------------------------------------

    async def submit_video_frames(
        self,
        project_id: str,
        *,
        video_path: str,
        fps: float = 2.0,
        max_frames: int = 1000,
    ) -> JobSubmitResponse:
        body = {"video_path": video_path, "fps": fps, "max_frames": max_frames}
        r = await self._req("POST", f"/v1/projects/{project_id}/datasets:from_video", json=body)
        return JobSubmitResponse.model_validate(r.json())

    async def submit_kapture_import(
        self, project_id: str, *, archive_path: str
    ) -> JobSubmitResponse:
        body = {"archive_path": archive_path}
        r = await self._req("POST", f"/v1/projects/{project_id}/datasets:import_kapture", json=body)
        return JobSubmitResponse.model_validate(r.json())

    # reconstruction-level reads ---------------------------------------

    async def read_two_view_geometries(self, recon_id: str) -> TwoViewGeometriesFile:
        r = await self._req("GET", f"/v1/reconstructions/{recon_id}/two_view_geometries.json")
        return TwoViewGeometriesFile.model_validate(r.json())

    async def read_correspondence_graph(self, recon_id: str) -> CorrespondenceGraphFile:
        r = await self._req("GET", f"/v1/reconstructions/{recon_id}/correspondence_graph.json")
        return CorrespondenceGraphFile.model_validate(r.json())

    # snapshot-level dense / mesh reads --------------------------------

    async def read_dense_index(self, recon_id: str, seq: int) -> DenseManifestFile:
        r = await self._req(
            "GET",
            f"/v1/reconstructions/{recon_id}/snapshots/{seq}/dense/index.json",
        )
        return DenseManifestFile.model_validate(r.json())

    async def read_dense_fused(self, recon_id: str, seq: int) -> bytes:
        r = await self._req(
            "GET", f"/v1/reconstructions/{recon_id}/snapshots/{seq}/dense/fused.bin"
        )
        return r.content

    async def read_depth_map(self, recon_id: str, seq: int, image_name: str) -> bytes:
        r = await self._req(
            "GET",
            f"/v1/reconstructions/{recon_id}/snapshots/{seq}/dense/depth_maps/{image_name}.bin",
        )
        return r.content

    async def read_normal_map(self, recon_id: str, seq: int, image_name: str) -> bytes:
        r = await self._req(
            "GET",
            f"/v1/reconstructions/{recon_id}/snapshots/{seq}/dense/normal_maps/{image_name}.bin",
        )
        return r.content

    async def read_mesh_manifest(self, recon_id: str, seq: int) -> MeshFile:
        r = await self._req("GET", f"/v1/reconstructions/{recon_id}/snapshots/{seq}/mesh.json")
        return MeshFile.model_validate(r.json())

    async def read_mesh_ply(self, recon_id: str, seq: int) -> bytes:
        r = await self._req("GET", f"/v1/reconstructions/{recon_id}/snapshots/{seq}/mesh.ply")
        return r.content

    async def get_localization_result(self, job_id: str) -> LocalizationResult:
        """Poll a localize job and decode the task output as a
        :class:`LocalizationResult`."""
        job = await self.get_job(job_id)
        for t in job.tasks:
            if t.kind == "localize" and t.outputs_ref:
                return LocalizationResult.model_validate(t.outputs_ref)
        raise ValueError(f"job {job_id} has no completed localize task")

    async def read_snapshot_file(self, recon_id: str, seq: int, name: str) -> bytes:
        r = await self._req("GET", f"/v1/reconstructions/{recon_id}/snapshots/{seq}/{name}")
        return r.content

    # ---- meta (extended) ----

    async def readyz(self) -> dict:
        r = await self._req("GET", "/readyz")
        return r.json()

    async def spec(self) -> dict:
        r = await self._req("GET", "/spec")
        return r.json()

    async def openapi(self) -> dict:
        r = await self._req("GET", "/openapi.json")
        return r.json()

    async def metrics(self) -> str:
        r = await self._req("GET", "/metrics")
        return r.text

    # ---- projects (extended) ----

    async def patch_project(self, project_id: str, patch: ProjectPatch | dict) -> Project:
        body = (
            patch.model_dump(mode="json", exclude_unset=True)
            if hasattr(patch, "model_dump")
            else patch
        )
        r = await self._req("PATCH", f"/v1/projects/{project_id}", json=body)
        return Project.model_validate(r.json())

    # ---- datasets (extended) ----

    async def patch_dataset(
        self, project_id: str, dataset_id: str, patch: DatasetPatch | dict
    ) -> Dataset:
        body = (
            patch.model_dump(mode="json", exclude_unset=True)
            if hasattr(patch, "model_dump")
            else patch
        )
        r = await self._req(
            "PATCH",
            f"/v1/projects/{project_id}/datasets/{dataset_id}",
            json=body,
        )
        return Dataset.model_validate(r.json())

    # ---- images (extended) ----

    async def batch_create_images(
        self, dataset_id: str, requests: BatchCreateImagesRequest | dict
    ) -> BatchCreateImagesResponse:
        """AIP-231 batch-create. ``requests`` is a list of
        ``ImageCreate`` payloads (or a ``BatchCreateImagesRequest``)."""
        r = await self._req(
            "POST",
            f"/v1/datasets/{dataset_id}/images:batchCreate",
            json=_spec_dict(requests),
        )
        return BatchCreateImagesResponse.model_validate(r.json())

    async def delete_image(self, dataset_id: str, name: str) -> None:
        await self._req("DELETE", f"/v1/datasets/{dataset_id}/images/{name}")

    async def get_image(self, image_id: str) -> Image:
        r = await self._req("GET", f"/v1/images/{image_id}")
        return Image.model_validate(r.json())

    async def get_image_bytes(self, image_id: str, *, download: bool = False) -> bytes:
        params = {"download": "true"} if download else {}
        r = await self._req("GET", f"/v1/images/{image_id}/bytes", params=params)
        return r.content

    async def get_image_thumbnail(self, image_id: str, *, size: int | None = None) -> bytes:
        params: dict[str, Any] = {}
        if size is not None:
            params["size"] = size
        r = await self._req("GET", f"/v1/images/{image_id}/thumbnail", params=params)
        return r.content

    async def get_image_exif(self, image_id: str) -> dict:
        r = await self._req("GET", f"/v1/images/{image_id}/exif")
        return r.json()

    # ---- uploads (extended) ----

    async def get_upload(self, upload_id: str) -> Upload:
        r = await self._req("GET", f"/v1/uploads/{upload_id}")
        return Upload.model_validate(r.json())

    # ---- reconstructions / submodels (extended) ----

    async def get_submodel(self, submodel_id: str) -> SubModel:
        r = await self._req("GET", f"/v1/submodels/{submodel_id}")
        return SubModel.model_validate(r.json())

    # ---- snapshot inspection (observations / visibility / tiles) ----

    async def read_image_observations(
        self, recon_id: str, seq: int, image_id: str
    ) -> list[ImageObservation]:
        r = await self._req(
            "GET",
            f"/v1/reconstructions/{recon_id}/snapshots/{seq}/images/{image_id}/observations",
        )
        body = r.json()
        return [ImageObservation.model_validate(o) for o in body.get("observations", [])]

    async def read_point_visibility(
        self, recon_id: str, seq: int, point3d_id: str
    ) -> list[PointObservation]:
        r = await self._req(
            "GET",
            f"/v1/reconstructions/{recon_id}/snapshots/{seq}/points/{point3d_id}/visibility",
        )
        body = r.json()
        return [PointObservation.model_validate(o) for o in body.get("observations", [])]

    async def read_tiles_index(
        self, recon_id: str, seq: int, *, max_level: int | None = None
    ) -> TilesIndex:
        params: dict[str, Any] = {}
        if max_level is not None:
            params["max_level"] = max_level
        r = await self._req(
            "GET",
            f"/v1/reconstructions/{recon_id}/snapshots/{seq}/tiles/index.json",
            params=params,
        )
        return TilesIndex.model_validate(r.json())

    async def read_tile(self, recon_id: str, seq: int, level: int, x: int, y: int, z: int) -> bytes:
        r = await self._req(
            "GET",
            f"/v1/reconstructions/{recon_id}/snapshots/{seq}/tiles/{level}/{x}/{y}/{z}.bin",
        )
        return r.content

    # ---- admin: api keys ----

    async def list_api_keys(self) -> list[ApiKey]:
        r = await self._req("GET", "/v1/admin/api-keys")
        return [ApiKey.model_validate(k) for k in r.json()]

    async def create_api_key(self, *, label: str | None = None) -> ApiKeyCreated:
        body = {"label": label} if label is not None else {}
        r = await self._req("POST", "/v1/admin/api-keys", json=body)
        return ApiKeyCreated.model_validate(r.json())

    async def delete_api_key(self, api_key_id: str) -> None:
        await self._req("DELETE", f"/v1/admin/api-keys/{api_key_id}")
