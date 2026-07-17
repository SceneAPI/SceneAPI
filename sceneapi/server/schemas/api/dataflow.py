"""Typed dataflow request/response schemas shared by discovery and run APIs."""

from __future__ import annotations

import re
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from sceneapi.server.core import pipelines as core_pipelines
from sceneapi.server.schemas.pipeline_spec import (
    PROVIDER_SELECTOR_MAX_LENGTH,
    PROVIDER_SELECTOR_PATTERN,
)

_STEP_REF_PATTERN = r"^[a-z][a-z0-9_]*$"
_STEP_REF_RE = re.compile(_STEP_REF_PATTERN)


class ProcessorPipelineStep(BaseModel):
    """One native Processor instance in a typed pipeline."""

    model_config = ConfigDict(extra="forbid")

    processor: str
    ref: str | None = Field(default=None, min_length=1, pattern=_STEP_REF_PATTERN)
    provider: str | None = Field(
        default=None,
        min_length=1,
        max_length=PROVIDER_SELECTOR_MAX_LENGTH,
        pattern=PROVIDER_SELECTOR_PATTERN,
    )
    attributes: dict[str, Any] = Field(default_factory=dict)
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Legacy alias for attributes; attributes win on overlap.",
    )
    wires: dict[str, str | list[str]] = Field(default_factory=dict)

    @field_validator("ref")
    @classmethod
    def _ref_is_not_reserved(cls, ref: str | None) -> str | None:
        if ref is None:
            return ref
        if not _STEP_REF_RE.match(ref):
            raise ValueError(f"step ref must match {_STEP_REF_PATTERN!r}: {ref!r}")
        if ref == "inputs":
            raise ValueError("'inputs' is reserved for the synthetic pipeline input source")
        return ref


class LegacyOperationStep(BaseModel):
    """Legacy operation-list step.

    This keeps the pre-Processor submission shape working. Supplying params
    promotes the step to Processor attribute validation before submission.
    """

    model_config = ConfigDict(extra="forbid")

    op: str
    provider: str | None = Field(
        default=None,
        min_length=1,
        max_length=PROVIDER_SELECTOR_MAX_LENGTH,
        pattern=PROVIDER_SELECTOR_PATTERN,
    )
    params: dict[str, Any] = Field(default_factory=dict)


class PipelineStep(LegacyOperationStep):
    """Compatibility schema name for the legacy operation-list step."""


PipelineStepIn = Annotated[
    ProcessorPipelineStep | PipelineStep,
    Field(union_mode="left_to_right"),
]


def step_processor_id(step: str | PipelineStepIn) -> str:
    if isinstance(step, str):
        return step
    if isinstance(step, ProcessorPipelineStep):
        return step.processor
    return step.op


def step_ref(step: str | PipelineStepIn, index: int) -> str:
    if isinstance(step, ProcessorPipelineStep) and step.ref:
        return step.ref
    return f"step_{index}"


def step_provider(step: str | PipelineStepIn) -> str | None:
    if isinstance(step, str):
        return None
    return step.provider


def step_attributes(step: str | PipelineStepIn) -> dict[str, Any]:
    if isinstance(step, str):
        return {}
    if isinstance(step, ProcessorPipelineStep):
        return {**step.params, **step.attributes}
    return dict(step.params)


def step_params(step: str | PipelineStepIn) -> dict[str, Any]:
    if isinstance(step, str):
        return {}
    return dict(step.params)


def step_wires(step: str | PipelineStepIn) -> dict[str, Any]:
    if isinstance(step, ProcessorPipelineStep):
        return dict(step.wires)
    return {}


def is_flat_legacy_step(step: str | PipelineStepIn) -> bool:
    return isinstance(step, (str, LegacyOperationStep))


def to_core_step(step: str | PipelineStepIn, index: int) -> core_pipelines.PipelineStep:
    return core_pipelines.PipelineStep(
        ref=step_ref(step, index),
        processor=step_processor_id(step),
        attributes=step_attributes(step),
        wires=step_wires(step),
    )


def provider_errors(
    steps: list[str | PipelineStepIn],
) -> list[core_pipelines.ChainError]:
    errors: list[core_pipelines.ChainError] = []
    for index, step in enumerate(steps):
        provider = step_provider(step)
        if provider is None:
            continue
        ref = step_ref(step, index)
        errors.append(
            core_pipelines.ChainError(
                where=f"step {index} '{ref}'",
                message=(
                    "provider selectors are not supported for custom "
                    "pipelines until typed processor execution lands"
                ),
                reason="provider_unsupported",
                path=f"steps.{index}.provider",
            )
        )
    return errors


def core_steps(steps: list[str | PipelineStepIn]) -> list[core_pipelines.PipelineStep]:
    return [to_core_step(step, i) for i, step in enumerate(steps)]


def should_use_legacy_validation(steps: list[str | PipelineStepIn]) -> bool:
    return bool(steps) and all(is_flat_legacy_step(step) for step in steps)


def legacy_operation_ids(steps: list[str | PipelineStepIn]) -> list[str]:
    return [step_processor_id(step) for step in steps]


EXECUTABLE_LEGACY_SFM_PIPELINE = ("features", "pairs", "matches", "verify", "map")


def is_executable_legacy_sfm_pipeline(steps: list[str | PipelineStepIn]) -> bool:
    return (
        should_use_legacy_validation(steps)
        and tuple(legacy_operation_ids(steps)) == EXECUTABLE_LEGACY_SFM_PIPELINE
    )


__all__ = [
    "EXECUTABLE_LEGACY_SFM_PIPELINE",
    "LegacyOperationStep",
    "PipelineStep",
    "PipelineStepIn",
    "ProcessorPipelineStep",
    "core_steps",
    "is_executable_legacy_sfm_pipeline",
    "is_flat_legacy_step",
    "legacy_operation_ids",
    "provider_errors",
    "should_use_legacy_validation",
    "step_attributes",
    "step_params",
    "step_processor_id",
    "step_provider",
    "step_ref",
    "step_wires",
    "to_core_step",
]
