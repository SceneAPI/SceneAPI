"""MappingInput checkpoint storage helpers.

`pycolmap.MappingInput.save/load` writes a binary container with magic
`PCMAPIN\\x00` (v1) holding the pre-mapping state needed to resume an
incremental run. We persist these per-job under
`jobs/{job_id}/checkpoints/{seq}.pcmapin` so the worker can reload from
the latest checkpoint on resume without re-running upstream stages.

The pycolmap binding is required to read/write — for tests we expose a
trivial adapter that round-trips a payload through a regular file so the
storage code path can be exercised independently.
"""

from __future__ import annotations

import contextlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from sceneapi.server.core.errors import StorageError


@dataclass(frozen=True)
class CheckpointRef:
    seq: int
    path: Path
    summary: dict


def checkpoint_root(job_dir: Path) -> Path:
    return job_dir / "checkpoints"


def write_checkpoint(
    job_dir: Path, *, seq: int, payload: bytes, summary: dict | None = None
) -> CheckpointRef:
    root = checkpoint_root(job_dir)
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{seq:08d}.pcmapin"
    if target.exists():
        raise StorageError(f"checkpoint seq {seq} already exists")
    tmp = target.with_suffix(".pcmapin.tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, target)
    meta = root / f"{seq:08d}.json"
    meta.write_text(json.dumps(summary or {}, sort_keys=True, indent=2), encoding="utf-8")
    return CheckpointRef(seq=seq, path=target, summary=summary or {})


def list_checkpoints(job_dir: Path) -> list[CheckpointRef]:
    root = checkpoint_root(job_dir)
    if not root.exists():
        return []
    out: list[CheckpointRef] = []
    for p in sorted(root.glob("*.pcmapin")):
        try:
            seq = int(p.stem)
        except ValueError:
            continue
        meta_path = root / f"{seq:08d}.json"
        summary: dict = {}
        if meta_path.is_file():
            with contextlib.suppress(json.JSONDecodeError, OSError):
                summary = json.loads(meta_path.read_text(encoding="utf-8"))
        out.append(CheckpointRef(seq=seq, path=p, summary=summary))
    return out


def latest_checkpoint(job_dir: Path) -> CheckpointRef | None:
    cps = list_checkpoints(job_dir)
    return cps[-1] if cps else None


def gc_checkpoints(job_dir: Path, *, keep_last: int = 5) -> list[int]:
    cps = list_checkpoints(job_dir)
    if len(cps) <= keep_last:
        return []
    to_drop = cps[:-keep_last]
    dropped: list[int] = []
    for cp in to_drop:
        with contextlib.suppress(OSError):
            cp.path.unlink()
            (cp.path.parent / f"{cp.seq:08d}.json").unlink()
        dropped.append(cp.seq)
    return dropped
