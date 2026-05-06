"""``ColmapModBackend`` — reference :class:`SfmBackend` implementation.

Wraps pycolmap (the colmap_mod fork). Every method here is a thin
delegation to the engine + a normalization to the sfmapi wire
contract. Worker tasks **MUST NOT** import pycolmap themselves; they
call this backend's methods through ``get_backend()``.

When pycolmap is unavailable, methods raise
:class:`app.core.errors.PycolmapUnavailableError` (a subclass of
:class:`CapabilityUnavailableError` so the wire still reports the
``capability`` name and ``501``).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from app.adapters import colmap_adapter
from app.core.errors import CapabilityUnavailableError


class ColmapModBackend:
    """Default backend, powered by the colmap_mod / pycolmap engine."""

    name = "colmap_mod"
    vendor = "ETH3D / sfmapi"

    @property
    def version(self) -> str:
        return self.runtime_versions().get("pycolmap_version", "unknown")

    def capabilities(self) -> set[str]:
        from app.core.config import get_settings

        if not get_settings().pycolmap_available:
            return set()
        caps = {
            "features.extract",
            "features.extract.sift",
            "matches.exhaustive",
            "matches.sequential",
            "matches.spatial",
            "matches.vocabtree",
            "matches.verify",
            "pairs.exhaustive",
            "pairs.sequential",
            "pairs.spatial",
            "pairs.vocabtree",
            "matchers.nn-mutual",
            "matchers.nn-ratio",
            "map.incremental",
            "map.global",
            "map.hierarchical",
            "map.spherical",
            "ba.standard",
            "ba.two_stage",
            "triangulate.retri",
            "relocalize.images",
            "pgo.optimize",
            "export.ply",
            "export.nvm",
            "export.colmap_text",
            "export.colmap_bin",
            # Sparse-export to modern formats — pure-Python emitters
            # in app.adapters.export_formats; always available.
            "export.nerfstudio",
            "export.gaussian_splatting",
            "export.instant_ngp",
            "export.kapture",
            "dense.patch_match_stereo",
            "dense.stereo_fusion",
            "similarity.vlad",
            "localize.from_memory",
            "localize.batch",
            "georegister.sim3",
            "spherical.to_cubemap",
            "spherical.render_cubemap",
            "recon.merge",
        }
        try:
            import pycolmap as _pc

            if hasattr(_pc, "GpuAlikedOptions") or hasattr(_pc, "extract_aliked"):
                caps.add("features.extract.aliked")
            if hasattr(_pc, "poisson_meshing") or hasattr(_pc, "PoissonMeshing"):
                caps.add("mesh.poisson")
            if hasattr(_pc, "delaunay_meshing") or hasattr(_pc, "DelaunayMeshing"):
                caps.add("mesh.delaunay")
        except Exception:
            pass
        return caps

    # ---- feature pipeline ----------------------------------------------

    def extract_features(
        self,
        *,
        database_path: Path,
        image_root: Path,
        image_list: list[str],
        options: dict,
    ) -> dict:
        return colmap_adapter.extract_features_into_db(
            database_path=database_path,
            image_root=image_root,
            image_list=image_list,
            options=options,
        )

    def match(self, *, database_path: Path, mode: str, options: dict) -> dict:
        return colmap_adapter.match_in_db(database_path=database_path, mode=mode, options=options)

    def verify_matches(self, *, database_path: Path, options: dict) -> dict:
        return colmap_adapter.verify_matches(database_path=database_path, options=options)

    # ---- DB walkers ----------------------------------------------------

    def iter_two_view_geometries(self, *, database_path: Path) -> Iterator[tuple[int, int, Any]]:
        from app.storage.two_view_emit import iter_database_pairs

        pc = colmap_adapter._require_pycolmap()
        with pc.Database(str(database_path)) as db:
            yield from iter_database_pairs(db)

    def iter_correspondences(self, *, database_path: Path) -> Iterator[tuple[int, int, Any]]:
        from app.storage.correspondence_emit import iter_database_correspondences

        pc = colmap_adapter._require_pycolmap()
        with pc.Database(str(database_path)) as db:
            yield from iter_database_correspondences(db)

    # ---- refinement ----------------------------------------------------

    def bundle_adjustment(self, *, model_path: Path, output_path: Path, spec: dict) -> dict:
        from app.storage.snapshot_emit import emit_snapshot_files

        pc = colmap_adapter._require_pycolmap()
        rec = pc.Reconstruction()
        rec.read(str(model_path))
        opts = pc.BundleAdjustmentOptions()
        for k, v in (spec or {}).items():
            if k == "mode":
                continue
            if hasattr(opts, k):
                setattr(opts, k, v)
        mode = (spec.get("mode") or "standard").lower()
        if mode == "two_stage":
            fn = getattr(pc, "two_stage_bundle_adjustment", None)
            if fn is None:
                raise CapabilityUnavailableError(
                    capability="ba.two_stage",
                    reason="pycolmap does not expose two_stage_bundle_adjustment",
                )
            fn(rec, options=opts)
        else:
            pc.bundle_adjustment(rec, options=opts)
        rec.write(str(output_path))
        emit_snapshot_files(rec, output_path)
        return {
            "model_path": str(output_path),
            "mode": mode,
            "num_reg_images": rec.num_reg_images,
            "num_points3D": rec.num_points3D,
        }

    def triangulate(
        self,
        *,
        model_path: Path,
        database_path: Path,
        image_root: Path,
        output_path: Path,
    ) -> dict:
        from app.storage.snapshot_emit import emit_snapshot_files

        pc = colmap_adapter._require_pycolmap()
        rec = pc.Reconstruction()
        rec.read(str(model_path))
        pc.triangulate_points(
            reconstruction=rec,
            database_path=str(database_path),
            image_path=str(image_root),
            output_path=str(output_path),
        )
        emit_snapshot_files(rec, output_path)
        return {"model_path": str(output_path), "num_points3D": rec.num_points3D}

    def relocalize(
        self,
        *,
        model_path: Path,
        database_path: Path,
        image_root: Path,
        output_path: Path,
        image_ids: list[int],
    ) -> dict:
        from app.storage.snapshot_emit import emit_snapshot_files

        pc = colmap_adapter._require_pycolmap()
        rec = pc.Reconstruction()
        rec.read(str(model_path))
        pc.relocalize_images(
            reconstruction=rec,
            database_path=str(database_path),
            image_path=str(image_root),
            image_ids=image_ids,
        )
        rec.write(str(output_path))
        emit_snapshot_files(rec, output_path)
        return {
            "model_path": str(output_path),
            "num_reg_images": rec.num_reg_images,
        }

    def pose_graph_optimize(self, *, model_path: Path, output_path: Path, spec: dict) -> dict:
        from app.storage.snapshot_emit import emit_snapshot_files

        pc = colmap_adapter._require_pycolmap()
        rec = pc.Reconstruction()
        rec.read(str(model_path))
        opts = pc.PoseGraphOptimizationOptions()
        for k, v in (spec or {}).items():
            if hasattr(opts, k):
                setattr(opts, k, v)
        pc.optimize_pose_graph(rec, options=opts)
        rec.write(str(output_path))
        emit_snapshot_files(rec, output_path)
        return {"model_path": str(output_path)}

    # ---- catch-alls: dense / vlad_index / localize still call the
    # lower-level adapter directly. Migration is purely additive.

    def run_mapping(
        self,
        *,
        kind: str,
        db_path: Path,
        image_root: Path,
        sparse_root: Path,
        job_dir: Path,
        spec: dict,
        pose_priors: dict | None = None,
    ) -> tuple[list[dict], list[Any]]:
        from app.core.errors import ValidationError

        pc = colmap_adapter._require_pycolmap()
        if kind == "incremental":
            recs = _run_incremental(
                pc=pc,
                spec=spec,
                db_path=db_path,
                image_root=image_root,
                sparse_root=sparse_root,
                job_dir=job_dir,
                pose_priors=pose_priors or {},
            )
        elif kind == "global":
            opts = pc.GlobalPipelineOptions() if hasattr(pc, "GlobalPipelineOptions") else None
            recs = pc.global_mapper(
                database_path=str(db_path),
                image_path=str(image_root),
                output_path=str(sparse_root),
                options=opts,
            )
        elif kind == "hierarchical":
            opts = (
                pc.HierarchicalPipelineOptions()
                if hasattr(pc, "HierarchicalPipelineOptions")
                else None
            )
            recs = pc.hierarchical_mapper(
                database_path=str(db_path),
                image_path=str(image_root),
                output_path=str(sparse_root),
                options=opts,
            )
        elif kind == "spherical":
            recs = pc.panorama_mapping(
                database_path=str(db_path),
                image_path=str(image_root),
                output_path=str(sparse_root),
            )
        else:
            raise ValidationError(f"Unknown mapping kind: {kind!r}")

        rec_list = list(recs or [])
        out: list[dict] = []
        for idx, rec in enumerate(rec_list):
            out.append(
                {
                    "idx": idx,
                    "num_reg_images": _num_reg_images(rec),
                    "num_points3D": _num_points3D(rec),
                }
            )
        return out, rec_list

    def export(self, *, model_path: Path, output_path: Path, format: str) -> dict:
        from app.adapters.export_formats import (
            export_gaussian_splatting,
            export_instant_ngp,
            export_kapture,
            export_nerfstudio,
        )

        pc = colmap_adapter._require_pycolmap()
        rec = pc.Reconstruction()
        rec.read(str(model_path))
        if format == "ply":
            rec.export_PLY(str(output_path))
        elif format == "nvm":
            rec.export_NVM(str(output_path))
        elif format == "colmap_text":
            output_path.mkdir(parents=True, exist_ok=True)
            rec.write_text(str(output_path))
        elif format == "colmap_bin":
            output_path.mkdir(parents=True, exist_ok=True)
            rec.write_binary(str(output_path))
        elif format == "nerfstudio":
            output_path.mkdir(parents=True, exist_ok=True)
            export_nerfstudio(rec, output_path)
        elif format == "gaussian_splatting":
            output_path.mkdir(parents=True, exist_ok=True)
            export_gaussian_splatting(rec, output_path)
        elif format == "instant_ngp":
            output_path.mkdir(parents=True, exist_ok=True)
            export_instant_ngp(rec, output_path)
        elif format == "kapture":
            output_path.mkdir(parents=True, exist_ok=True)
            export_kapture(rec, output_path)
        else:
            raise CapabilityUnavailableError(
                capability=f"export.{format}",
                reason=f"unknown export format: {format!r}",
            )
        return {"output_path": str(output_path), "format": format}

    def generate_mesh(
        self,
        *,
        sparse_dir: Path,
        dense_fused_path: Path | None,
        output_path: Path,
        method: str,
        options: dict,
    ) -> dict:
        pc = colmap_adapter._require_pycolmap()
        if method == "poisson":
            fn = getattr(pc, "poisson_meshing", None) or getattr(pc, "PoissonMeshing", None)
            cap = "mesh.poisson"
        elif method == "delaunay":
            fn = getattr(pc, "delaunay_meshing", None) or getattr(pc, "DelaunayMeshing", None)
            cap = "mesh.delaunay"
        else:
            raise CapabilityUnavailableError(
                capability=f"mesh.{method}",
                reason=f"unknown mesh method: {method!r}",
            )
        if fn is None:
            raise CapabilityUnavailableError(
                capability=cap,
                reason=f"pycolmap does not expose {method} meshing",
            )
        # Poisson wants the dense cloud; Delaunay can use the sparse
        # workspace (which is the sparse_dir).
        input_path = (
            str(dense_fused_path)
            if (method == "poisson" and dense_fused_path is not None)
            else str(sparse_dir)
        )
        try:
            fn(input_path=input_path, output_path=str(output_path))
        except TypeError:
            try:
                fn(input_path, str(output_path))
            except Exception as e:
                raise CapabilityUnavailableError(
                    capability=cap,
                    reason=f"could not call {method} meshing: {e}",
                ) from e
        return _summarize_mesh_ply(output_path, method=method)

    def convert_spherical_to_cubemap(
        self,
        *,
        input_model_path: Path,
        input_image_path: Path,
        output_path: Path,
    ) -> dict:
        pc = colmap_adapter._require_pycolmap()
        fn = getattr(pc, "convert_spherical_reconstruction_to_cubemap", None)
        if fn is None:
            raise CapabilityUnavailableError(
                capability="spherical.to_cubemap",
                reason="pycolmap does not expose convert_spherical_reconstruction_to_cubemap",
            )
        attempts: list[dict[str, Any]] = [
            {
                "input_model_path": str(input_model_path),
                "input_image_path": str(input_image_path),
                "output_path": str(output_path),
            },
            {
                "input_path": str(input_model_path),
                "image_path": str(input_image_path),
                "output_path": str(output_path),
            },
        ]
        last_err: Exception | None = None
        for kwargs in attempts:
            try:
                fn(**kwargs)
                return {"output_path": str(output_path)}
            except TypeError as e:
                last_err = e
                continue
        try:
            fn(str(input_model_path), str(input_image_path), str(output_path))
            return {"output_path": str(output_path)}
        except Exception as e:
            raise CapabilityUnavailableError(
                capability="spherical.to_cubemap",
                reason=f"could not call converter: {last_err or e}",
            ) from (last_err or e)

    def render_spherical_cubemap_images(
        self,
        *,
        input_image_path: Path,
        output_path: Path,
        face_size: int | None = None,
    ) -> dict:
        pc = colmap_adapter._require_pycolmap()
        fn = getattr(pc, "render_spherical_cubemap_images", None)
        if fn is None:
            raise CapabilityUnavailableError(
                capability="spherical.render_cubemap",
                reason="pycolmap does not expose render_spherical_cubemap_images",
            )
        kwargs: dict[str, Any] = {
            "input_image_path": str(input_image_path),
            "output_path": str(output_path),
        }
        if face_size:
            kwargs["face_size"] = int(face_size)
        try:
            fn(**kwargs)
        except TypeError:
            try:
                if face_size:
                    fn(str(input_image_path), str(output_path), int(face_size))
                else:
                    fn(str(input_image_path), str(output_path))
            except Exception as e:
                raise CapabilityUnavailableError(
                    capability="spherical.render_cubemap",
                    reason=f"could not call renderer: {e}",
                ) from e
        return {"output_path": str(output_path), "face_size": face_size}

    def dense_pipeline(
        self,
        *,
        sparse_dir: Path,
        image_root: Path,
        workspace: Path,
        out_dir: Path,
        spec: dict,
    ) -> dict:
        from app.schemas.api.scene import DenseManifestFile, DenseSummary
        from app.storage._atomic import write_bytes as _atomic_write_bytes
        from app.storage._atomic import write_text as _atomic_write_text
        from app.storage.snapshot_emit import emit_snapshot_files

        pc = colmap_adapter._require_pycolmap()
        undistort = _resolve_pycolmap_callable(pc, ["undistort_images"])
        pms = _resolve_pycolmap_callable(pc, ["patch_match_stereo"])
        sfusion = _resolve_pycolmap_callable(pc, ["stereo_fusion"])

        undistort(
            image_path=str(image_root),
            input_path=str(sparse_dir),
            output_path=str(workspace),
        )
        pm_opts = pc.PatchMatchOptions() if hasattr(pc, "PatchMatchOptions") else None
        for k, v in ((spec or {}).get("patch_match") or {}).items():
            if pm_opts is not None and hasattr(pm_opts, k):
                setattr(pm_opts, k, v)
        if pm_opts is not None:
            pms(workspace_path=str(workspace), options=pm_opts)
        else:
            pms(workspace_path=str(workspace))

        fused_ply = workspace / "fused.ply"
        sf_opts = pc.StereoFusionOptions() if hasattr(pc, "StereoFusionOptions") else None
        for k, v in ((spec or {}).get("stereo_fusion") or {}).items():
            if sf_opts is not None and hasattr(sf_opts, k):
                setattr(sf_opts, k, v)
        if sf_opts is not None:
            sfusion(output_path=str(fused_ply), workspace_path=str(workspace), options=sf_opts)
        else:
            sfusion(output_path=str(fused_ply), workspace_path=str(workspace))

        # Sparse model emit (cameras.json / images.json / ...).
        rec = pc.Reconstruction()
        rec.read(str(sparse_dir))
        emit_snapshot_files(rec, out_dir)

        dense_dir = out_dir / "dense"
        dense_dir.mkdir(parents=True, exist_ok=True)

        # Convert PLY → points.bin.
        fused_points = 0
        if fused_ply.is_file():
            try:
                blob, fused_points = _ply_to_points_bin(fused_ply)
                _atomic_write_bytes(dense_dir / "fused.bin", blob)
            except Exception:
                pass

        # Convert COLMAP depth/normal binaries.
        depth_entries, normal_count = _convert_colmap_depth_maps(workspace / "stereo", dense_dir)

        summary = DenseSummary(
            num_images=len(depth_entries),
            num_depth_maps=len(depth_entries),
            num_normal_maps=normal_count,
            fused_points=fused_points,
        )
        manifest = DenseManifestFile(summary=summary, depth_maps=depth_entries)
        _atomic_write_text(
            dense_dir / "index.json",
            manifest.model_dump_json(by_alias=True, indent=2),
        )
        return {"num_depth_maps": len(depth_entries), "fused_points": fused_points}

    def build_vlad_index(
        self, *, image_paths_by_id: dict[str, Path], spec: dict
    ) -> tuple[list[str], Any]:
        import numpy as np

        pc = colmap_adapter._require_pycolmap()
        if not hasattr(pc, "IncrementalVLADIndex"):
            raise CapabilityUnavailableError(
                capability="similarity.vlad",
                reason="pycolmap does not expose IncrementalVLADIndex",
            )

        sift_options = pc.SiftExtractionOptions()
        for k, v in ((spec or {}).get("sift") or {}).items():
            if hasattr(sift_options, k):
                setattr(sift_options, k, v)
        sift = pc.Sift(options=sift_options)

        index = pc.IncrementalVLADIndex(
            descriptor_type=spec.get("descriptor_type", "vlad"),
            num_vlad_clusters=int(spec.get("num_vlad_clusters", 32)),
            num_vlad_training_samples=int(spec.get("num_vlad_training_samples", 200)),
            num_vlad_kmeans_iters=int(spec.get("num_vlad_kmeans_iters", 10)),
            max_vlad_training_points=int(spec.get("max_vlad_training_points", 0)),
        )

        sift_results: list[Any] = []
        pycolmap_ids: list[int] = []
        sfmapi_ids: list[str] = []
        for idx, (sfmapi_id, path) in enumerate(image_paths_by_id.items(), start=1):
            if not path.is_file():
                continue
            try:
                sift_results.append(sift.extract(str(path)))
            except Exception:
                continue
            pycolmap_ids.append(idx)
            sfmapi_ids.append(sfmapi_id)

        if not sift_results:
            return sfmapi_ids, np.zeros((0, 0), dtype=np.float32)

        index.add_batch(pycolmap_ids, sift_results)
        vectors = np.asarray(index._descriptors, dtype=np.float32)
        return sfmapi_ids, vectors

    def localize_from_memory(self, *, sparse_dir: Path, query_image: Path, spec: dict) -> dict:
        from app.storage.snapshot_emit import _quat_xyzw_to_rotation

        pc = colmap_adapter._require_pycolmap()
        if not hasattr(pc, "localize_from_memory"):
            raise CapabilityUnavailableError(
                capability="localize.from_memory",
                reason="pycolmap does not expose localize_from_memory",
            )
        if not sparse_dir.is_dir():
            return _localize_failure(f"sparse dir does not exist: {sparse_dir}")
        rec = pc.Reconstruction()
        try:
            rec.read(str(sparse_dir))
        except Exception as e:
            return _localize_failure(f"failed to read reconstruction: {e}")

        sift_options = pc.SiftExtractionOptions()
        for k, v in ((spec or {}).get("sift") or {}).items():
            if hasattr(sift_options, k):
                setattr(sift_options, k, v)
        sift = pc.Sift(options=sift_options)
        try:
            query_sift = sift.extract(str(query_image))
        except Exception as e:
            return _localize_failure(f"sift extract failed: {e}")

        try:
            result = pc.localize_from_memory(reconstruction=rec, query_sift_results=[query_sift])
        except TypeError:
            try:
                result = pc.localize_from_memory(rec, [query_sift])
            except Exception as e:
                return _localize_failure(f"localize_from_memory failed: {e}")
        except Exception as e:
            return _localize_failure(f"localize_from_memory failed: {e}")

        poses = list(result) if result else []
        if not poses:
            return _localize_failure("no pose returned")
        pose = poses[0]
        success = bool(getattr(pose, "success", True))
        cam_from_world = getattr(pose, "cam_from_world", None) or getattr(pose, "pose", None)
        inlier_matches: list[tuple[int, int]] = []
        raw_inliers = getattr(pose, "inlier_matches", None)
        if raw_inliers is not None:
            for kp, p3d in raw_inliers:
                inlier_matches.append((int(kp), int(p3d)))
        num_inliers = int(getattr(pose, "num_inliers", len(inlier_matches)))

        cfw_dict = None
        if cam_from_world is not None:
            rot = _quat_xyzw_to_rotation(cam_from_world.rotation.quat)
            t = cam_from_world.translation
            cfw_dict = {
                "rotation": {"w": rot.w, "x": rot.x, "y": rot.y, "z": rot.z},
                "translation": [float(t[0]), float(t[1]), float(t[2])],
            }
        return {
            "success": success,
            "cam_from_world": cfw_dict,
            "num_inliers": num_inliers,
            "inlier_matches": inlier_matches,
            "diagnostics": {"query_image": str(query_image), "sparse_dir": str(sparse_dir)},
        }

    def apply_sim3(self, *, model_path: Path, output_path: Path, sim3: dict) -> dict:
        from app.storage.snapshot_emit import emit_snapshot_files

        pc = colmap_adapter._require_pycolmap()
        rec = pc.Reconstruction()
        rec.read(str(model_path))

        rot = sim3["rotation"]
        rot_xyzw = [
            float(rot["x"]),
            float(rot["y"]),
            float(rot["z"]),
            float(rot["w"]),
        ]
        t = list(sim3["translation"])
        scale = float(sim3["scale"])
        if not hasattr(pc, "Sim3d"):
            raise CapabilityUnavailableError(
                capability="georegister.sim3",
                reason="pycolmap does not expose Sim3d",
            )
        rotation = pc.Rotation3d(rot_xyzw) if hasattr(pc, "Rotation3d") else rot_xyzw
        try:
            similarity = pc.Sim3d(scale=scale, rotation=rotation, translation=t)
        except TypeError:
            similarity = pc.Sim3d(scale, rotation, t)

        if hasattr(rec, "transform"):
            rec.transform(similarity)
        elif hasattr(pc, "transform_reconstruction"):
            pc.transform_reconstruction(rec, similarity)
        else:
            raise CapabilityUnavailableError(
                capability="georegister.sim3",
                reason="pycolmap exposes neither Reconstruction.transform nor "
                "transform_reconstruction",
            )

        rec.write(str(output_path))
        emit_snapshot_files(rec, output_path)
        return {"output_path": str(output_path), "applied_sim3": sim3}

    # ---- read sparse model --------------------------------------------

    def read_reconstruction(self, path: Path) -> Any:
        pc = colmap_adapter._require_pycolmap()
        rec = pc.Reconstruction()
        rec.read(str(path))
        return rec

    # ---- runtime version vector ---------------------------------------

    def runtime_versions(self) -> dict[str, str]:
        return colmap_adapter.get_runtime_versions()


def _summarize_mesh_ply(ply_path: Path, *, method: str) -> dict:
    """Read a binary PLY header to extract vertex/face counts so the
    snapshot emitter doesn't need to parse the body."""
    if not ply_path.is_file():
        return {
            "method": method,
            "num_vertices": 0,
            "num_faces": 0,
            "has_vertex_colors": False,
            "has_vertex_normals": False,
        }
    raw = ply_path.read_bytes()
    header_end = raw.find(b"end_header\n")
    if header_end == -1:
        return {"method": method, "num_vertices": 0, "num_faces": 0}
    header_text = raw[:header_end].decode("ascii", errors="replace")
    num_vertices = 0
    num_faces = 0
    has_colors = False
    has_normals = False
    for line in header_text.splitlines():
        if line.startswith("element vertex "):
            num_vertices = int(line.split()[-1])
        elif line.startswith("element face "):
            num_faces = int(line.split()[-1])
        elif line.startswith("property uchar red"):
            has_colors = True
        elif line.startswith("property float nx"):
            has_normals = True
    return {
        "method": method,
        "num_vertices": num_vertices,
        "num_faces": num_faces,
        "has_vertex_colors": has_colors,
        "has_vertex_normals": has_normals,
    }


def _resolve_pycolmap_callable(pc: Any, names: list[str]) -> Any:
    for name in names:
        fn = getattr(pc, name, None)
        if callable(fn):
            return fn
    raise CapabilityUnavailableError(
        capability="dense.patch_match_stereo",
        reason=f"None of {names!r} are exposed by the installed pycolmap",
    )


def _read_colmap_depth_map(path: Path) -> tuple[int, int, int, Any]:
    """Parse the ASCII-prefixed COLMAP depth/normal binary.

    File starts with text ``"<width>&<height>&<channels>&"`` then raw
    float32 little-endian pixels, channels-last, row-major."""
    import numpy as np

    raw = path.read_bytes()
    head_end = 0
    sep_count = 0
    for i, ch in enumerate(raw):
        if ch == 0x26:  # '&'
            sep_count += 1
            if sep_count == 3:
                head_end = i + 1
                break
    if sep_count != 3:
        raise ValueError(f"malformed COLMAP map header in {path}")
    head = raw[:head_end].decode("ascii", errors="replace").rstrip("&")
    parts = head.split("&")
    width = int(parts[0])
    height = int(parts[1])
    channels = int(parts[2])
    pixels = np.frombuffer(raw[head_end:], dtype="<f4")
    expected = width * height * channels
    if pixels.size != expected:
        raise ValueError(f"COLMAP map {path} body has {pixels.size} floats, expected {expected}")
    return width, height, channels, pixels.reshape(height, width, channels)


def _ply_to_points_bin(ply_path: Path) -> tuple[bytes, int]:
    """Convert a binary PLY of vertices (with optional rgb) to the
    ``application/x-sfm-points-v1`` blob. Returns ``(blob, num_points)``."""
    import struct

    from app.schemas.points_binary import Point3DRecord, encode_all

    raw = ply_path.read_bytes()
    header_end = raw.find(b"end_header\n")
    if header_end == -1:
        raise ValueError(f"no end_header in {ply_path}")
    header_text = raw[:header_end].decode("ascii", errors="replace")
    body = raw[header_end + len(b"end_header\n") :]
    if "format binary_little_endian" not in header_text:
        raise ValueError("only binary_little_endian PLY supported by dense fusion")
    num_vertices = 0
    has_rgb = False
    for line in header_text.splitlines():
        if line.startswith("element vertex "):
            num_vertices = int(line.split()[-1])
        if line.startswith("property uchar red"):
            has_rgb = True

    record_size = (3 * 4) + (3 if has_rgb else 0)
    if len(body) < num_vertices * record_size:
        raise ValueError(f"PLY body too short: {len(body)} < {num_vertices * record_size}")
    records: list[Point3DRecord] = []
    bbox_min = [float("inf")] * 3
    bbox_max = [float("-inf")] * 3
    for i in range(num_vertices):
        off = i * record_size
        x, y, z = struct.unpack_from("<fff", body, off)
        if has_rgb:
            r, g, b = body[off + 12], body[off + 13], body[off + 14]
        else:
            r = g = b = 200
        records.append(Point3DRecord(point3d_id=i, xyz=(x, y, z), rgb=(r, g, b), track_len=0))
        for axis, val in enumerate((x, y, z)):
            if val < bbox_min[axis]:
                bbox_min[axis] = val
            if val > bbox_max[axis]:
                bbox_max[axis] = val
    if not records:
        bbox_min = bbox_max = [0.0, 0.0, 0.0]
    blob = encode_all(records, bbox_min=tuple(bbox_min), bbox_max=tuple(bbox_max))
    return blob, len(records)


def _convert_colmap_depth_maps(stereo_dir: Path, out_dense: Path) -> tuple[list, int]:
    """Walk ``<workspace>/stereo/depth_maps/*.geometric.bin``, convert
    each to the sfmapi binary format. Returns the index entries +
    normal-map count."""
    from app.schemas.api.scene import DepthMapInfo
    from app.schemas.depth_map_binary import encode_depth, encode_normal
    from app.storage._atomic import write_bytes as _atomic_write_bytes

    depth_dir = stereo_dir / "depth_maps"
    normal_dir = stereo_dir / "normal_maps"
    if not depth_dir.is_dir():
        return [], 0
    out_depth = out_dense / "depth_maps"
    out_normal = out_dense / "normal_maps"
    out_depth.mkdir(parents=True, exist_ok=True)

    entries: list = []
    normal_count = 0
    image_id_counter = 0
    for path in sorted(depth_dir.glob("*.geometric.bin")):
        image_id_counter += 1
        image_name = path.name[: -len(".geometric.bin")]
        try:
            w, h, _, arr = _read_colmap_depth_map(path)
        except Exception:
            continue
        depths_ok = arr[arr > 0]
        dmin = float(depths_ok.min()) if depths_ok.size else 0.0
        dmax = float(depths_ok.max()) if depths_ok.size else 0.0
        blob = encode_depth(w, h, dmin, dmax, arr.astype("<f4").tobytes())
        _atomic_write_bytes(out_depth / f"{image_name}.bin", blob)

        has_normal = False
        normal_path = normal_dir / f"{image_name}.geometric.bin"
        if normal_path.is_file():
            try:
                nw, nh, nc, narr = _read_colmap_depth_map(normal_path)
                if nc == 3:
                    out_normal.mkdir(parents=True, exist_ok=True)
                    nblob = encode_normal(nw, nh, narr.astype("<f4").tobytes())
                    _atomic_write_bytes(out_normal / f"{image_name}.bin", nblob)
                    has_normal = True
                    normal_count += 1
            except Exception:
                pass
        entries.append(
            DepthMapInfo(
                image_id=image_id_counter,
                image_name=image_name,
                width=w,
                height=h,
                depth_min=dmin,
                depth_max=dmax,
                has_normal_map=has_normal,
            )
        )
    return entries, normal_count


def _localize_failure(reason: str) -> dict:
    return {
        "success": False,
        "cam_from_world": None,
        "num_inliers": 0,
        "inlier_matches": [],
        "diagnostics": {"reason": reason},
    }


# ---- private helpers (formerly inside app/workers/tasks/map.py) ---------


def _num_reg_images(rec: Any) -> int:
    """Pycolmap exposes ``num_reg_images`` as a method on real
    Reconstructions but tests stub it as an int attribute. Accept both."""
    nr = getattr(rec, "num_reg_images", 0)
    return int(nr() if callable(nr) else nr)


def _num_points3D(rec: Any) -> int:
    nr = getattr(rec, "num_points3D", 0)
    return int(nr() if callable(nr) else nr)


def _next_checkpoint_seq(job_dir: Path) -> int:
    from app.storage.mapping_input import latest_checkpoint

    cp = latest_checkpoint(job_dir)
    return (cp.seq + 1) if cp else 1


def _attach_pose_priors(pc: Any, mapping_input: Any, pose_priors: dict) -> bool:
    """Best-effort: install wire-format pose priors onto a
    pycolmap.MappingInput. Returns True if at least one prior was
    accepted. The pycolmap API for priors moves around between
    versions — try a few shapes and fall through silently if none stick."""
    if not pose_priors or not hasattr(pc, "Rigid3d"):
        return False
    add_one = getattr(mapping_input, "add_pose_prior", None) or getattr(
        mapping_input, "set_pose_prior", None
    )
    attached = 0
    for name, prior in pose_priors.items():
        cfw = (prior or {}).get("cam_from_world") or {}
        rot = cfw.get("rotation") or {}
        t = cfw.get("translation") or [0.0, 0.0, 0.0]
        try:
            rot_xyzw = [float(rot["x"]), float(rot["y"]), float(rot["z"]), float(rot["w"])]
            rotation = pc.Rotation3d(rot_xyzw)
            rigid = pc.Rigid3d(rotation=rotation, translation=list(t))
        except Exception:
            continue
        if add_one is None:
            store = getattr(mapping_input, "pose_priors", None)
            if store is None:
                continue
            try:
                store[name] = rigid  # type: ignore[index]
                attached += 1
            except Exception:
                continue
        else:
            try:
                add_one(name, rigid)
            except TypeError:
                try:
                    add_one(name=name, cam_from_world=rigid)
                except Exception:
                    continue
            except Exception:
                continue
            attached += 1
    return attached > 0


class _SuppressPycolmapErrors:
    """A callback raising into pycolmap's C++ pipeline can crash the
    run. Swallow callback exceptions; worst case is a missed
    checkpoint, not a corrupt reconstruction."""

    def __enter__(self) -> _SuppressPycolmapErrors:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return True


def _run_incremental(
    *,
    pc: Any,
    spec: dict,
    db_path: Path,
    image_root: Path,
    sparse_root: Path,
    job_dir: Path,
    pose_priors: dict,
) -> list[Any]:
    """Drive pycolmap.IncrementalPipeline with checkpoint emission +
    pose-prior seeding. Falls back to the one-shot
    ``incremental_mapping`` entrypoint when the manual-pipeline
    bindings aren't available."""
    from app.storage.mapping_input import latest_checkpoint, write_checkpoint

    opts = pc.IncrementalPipelineOptions()
    for k, v in (spec or {}).items():
        if hasattr(opts, k):
            setattr(opts, k, v)

    checkpoint_every = int(spec.get("checkpoint_every", 10))
    state = {"count": 0, "seq": _next_checkpoint_seq(job_dir)}

    use_pipeline = (
        hasattr(pc, "IncrementalPipeline")
        and hasattr(pc, "MappingInput")
        and hasattr(pc, "ReconstructionManager")
    )
    if not use_pipeline:
        kwargs: dict[str, Any] = dict(
            database_path=str(db_path),
            image_path=str(image_root),
            output_path=str(sparse_root),
            options=opts,
        )
        cp = latest_checkpoint(job_dir)
        if (cp is not None and "mapping_input" in pc.incremental_mapping.__doc__) or "":
            try:
                mi = pc.MappingInput.load(str(cp.path))  # type: ignore[attr-defined]
                kwargs["mapping_input"] = mi
            except Exception:
                pass
        return list(pc.incremental_mapping(**kwargs) or [])

    rec_manager = pc.ReconstructionManager()
    pipeline = pc.IncrementalPipeline(
        options=opts,
        image_path=str(image_root),
        database_path=str(db_path),
        reconstruction_manager=rec_manager,
    )

    cp = latest_checkpoint(job_dir)
    if cp is not None:
        try:
            mi = pc.MappingInput.load(str(cp.path))
            if hasattr(pipeline, "set_mapping_input"):
                pipeline.set_mapping_input(mi)
        except Exception:
            pass
    elif pose_priors:
        try:
            mi = pc.MappingInput()
            attached = _attach_pose_priors(pc, mi, pose_priors)
            if attached and hasattr(pipeline, "set_mapping_input"):
                pipeline.set_mapping_input(mi)
        except Exception:
            pass

    def _on_register(*_args: Any, **_kw: Any) -> None:
        state["count"] += 1
        if state["count"] % checkpoint_every != 0:
            return
        with _SuppressPycolmapErrors():
            mi = pc.MappingInput()
            if hasattr(mi, "from_pipeline"):
                mi.from_pipeline(pipeline)
            elif hasattr(mi, "build_from"):
                mi.build_from(pipeline)
            payload = bytes(mi.save_to_bytes()) if hasattr(mi, "save_to_bytes") else b""
            if not payload:
                tmp = job_dir / "checkpoints" / f"_inflight_{state['seq']:08d}.pcmapin"
                tmp.parent.mkdir(parents=True, exist_ok=True)
                mi.save(str(tmp))
                payload = tmp.read_bytes()
                tmp.unlink(missing_ok=True)
            write_checkpoint(
                job_dir,
                seq=state["seq"],
                payload=payload,
                summary={"registered": state["count"], "phase": "incremental_register"},
            )
            state["seq"] += 1

    if hasattr(pipeline, "add_callback") and hasattr(pc, "PipelineCallback"):
        pipeline.add_callback(pc.PipelineCallback.NEXT_IMAGE_REG_CALLBACK, _on_register)
    pipeline.run()
    pipeline.write(str(sparse_root))
    return list(rec_manager) if rec_manager else []


__all__ = ["ColmapModBackend"]
