"""Parse a Kapture-format archive into a sensors/records inventory.

Reads ``sensors/sensors.txt`` + ``sensors/records_camera.txt`` from
an extracted Kapture archive and returns the parsed contents plus
the recommended ``image_root`` (``<archive>/records_data``). The
client follows up with a ``POST /v1/projects/{pid}/datasets`` of
``kind="local"`` pointing at that root.

Pure-Python — no external deps. Capability ``import.kapture`` is
always advertised by sfmapi (it doesn't depend on the SfM backend).
"""

from __future__ import annotations

from pathlib import Path

from sfmapi.server.core.errors import ValidationError
from sfmapi.server.db.models import Task
from sfmapi.server.workers._task_io import read_inputs
from sfmapi.server.workers.tasks._registry import task_handler


def _parse_kapture_sensors(sensors_path: Path) -> list[dict]:
    """Parse a ``sensors.txt`` file. Returns one dict per camera with
    ``id``, ``model``, ``width``, ``height``, ``params``."""
    out: list[dict] = []
    if not sensors_path.is_file():
        return out
    for raw in sensors_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 6:
            continue
        sensor_id, _name, sensor_type, model, *rest = parts
        if sensor_type != "camera":
            continue
        try:
            width = int(rest[0])
            height = int(rest[1])
            params = [float(p) for p in rest[2:]]
        except (ValueError, IndexError):
            continue
        out.append(
            {
                "id": sensor_id,
                "model": model,
                "width": width,
                "height": height,
                "params": params,
            }
        )
    return out


def _parse_kapture_records(records_path: Path) -> list[dict]:
    """Parse ``records_camera.txt``. Returns one dict per record with
    ``timestamp``, ``sensor_id``, ``image_path`` (relative to the
    archive's ``records_data/`` root)."""
    out: list[dict] = []
    if not records_path.is_file():
        return out
    for raw in records_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        try:
            ts = int(parts[0])
        except ValueError:
            continue
        out.append({"timestamp": ts, "sensor_id": parts[1], "image_path": parts[2]})
    return out


@task_handler("kapture_import")
def run(task: Task) -> dict:
    inputs = read_inputs(task)
    archive_path = Path(inputs["archive_path"])
    if not archive_path.is_dir():
        raise ValidationError(
            f"kapture archive not found at {archive_path}; "
            "expected an extracted directory with sensors/ and records_data/"
        )
    sensors = _parse_kapture_sensors(archive_path / "sensors" / "sensors.txt")
    records = _parse_kapture_records(archive_path / "sensors" / "records_camera.txt")
    if not records:
        raise ValidationError("kapture archive has no camera records")
    return {
        "archive_path": str(archive_path),
        "image_root": str(archive_path / "records_data"),
        "num_sensors": len(sensors),
        "num_records": len(records),
        "sensors": sensors,
        "records": records,
    }
