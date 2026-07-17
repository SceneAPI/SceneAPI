"""Job → Task DAG construction + cache-key computation.

A `Job` is the user-facing intent (one HTTP call). It owns a DAG of
`Task`s. Each Task has a `cache_key` derived from `(kind, inputs_hash,
params_hash, runtime_version_id)`; if a Task with that cache_key has
already produced output, the DAG short-circuits to the existing result
without enqueuing.

This module builds DAGs deterministically from input specs. It does not
talk to the DB or the queue — those are scheduler concerns.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sfmapi.server.core.hashing import canonical_json, content_address


@dataclass
class TaskNode:
    task_id: str  # filled by scheduler from new_id()
    kind: str
    inputs_hash: str
    params_hash: str
    depends_on: list[str] = field(default_factory=list)
    gpu_required: bool = True
    metadata: dict = field(default_factory=dict)

    def cache_key(self, runtime_version_id: str) -> str:
        return content_address(
            canonical_json(
                {
                    "kind": self.kind,
                    "inputs_hash": self.inputs_hash,
                    "params_hash": self.params_hash,
                    "rv": runtime_version_id,
                }
            )
        )


def hash_params(spec: dict) -> str:
    return content_address(canonical_json(spec))


def hash_inputs(refs: dict) -> str:
    return content_address(canonical_json(refs))
