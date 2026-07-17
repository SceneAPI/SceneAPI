"""PoseGraph sidecar emitter.

Writes ``pose_graph.json`` alongside a sealed snapshot. Nodes are the
optimized image poses (the same shape as ``images.json`` would expose,
without per-image keypoints). Edges are the relative-pose constraints
that drove the optimization.

Pycolmap is duck-typed: callers pass a ``pycolmap.Reconstruction``
(plus an optional ``pose_graph`` object exposing ``edges``); tests
pass synthetic stubs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sceneapi.server.schemas.api.scene import (
    ImagePose,
    PoseGraph,
    PoseGraphEdge,
    PoseGraphFile,
    Rigid3,
)
from sceneapi.server.storage._atomic import write_text as _atomic_write_text
from sceneapi.server.storage.snapshot_emit import _rigid3_from_pycolmap


def _node_from_image(img: Any) -> ImagePose:
    return ImagePose(
        image_id=int(img.image_id),
        name=str(img.name),
        camera_id=int(img.camera_id),
        cam_from_world=_rigid3_from_pycolmap(img.cam_from_world),
        points2D=[],
    )


def _edges_from_object(graph: Any) -> list[PoseGraphEdge]:
    edges: list[PoseGraphEdge] = []
    for e in getattr(graph, "edges", None) or []:
        rel: Rigid3 = _rigid3_from_pycolmap(e.cam2_from_cam1)
        edges.append(
            PoseGraphEdge(
                image_id1=int(e.image_id1),
                image_id2=int(e.image_id2),
                cam2_from_cam1=rel,
                weight=float(getattr(e, "weight", 1.0)),
            )
        )
    return edges


def emit_pose_graph_file(reconstruction: Any, out_dir: Path, *, graph: Any | None = None) -> Path:
    """Write ``pose_graph.json`` into ``out_dir``. ``graph`` is the
    optional ``pycolmap.PoseGraph`` (or any duck-typed object with
    ``edges``); if absent, only the optimized nodes are emitted."""
    images = list((getattr(reconstruction, "images", None) or {}).values())
    nodes = [_node_from_image(i) for i in images]
    edges = _edges_from_object(graph) if graph is not None else []
    payload = PoseGraphFile(pose_graph=PoseGraph(nodes=nodes, edges=edges))
    out = out_dir / "pose_graph.json"
    _atomic_write_text(out, payload.model_dump_json(by_alias=True, indent=2))
    return out


__all__ = ["emit_pose_graph_file"]
