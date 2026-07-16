"""Typed-dataflow discovery + validation endpoints.

Surfaces the core typed-dataflow contracts on the wire:
* ``GET  /v1/datatypes``           -- the DataType registry (the data nouns).
* ``GET  /v1/processors``          -- the native named-port Processor registry.
* ``GET  /v1/pipelines``           -- core + plugin Pipeline definitions.
* ``GET  /v1/operations``          -- legacy flat Operation projection.
* ``POST /v1/pipelines:validate``  -- pre-flight type-check of a Processor
                                      pipeline before any job is submitted.

The core contract subset is byte-identical across Python and C++. Python also
merges active plugin DataType/Processor/Pipeline extensions into these
discovery endpoints; C++ plugin-expanded dataflow discovery is a later parity
target.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.core import attributes, operations, pipelines
from app.core.errors import ValidationError
from app.schemas.api.common import ProblemResponse
from app.schemas.api.dataflow import (
    PipelineStepIn,
    core_steps,
    is_executable_legacy_sfm_pipeline,
    legacy_operation_ids,
    provider_errors,
    should_use_legacy_validation,
)
from app.services import dataflow_registry_service

router = APIRouter(tags=["dataflow"])


class DataTypeOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type_id: str
    title: str
    kind: str
    aliases: list[str]
    description: str


class DataTypesContractOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract: str
    contract_schema_version: int
    kinds: list[str]
    types: list[DataTypeOut]


class AttributeOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type: str
    required: bool
    description: str
    default: Any | None = None
    enum: list[str] | None = None
    min: int | float | None = None
    max: int | float | None = None


class AttributesContractOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract: str
    contract_schema_version: int
    attribute_types: list[str]
    rules: dict[str, str]


class PortSpecOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    datatype: str
    required: bool
    multiple: bool
    description: str


class ProcessorOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    processor_id: str
    title: str
    consumer: dict[str, PortSpecOut]
    supplier: dict[str, PortSpecOut]
    attributes: list[AttributeOut]
    special_inputs: dict[str, PortSpecOut]
    special_attributes: list[AttributeOut]
    capabilities: list[str]
    config_stage: str | None
    aliases: list[str]
    description: str


class ProcessorsContractOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract: str
    contract_schema_version: int
    processors: list[ProcessorOut]
    rules: dict[str, str]


class PipelineDefinitionStepOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str
    processor: str
    attributes: dict[str, Any]
    wires: dict[str, str | list[str]]


class PipelineDefinitionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pipeline_id: str
    title: str
    aliases: list[str]
    initial_inputs: list[str]
    steps: list[PipelineDefinitionStepOut]
    description: str


class PipelinesContractOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract: str
    contract_schema_version: int
    composition_rule: str
    initial_inputs: list[str]
    canonical_pipelines: dict[str, list[str]]
    plugin_pipelines: list[PipelineDefinitionOut]
    step_schema: dict[str, Any]
    validation_reasons: list[str]


class OperationOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op_id: str
    title: str
    consumes: list[str]
    produces: list[str]
    capabilities: list[str]
    config_stage: str | None
    description: str


class OperationsContractOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract: str
    contract_schema_version: int
    operations: list[OperationOut]
    compatibility: dict[str, str]


def _effective_registry_or_error() -> dataflow_registry_service.EffectiveDataflowRegistry:
    try:
        return dataflow_registry_service.effective_registry()
    except ValueError as exc:
        raise ValidationError(
            f"invalid active plugin dataflow registry: {exc}"
        ) from exc


def _validated_json(model: type[BaseModel], payload: Any) -> JSONResponse:
    model.model_validate(payload)
    return JSONResponse(content=payload)


@router.get(
    "/datatypes",
    response_model=DataTypesContractOut,
    responses={422: {"model": ProblemResponse}},
)
async def list_datatypes() -> JSONResponse:
    """The DataType registry: the logical data objects a pipeline flows."""
    registry = _effective_registry_or_error()
    return _validated_json(
        DataTypesContractOut,
        dataflow_registry_service.datatypes_contract(registry),
    )


@router.get("/attributes", response_model=AttributesContractOut)
async def list_attributes() -> JSONResponse:
    """The portable Attribute meta-schema used by Processor contracts."""
    return _validated_json(AttributesContractOut, attributes.contract_dict())


@router.get(
    "/operations",
    response_model=OperationsContractOut,
    response_model_exclude_none=True,
)
async def list_operations() -> JSONResponse:
    """Legacy flat operation registry.

    New clients should use ``GET /v1/processors`` for named consumer/supplier
    ports and attributes. This endpoint remains a compatibility projection.
    """
    return _validated_json(OperationsContractOut, operations.contract_dict())


@router.get(
    "/processors",
    response_model=ProcessorsContractOut,
    response_model_exclude_none=True,
    responses={422: {"model": ProblemResponse}},
)
async def list_processors() -> JSONResponse:
    """The Processor registry: named input/output ports, attributes, and the
    current execution selectors.

    ``capabilities`` names legacy routing selectors required by today's bridge
    execution path. P6 splits these into capability-family metadata and
    provider/runtime requirements before treating them as stable Processor
    law.
    """
    registry = _effective_registry_or_error()
    return _validated_json(
        ProcessorsContractOut,
        dataflow_registry_service.processors_contract(registry),
    )


@router.get(
    "/pipelines",
    response_model=PipelinesContractOut,
    response_model_exclude_none=True,
    responses={422: {"model": ProblemResponse}},
)
async def list_pipelines() -> JSONResponse:
    """Pipeline registry.

    Always includes canonical core pipelines. Runtimes with an effective plugin
    registry may add active plugin DAGs; the C++ tier remains core-only until
    the P3d plugin-open registry gate lands.
    """
    registry = _effective_registry_or_error()
    return _validated_json(
        PipelinesContractOut,
        dataflow_registry_service.pipelines_contract(registry),
    )


class PipelineValidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    initial_inputs: list[str] = Field(
        default_factory=lambda: list(pipelines.DEFAULT_INITIAL_INPUTS),
        description=(
            "Legacy compatibility list of initial DataType ids available as "
            "synthetic inputs.* ports. New Processor pipelines should prefer "
            "reference-keyed initial inputs when that durable shape is enabled."
        ),
    )
    steps: list[str | PipelineStepIn] = Field(min_length=1)


class ChainErrorOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    where: str
    message: str
    reason: str | None = None
    path: str | None = None


class PipelineValidateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool
    errors: list[ChainErrorOut]


def _initial_input_errors(
    initial_inputs: tuple[str, ...],
    *,
    datatype_lookup,
) -> list[pipelines.ChainError]:
    errors: list[pipelines.ChainError] = []
    duplicates = sorted(
        {datatype for datatype in initial_inputs if initial_inputs.count(datatype) > 1}
    )
    for datatype in duplicates:
        errors.append(
            pipelines.ChainError(
                "inputs",
                f"duplicate initial input datatype '{datatype}'",
                "duplicate_initial_input",
                "initial_inputs",
            )
        )
    for datatype in sorted(set(initial_inputs)):
        if not datatype_lookup(datatype):
            errors.append(
                pipelines.ChainError(
                    "inputs",
                    f"unknown initial input datatype '{datatype}'",
                    "unknown_datatype",
                    "initial_inputs",
                )
            )
    return errors


def _legacy_sfm_param_errors(
    steps: list[str | PipelineStepIn],
) -> list[pipelines.ChainError]:
    from app.api.v1 import pipelines as pipeline_routes

    try:
        pipeline_routes._legacy_sfm_specs(steps)
    except ValidationError as exc:
        raw_errors = exc.extras.get("errors")
        if not isinstance(raw_errors, list):
            return [
                pipelines.ChainError(
                    "pipeline",
                    exc.detail,
                    "invalid_attribute",
                    None,
                )
            ]
        errors: list[pipelines.ChainError] = []
        for item in raw_errors:
            if not isinstance(item, dict):
                continue
            ctx = item.get("ctx") if isinstance(item.get("ctx"), dict) else {}
            errors.append(
                pipelines.ChainError(
                    str(ctx.get("where") or "pipeline"),
                    str(item.get("msg") or exc.detail),
                    str(ctx.get("reason") or item.get("type") or "invalid_attribute"),
                    str(ctx.get("path")) if ctx.get("path") is not None else None,
                )
            )
        return errors or [
            pipelines.ChainError(
                "pipeline",
                exc.detail,
                "invalid_attribute",
                None,
            )
        ]
    return []


def legacy_operation_projection_errors(
    steps: list[str | PipelineStepIn],
) -> list[pipelines.ChainError]:
    errors: list[pipelines.ChainError] = []
    for index, op_id in enumerate(legacy_operation_ids(steps)):
        if op_id in operations.OPERATIONS_BY_ID:
            continue
        errors.append(
            pipelines.ChainError(
                f"step {index} '{op_id}'",
                (
                    f"unknown legacy operation '{op_id}'; plugin processors "
                    "must use the native processor step shape"
                ),
                "unknown_processor",
                f"steps.{index}.op",
            )
        )
    return errors


def _canonicalize_initial_input_wires(
    steps: list[pipelines.PipelineStep],
    *,
    registry: dataflow_registry_service.EffectiveDataflowRegistry,
) -> list[pipelines.PipelineStep]:
    def canonical_wire(value: Any) -> Any:
        if not isinstance(value, str) or not value.startswith("inputs."):
            return value
        datatype = value.removeprefix("inputs.")
        if not datatype:
            return value
        return f"inputs.{registry.canonical_datatype(datatype)}"

    out: list[pipelines.PipelineStep] = []
    for step in steps:
        wires: dict[str, Any] = {}
        for role, value in step.wires.items():
            if isinstance(value, list):
                wires[role] = [canonical_wire(item) for item in value]
            else:
                wires[role] = canonical_wire(value)
        out.append(
            pipelines.PipelineStep(
                ref=step.ref,
                processor=step.processor,
                attributes=step.attributes,
                wires=wires,
            )
        )
    return out


@router.post(
    "/pipelines:validate",
    response_model=PipelineValidateResponse,
    response_model_exclude_none=True,
    responses={422: {"model": ProblemResponse}},
)
async def validate_pipeline(body: PipelineValidateRequest) -> PipelineValidateResponse:
    """Type-check a pipeline.

    ``steps`` may be the legacy list of operation ids or the native Processor
    step form with ``ref``, ``processor``, ``attributes``, and port ``wires``.
    Native steps are checked port-to-port; ambiguous type inference requires an
    explicit wire.
    """
    registry = _effective_registry_or_error()
    initial_inputs = tuple(
        registry.canonical_datatype(type_id) for type_id in body.initial_inputs
    )
    input_errors = _initial_input_errors(
        initial_inputs,
        datatype_lookup=registry.has_datatype,
    )
    errors: list[pipelines.ChainError] = []
    steps: list[str] | list[pipelines.PipelineStep]
    use_legacy_validation = should_use_legacy_validation(body.steps)
    executable_legacy_sfm = is_executable_legacy_sfm_pipeline(body.steps)
    if use_legacy_validation:
        errors.extend(input_errors)
        errors.extend(legacy_operation_projection_errors(body.steps))
        steps = legacy_operation_ids(body.steps)
        attr_steps = core_steps(body.steps)
        errors.extend(
            pipelines.validate_pipeline(
                steps,
                initial_inputs=initial_inputs,
                processor_lookup=registry.processor_for,
            )
        )
        if not executable_legacy_sfm:
            errors.extend(
                pipelines.validate_step_attributes(
                    attr_steps,
                    processor_lookup=registry.processor_for,
                )
            )
        else:
            errors.extend(_legacy_sfm_param_errors(body.steps))
    else:
        errors.extend(input_errors)
        steps = _canonicalize_initial_input_wires(
            core_steps(body.steps),
            registry=registry,
        )
        validation_errors = pipelines.validate_pipeline(
            steps,
            initial_inputs=initial_inputs,
            processor_lookup=registry.processor_for,
        )
        errors.extend(
            error for error in validation_errors
            if error.reason != "duplicate_initial_input"
        )
    if use_legacy_validation and not executable_legacy_sfm:
        errors.extend(provider_errors(body.steps))
    return PipelineValidateResponse(
        valid=not errors,
        errors=[
            ChainErrorOut(
                where=e.where,
                message=e.message,
                reason=e.reason,
                path=e.path,
            )
            for e in errors
        ],
    )
