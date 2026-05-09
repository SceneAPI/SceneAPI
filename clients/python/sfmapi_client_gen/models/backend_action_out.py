from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.backend_action_out_side_effects import BackendActionOutSideEffects
from ..models.backend_action_out_stability import BackendActionOutStability
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.backend_action_out_input_schema_type_0 import BackendActionOutInputSchemaType0
    from ..models.backend_action_out_links_type_0 import BackendActionOutLinksType0
    from ..models.backend_action_out_metadata import BackendActionOutMetadata
    from ..models.backend_action_out_output_schema_type_0 import BackendActionOutOutputSchemaType0


T = TypeVar("T", bound="BackendActionOut")


@_attrs_define
class BackendActionOut:
    """Discoverable backend-native operation.

    ``action_id`` is namespaced by the backend or tool family, for
    example ``colmap.feature_extractor``. Portable clients should treat
    action ids as opaque strings and inspect ``input_schema`` before
    presenting a UI or constructing a request.

        Attributes:
            action_id (str):
            backend (str):
            display_name (str):
            description (None | str | Unset):
            category (None | str | Unset):
            stability (BackendActionOutStability | Unset):  Default: BackendActionOutStability.BACKEND_EXTENSION.
            side_effects (BackendActionOutSideEffects | Unset):  Default: BackendActionOutSideEffects.UNKNOWN.
            long_running (bool | Unset):  Default: True.
            supports_progress (bool | Unset):  Default: False.
            idempotent (bool | Unset):  Default: False.
            gpu_required (bool | Unset):  Default: True.
            required_capabilities (list[str] | Unset):
            input_schema (BackendActionOutInputSchemaType0 | None | Unset):
            output_schema (BackendActionOutOutputSchemaType0 | None | Unset):
            metadata (BackendActionOutMetadata | Unset):
            field_links (BackendActionOutLinksType0 | None | Unset):
    """

    action_id: str
    backend: str
    display_name: str
    description: None | str | Unset = UNSET
    category: None | str | Unset = UNSET
    stability: BackendActionOutStability | Unset = BackendActionOutStability.BACKEND_EXTENSION
    side_effects: BackendActionOutSideEffects | Unset = BackendActionOutSideEffects.UNKNOWN
    long_running: bool | Unset = True
    supports_progress: bool | Unset = False
    idempotent: bool | Unset = False
    gpu_required: bool | Unset = True
    required_capabilities: list[str] | Unset = UNSET
    input_schema: BackendActionOutInputSchemaType0 | None | Unset = UNSET
    output_schema: BackendActionOutOutputSchemaType0 | None | Unset = UNSET
    metadata: BackendActionOutMetadata | Unset = UNSET
    field_links: BackendActionOutLinksType0 | None | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.backend_action_out_input_schema_type_0 import BackendActionOutInputSchemaType0
        from ..models.backend_action_out_links_type_0 import BackendActionOutLinksType0
        from ..models.backend_action_out_output_schema_type_0 import (
            BackendActionOutOutputSchemaType0,
        )

        action_id = self.action_id

        backend = self.backend

        display_name = self.display_name

        description: None | str | Unset
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        category: None | str | Unset
        if isinstance(self.category, Unset):
            category = UNSET
        else:
            category = self.category

        stability: str | Unset = UNSET
        if not isinstance(self.stability, Unset):
            stability = self.stability.value

        side_effects: str | Unset = UNSET
        if not isinstance(self.side_effects, Unset):
            side_effects = self.side_effects.value

        long_running = self.long_running

        supports_progress = self.supports_progress

        idempotent = self.idempotent

        gpu_required = self.gpu_required

        required_capabilities: list[str] | Unset = UNSET
        if not isinstance(self.required_capabilities, Unset):
            required_capabilities = self.required_capabilities

        input_schema: dict[str, Any] | None | Unset
        if isinstance(self.input_schema, Unset):
            input_schema = UNSET
        elif isinstance(self.input_schema, BackendActionOutInputSchemaType0):
            input_schema = self.input_schema.to_dict()
        else:
            input_schema = self.input_schema

        output_schema: dict[str, Any] | None | Unset
        if isinstance(self.output_schema, Unset):
            output_schema = UNSET
        elif isinstance(self.output_schema, BackendActionOutOutputSchemaType0):
            output_schema = self.output_schema.to_dict()
        else:
            output_schema = self.output_schema

        metadata: dict[str, Any] | Unset = UNSET
        if not isinstance(self.metadata, Unset):
            metadata = self.metadata.to_dict()

        field_links: dict[str, Any] | None | Unset
        if isinstance(self.field_links, Unset):
            field_links = UNSET
        elif isinstance(self.field_links, BackendActionOutLinksType0):
            field_links = self.field_links.to_dict()
        else:
            field_links = self.field_links

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "action_id": action_id,
                "backend": backend,
                "display_name": display_name,
            }
        )
        if description is not UNSET:
            field_dict["description"] = description
        if category is not UNSET:
            field_dict["category"] = category
        if stability is not UNSET:
            field_dict["stability"] = stability
        if side_effects is not UNSET:
            field_dict["side_effects"] = side_effects
        if long_running is not UNSET:
            field_dict["long_running"] = long_running
        if supports_progress is not UNSET:
            field_dict["supports_progress"] = supports_progress
        if idempotent is not UNSET:
            field_dict["idempotent"] = idempotent
        if gpu_required is not UNSET:
            field_dict["gpu_required"] = gpu_required
        if required_capabilities is not UNSET:
            field_dict["required_capabilities"] = required_capabilities
        if input_schema is not UNSET:
            field_dict["input_schema"] = input_schema
        if output_schema is not UNSET:
            field_dict["output_schema"] = output_schema
        if metadata is not UNSET:
            field_dict["metadata"] = metadata
        if field_links is not UNSET:
            field_dict["_links"] = field_links

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.backend_action_out_input_schema_type_0 import BackendActionOutInputSchemaType0
        from ..models.backend_action_out_links_type_0 import BackendActionOutLinksType0
        from ..models.backend_action_out_metadata import BackendActionOutMetadata
        from ..models.backend_action_out_output_schema_type_0 import (
            BackendActionOutOutputSchemaType0,
        )

        d = dict(src_dict)
        action_id = d.pop("action_id")

        backend = d.pop("backend")

        display_name = d.pop("display_name")

        def _parse_description(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        description = _parse_description(d.pop("description", UNSET))

        def _parse_category(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        category = _parse_category(d.pop("category", UNSET))

        _stability = d.pop("stability", UNSET)
        stability: BackendActionOutStability | Unset
        if isinstance(_stability, Unset):
            stability = UNSET
        else:
            stability = BackendActionOutStability(_stability)

        _side_effects = d.pop("side_effects", UNSET)
        side_effects: BackendActionOutSideEffects | Unset
        if isinstance(_side_effects, Unset):
            side_effects = UNSET
        else:
            side_effects = BackendActionOutSideEffects(_side_effects)

        long_running = d.pop("long_running", UNSET)

        supports_progress = d.pop("supports_progress", UNSET)

        idempotent = d.pop("idempotent", UNSET)

        gpu_required = d.pop("gpu_required", UNSET)

        required_capabilities = cast(list[str], d.pop("required_capabilities", UNSET))

        def _parse_input_schema(data: object) -> BackendActionOutInputSchemaType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                input_schema_type_0 = BackendActionOutInputSchemaType0.from_dict(data)

                return input_schema_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(BackendActionOutInputSchemaType0 | None | Unset, data)

        input_schema = _parse_input_schema(d.pop("input_schema", UNSET))

        def _parse_output_schema(data: object) -> BackendActionOutOutputSchemaType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                output_schema_type_0 = BackendActionOutOutputSchemaType0.from_dict(data)

                return output_schema_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(BackendActionOutOutputSchemaType0 | None | Unset, data)

        output_schema = _parse_output_schema(d.pop("output_schema", UNSET))

        _metadata = d.pop("metadata", UNSET)
        metadata: BackendActionOutMetadata | Unset
        if isinstance(_metadata, Unset):
            metadata = UNSET
        else:
            metadata = BackendActionOutMetadata.from_dict(_metadata)

        def _parse_field_links(data: object) -> BackendActionOutLinksType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                field_links_type_0 = BackendActionOutLinksType0.from_dict(data)

                return field_links_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(BackendActionOutLinksType0 | None | Unset, data)

        field_links = _parse_field_links(d.pop("_links", UNSET))

        backend_action_out = cls(
            action_id=action_id,
            backend=backend,
            display_name=display_name,
            description=description,
            category=category,
            stability=stability,
            side_effects=side_effects,
            long_running=long_running,
            supports_progress=supports_progress,
            idempotent=idempotent,
            gpu_required=gpu_required,
            required_capabilities=required_capabilities,
            input_schema=input_schema,
            output_schema=output_schema,
            metadata=metadata,
            field_links=field_links,
        )

        return backend_action_out
