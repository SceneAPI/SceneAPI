"""Config-stage vocabulary — the parameter stages of the pipeline.

A config stage names the *parameter contract* an operation exposes (its
algorithm knobs). Operations (:mod:`sfmapi.server.core.operations`) link to a stage via
``config_stage``; the backend config-schema adapter fills each stage with the
concrete per-provider params. This is the single ordered source of truth for
those stage names, imported by both the core operation registry and the adapter
-- so core no longer reaches into an adapter private for its own vocabulary.

Repo-owned core contract (no plugin import).
"""

from __future__ import annotations

# Ordered: declaration order = pipeline order. The numeric order drives stable
# sorting of backend config schemas / artifact contracts.
CONFIG_STAGES: tuple[str, ...] = (
    "features",
    "pairs",
    "matcher",
    "verify",
    "mapping",
    "bundle_adjustment",
)

CONFIG_STAGE_ORDER: dict[str, int] = {
    stage: (index + 1) * 10 for index, stage in enumerate(CONFIG_STAGES)
}

VALID_CONFIG_STAGES = frozenset(CONFIG_STAGES)


__all__ = ["CONFIG_STAGES", "CONFIG_STAGE_ORDER", "VALID_CONFIG_STAGES"]
