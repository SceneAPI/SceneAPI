"""ProgressEvent emitter.

The worker writes events to two sinks:
  1. `events.jsonl` — durable, used for SSE replay via `Last-Event-ID`.
  2. `JobEvent` table — durable index for the API to count/serve events.

Both are appended in order. The `event_id` in the DB doubles as the SSE
event id; clients reconnect with `Last-Event-ID: <int>` to resume from
that point.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class JsonlEventSink:
    """Append-only JSONL sink, thread-safe under a process. Workers run
    in subprocess so cross-process locking isn't required for v0."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def append(self, payload: dict[str, Any]) -> None:
        line = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        with self._lock, self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()

    def read_from(self, after_seq: int = 0) -> list[dict]:
        if not self.path.is_file():
            return []
        out: list[dict] = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("seq", 0) > after_seq:
                    out.append(obj)
        return out


def now_iso() -> str:
    return datetime.now(UTC).isoformat()
