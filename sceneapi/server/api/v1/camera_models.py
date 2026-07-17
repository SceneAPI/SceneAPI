"""Camera model registry."""

from __future__ import annotations

from fastapi import APIRouter

from sceneapi.server.core.camera_models import list_camera_models
from sceneapi.server.schemas.api.camera_models import CameraModelListPage, CameraModelOut

router = APIRouter(prefix="/camera-models", tags=["camera-models"])


@router.get("", response_model=CameraModelListPage)
async def list_supported_camera_models() -> CameraModelListPage:
    """List portable camera model parameter layouts known to sfmapi."""
    return CameraModelListPage(
        items=[CameraModelOut.model_validate(row) for row in list_camera_models()],
        next_page_token=None,
    )
