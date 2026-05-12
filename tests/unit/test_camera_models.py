from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from app.core.camera_models import get_camera_model
from app.core.config import reset_settings_for_tests


def test_camera_model_registry_describes_distortion_layouts() -> None:
    opencv = get_camera_model("OPENCV")
    spherical = get_camera_model("SPHERICAL")

    assert opencv is not None
    assert opencv.params == ("fx", "fy", "cx", "cy", "k1", "k2", "p1", "p2")
    assert opencv.distortion == "opencv_brown"
    assert spherical is not None
    assert spherical.spherical is True
    assert spherical.params == ()


async def test_camera_model_registry_is_exposed(db_setup: None) -> None:
    reset_settings_for_tests()
    from app.main import create_app

    async with AsyncClient(
        transport=ASGITransport(app=create_app()),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/v1/camera-models")

    assert response.status_code == 200
    by_model = {row["model"]: row for row in response.json()["items"]}
    assert by_model["SIMPLE_RADIAL"]["params"] == ["f", "cx", "cy", "k1"]
    assert by_model["SPHERICAL"]["spherical"] is True
