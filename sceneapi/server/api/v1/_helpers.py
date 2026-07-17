"""Web-layer helpers shared across v1 routes.

These belong in the API layer (not in ``sceneapi/server/schemas/api/``) because
they construct FastAPI ``Response`` objects — schemas modules are
intentionally kept Pydantic-only so workers and the CLI can import
the wire types without pulling FastAPI into their process.
"""

from __future__ import annotations

from typing import Any

from fastapi import status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from sceneapi.server.core.errors import ValidationError
from sceneapi.server.schemas.api.jobs import JobAcceptedResponse


def accepted_response(body: JobAcceptedResponse) -> JSONResponse:
    """Wrap a :class:`JobAcceptedResponse` as the canonical 202 envelope.

    Every job-submitting route returns 202 + a ``Location`` header
    pointing at ``GET /v1/jobs/{job_id}``. Centralizing the
    construction keeps the wire shape consistent and lets future
    additions (Link header, retry-after, request-id echo) land in
    one place.
    """
    return JSONResponse(
        body.model_dump(),
        status_code=status.HTTP_202_ACCEPTED,
        headers={"Location": f"/v1/jobs/{body.job_id}"},
    )


def masked_updates(
    body: BaseModel, update_mask: str | None, *, allowed: set[str]
) -> dict[str, Any]:
    """Return PATCH updates using AIP-161-style comma-separated mask paths.

    Omitted ``update_mask`` preserves the existing sfmapi behavior:
    apply exactly the request body fields the client sent. When a mask
    is present, every path must be writable and present in the body;
    body fields outside the mask are intentionally ignored.
    """
    if update_mask is None:
        return body.model_dump(exclude_unset=True)

    paths = [part.strip() for part in update_mask.split(",") if part.strip()]
    if not paths:
        raise ValidationError("update_mask must contain at least one field path")

    unknown = sorted(set(paths) - allowed)
    if unknown:
        raise ValidationError(f"update_mask contains unknown field(s): {', '.join(unknown)}")

    sent: set[str] = body.model_fields_set
    missing = sorted(path for path in paths if path not in sent)
    if missing:
        raise ValidationError(f"update_mask field(s) missing from body: {', '.join(missing)}")

    return {path: getattr(body, path) for path in paths}
