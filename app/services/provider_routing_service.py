"""Bridge sfm_hub provider resolution into stage validation."""

from __future__ import annotations

from typing import Any

from app.core.errors import ValidationError
from sfm_hub.routing import ProviderAmbiguityError, resolve_provider


def apply_provider_resolution(
    spec: dict[str, Any],
    *,
    stage: str,
    capability: str | None,
    project_id: str | None = None,
    workspace: str | None = None,
) -> None:
    """Mutate a stage spec with the selected provider when the hub can resolve one."""

    requested = spec.get("provider")
    try:
        provider = resolve_provider(
            stage=stage,
            capability=capability,
            requested_provider=str(requested) if requested is not None else None,
            project_id=project_id,
            workspace=workspace,
        )
    except ProviderAmbiguityError as exc:
        raise ValidationError(
            str(exc),
            candidates=exc.candidates,
            suggested_fix="set provider on the request or configure a routing profile",
        ) from exc
    except KeyError as exc:
        raise ValidationError(str(exc)) from exc
    if requested is None and provider:
        spec["provider"] = provider
