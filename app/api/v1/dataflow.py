"""Typed-dataflow validation endpoints.

Pre-flight type-checking for pipelines: given an ordered list of action ids,
confirm the chain composes under the nominal type system
(:mod:`app.core.pipelines`) before any job is submitted. Project-independent
and side-effect-free -- a pure function of the embedded core contracts, so the
C++ port serves byte-identical results from the same action-signature embed.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from app.core import pipelines

router = APIRouter(prefix="/pipelines", tags=["pipelines"])


class PipelineValidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    steps: list[str]


class ChainErrorOut(BaseModel):
    where: str
    message: str


class PipelineValidateResponse(BaseModel):
    valid: bool
    errors: list[ChainErrorOut]


@router.post(":validate", response_model=PipelineValidateResponse)
async def validate_pipeline(body: PipelineValidateRequest) -> PipelineValidateResponse:
    """Type-check a linear pipeline. Returns ``valid`` + per-step ``errors``;
    a type break or unsignatured action makes it invalid. Bridging a type
    mismatch requires an explicit conversion action (nominal typing)."""
    errors = pipelines.validate_linear(list(body.steps))
    return PipelineValidateResponse(
        valid=not errors,
        errors=[ChainErrorOut(where=e.where, message=e.message) for e in errors],
    )
