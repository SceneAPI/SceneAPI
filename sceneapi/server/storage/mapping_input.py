"""Re-export shim: the `PCMAPIN` resume-checkpoint storage helpers now
live in the :mod:`sceneio.mapping_input` contract package.

The helpers raise :class:`sceneio.errors.SceneIoError`; the core
:class:`sceneapi.server.core.errors.StorageError` subclasses it, so
existing 507 handling still applies (see the ``SceneIoError`` handler in
``sceneapi.server.main``)."""

from __future__ import annotations

from sceneio.mapping_input import (
    CheckpointRef,
    checkpoint_root,
    gc_checkpoints,
    latest_checkpoint,
    list_checkpoints,
    write_checkpoint,
)

__all__ = [
    "CheckpointRef",
    "checkpoint_root",
    "gc_checkpoints",
    "latest_checkpoint",
    "list_checkpoints",
    "write_checkpoint",
]
