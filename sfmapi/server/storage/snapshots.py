"""Sealed-snapshot writer.

Workers write live progress to `sparse/`; periodically they "seal" the
current state into `snapshots/{seq}/` via atomic dir rename so the API
can read without racing the writer.

Sealing protocol:
  1. tmp = `snapshots/.tmp_{seq}`
  2. copy live `sparse/` (and any sidecar JSONs) into tmp
  3. write `tmp/.complete` last
  4. `os.replace(tmp, target)` (atomic on the same FS)
  5. update `latest` pointer (text file holding seq number)

API readers:
  - list seq dirs (filter to those with `.complete` marker)
  - read inside any sealed dir; data is immutable
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
from pathlib import Path


class SnapshotStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.snapshots = root / "snapshots"
        self.snapshots.mkdir(parents=True, exist_ok=True)

    def seal(self, *, seq: int, source_dir: Path, summary: dict | None = None) -> Path:
        target = self.snapshots / f"{seq:08d}"
        if target.exists():
            raise FileExistsError(f"Snapshot seq {seq} already sealed")
        tmp = self.snapshots / f".tmp_{seq:08d}"
        if tmp.exists():
            shutil.rmtree(tmp)
        if source_dir.is_dir():
            shutil.copytree(source_dir, tmp)
        else:
            tmp.mkdir(parents=True)
        if summary is not None:
            (tmp / "summary.json").write_text(
                json.dumps(summary, sort_keys=True, indent=2), encoding="utf-8"
            )
        # Marker last so partial dirs are easy to detect.
        (tmp / ".complete").write_text(str(seq), encoding="utf-8")
        os.replace(tmp, target)
        self._set_latest(seq)
        return target

    def list_sealed(self) -> list[int]:
        out: list[int] = []
        if not self.snapshots.exists():
            return out
        for child in self.snapshots.iterdir():
            if (
                child.is_dir()
                and not child.name.startswith(".tmp_")
                and (child / ".complete").is_file()
            ):
                try:
                    out.append(int(child.name))
                except ValueError:
                    continue
        out.sort()
        return out

    def latest(self) -> int | None:
        latest_file = self.snapshots / "latest"
        if not latest_file.is_file():
            seqs = self.list_sealed()
            return seqs[-1] if seqs else None
        try:
            return int(latest_file.read_text().strip())
        except (ValueError, OSError):
            return None

    def path_for(self, seq: int) -> Path:
        return self.snapshots / f"{seq:08d}"

    def gc(self, *, keep_last: int = 3) -> list[int]:
        seqs = self.list_sealed()
        if len(seqs) <= keep_last:
            return []
        to_drop = seqs[:-keep_last]
        for s in to_drop:
            with contextlib.suppress(OSError):
                shutil.rmtree(self.path_for(s))
        return to_drop

    def _set_latest(self, seq: int) -> None:
        # Plain text file is portable across SQLite/Postgres-shaped reads
        # and across Windows/Linux. Write+rename for atomicity.
        latest = self.snapshots / "latest"
        tmp = self.snapshots / ".latest.tmp"
        tmp.write_text(str(seq), encoding="utf-8")
        os.replace(tmp, latest)
