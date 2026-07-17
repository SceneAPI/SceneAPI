"""Extract keyframes from a video into a derived directory.

Calls ``ffmpeg`` via subprocess. Output is a directory of JPEGs that
the user can register as a ``local`` source for downstream pipelines.

Capability flag: ``video.frame_extract`` (depends on ffmpeg being on
PATH at the worker).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from sfmapi.server.core.errors import CapabilityUnavailableError, ValidationError
from sfmapi.server.db.models import Task
from sfmapi.server.workers._task_io import read_state
from sfmapi.server.workers.tasks._registry import task_handler


@task_handler("video_frames")
def run(task: Task) -> dict:
    inputs, spec = read_state(task)
    video_path = Path(inputs["video_path"])
    output_dir = Path(inputs["output_dir"])
    fps = float(spec.get("fps", 2.0))
    max_frames = int(spec.get("max_frames", 1000))

    if not video_path.is_file():
        raise ValidationError(f"video file not found: {video_path}")
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise CapabilityUnavailableError(
            capability="video.frame_extract",
            reason="ffmpeg not on PATH on this worker",
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(output_dir / "frame_%06d.jpg")
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(video_path),
        "-vf",
        f"fps={fps}",
        "-frames:v",
        str(max_frames),
        "-q:v",
        "3",
        pattern,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise ValidationError(f"ffmpeg failed: {proc.stderr[:1000]}")
    frames = sorted(output_dir.glob("frame_*.jpg"))
    return {
        "output_dir": str(output_dir),
        "num_frames": len(frames),
        "fps": fps,
    }
