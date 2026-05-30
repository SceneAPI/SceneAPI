"""Typed-dataflow discovery + validation endpoints.

Surfaces the core typed-dataflow contracts on the wire:
* ``GET  /v1/datatypes``           -- the DataType registry (the data nouns).
* ``GET  /v1/operations``          -- the operation registry (typed verbs +
                                      their consumes/produces + capability link).
* ``POST /v1/pipelines:validate``  -- pre-flight type-check of an operation
                                      pipeline before any job is submitted.

All three are project-independent, side-effect-free functions of the embedded
core contracts, so the C++ port serves byte-identical results from the same
embed.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from app.core import datatypes, operations, pipelines

router = APIRouter(tags=["dataflow"])


@router.get("/datatypes")
async def list_datatypes() -> dict[str, Any]:
    """The DataType registry: the logical data objects a pipeline flows."""
    return datatypes.contract_dict()


@router.get("/operations")
async def list_operations() -> dict[str, Any]:
    """The operation registry: typed transforms (consumes/produces) and the
    capability family that implements each."""
    return operations.contract_dict()


class PipelineValidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    steps: list[str]


class ChainErrorOut(BaseModel):
    where: str
    message: str


class PipelineValidateResponse(BaseModel):
    valid: bool
    errors: list[ChainErrorOut]


@router.post("/pipelines:validate", response_model=PipelineValidateResponse)
async def validate_pipeline(body: PipelineValidateRequest) -> PipelineValidateResponse:
    """Type-check a pipeline of operations. Returns ``valid`` + per-step
    ``errors``: an operation whose inputs are not produced upstream (or an
    unknown operation) makes it invalid. Bridging a missing input requires an
    explicit conversion operation (nominal typing)."""
    errors = pipelines.validate_pipeline(list(body.steps))
    return PipelineValidateResponse(
        valid=not errors,
        errors=[ChainErrorOut(where=e.where, message=e.message) for e in errors],
    )
