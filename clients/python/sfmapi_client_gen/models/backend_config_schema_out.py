from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.backend_config_schema_out_defaults import BackendConfigSchemaOutDefaults
    from ..models.backend_config_schema_out_links_type_0 import BackendConfigSchemaOutLinksType0
    from ..models.backend_config_schema_out_metadata import BackendConfigSchemaOutMetadata
    from ..models.backend_config_schema_out_option_schema_type_0 import (
        BackendConfigSchemaOutOptionSchemaType0,
    )


T = TypeVar("T", bound="BackendConfigSchemaOut")


@_attrs_define
class BackendConfigSchemaOut:
    """Discoverable backend-specific options for a portable sfmapi stage.

    Clients send these settings in the stage spec's ``backend_options``
    object. Portable knobs stay on the top-level stage spec.

        Attributes:
            config_id (str):
            backend (str):
            stage (str):
            display_name (str):
            capability (None | str | Unset):
            provider (None | str | Unset):
            description (None | str | Unset):
            option_schema (BackendConfigSchemaOutOptionSchemaType0 | None | Unset):
            defaults (BackendConfigSchemaOutDefaults | Unset):
            metadata (BackendConfigSchemaOutMetadata | Unset):
            field_links (BackendConfigSchemaOutLinksType0 | None | Unset):
    """

    config_id: str
    backend: str
    stage: str
    display_name: str
    capability: None | str | Unset = UNSET
    provider: None | str | Unset = UNSET
    description: None | str | Unset = UNSET
    option_schema: BackendConfigSchemaOutOptionSchemaType0 | None | Unset = UNSET
    defaults: BackendConfigSchemaOutDefaults | Unset = UNSET
    metadata: BackendConfigSchemaOutMetadata | Unset = UNSET
    field_links: BackendConfigSchemaOutLinksType0 | None | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.backend_config_schema_out_links_type_0 import BackendConfigSchemaOutLinksType0
        from ..models.backend_config_schema_out_option_schema_type_0 import (
            BackendConfigSchemaOutOptionSchemaType0,
        )

        config_id = self.config_id

        backend = self.backend

        stage = self.stage

        display_name = self.display_name

        capability: None | str | Unset
        if isinstance(self.capability, Unset):
            capability = UNSET
        else:
            capability = self.capability

        provider: None | str | Unset
        if isinstance(self.provider, Unset):
            provider = UNSET
        else:
            provider = self.provider

        description: None | str | Unset
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        option_schema: dict[str, Any] | None | Unset
        if isinstance(self.option_schema, Unset):
            option_schema = UNSET
        elif isinstance(self.option_schema, BackendConfigSchemaOutOptionSchemaType0):
            option_schema = self.option_schema.to_dict()
        else:
            option_schema = self.option_schema

        defaults: dict[str, Any] | Unset = UNSET
        if not isinstance(self.defaults, Unset):
            defaults = self.defaults.to_dict()

        metadata: dict[str, Any] | Unset = UNSET
        if not isinstance(self.metadata, Unset):
            metadata = self.metadata.to_dict()

        field_links: dict[str, Any] | None | Unset
        if isinstance(self.field_links, Unset):
            field_links = UNSET
        elif isinstance(self.field_links, BackendConfigSchemaOutLinksType0):
            field_links = self.field_links.to_dict()
        else:
            field_links = self.field_links

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "config_id": config_id,
                "backend": backend,
                "stage": stage,
                "display_name": display_name,
            }
        )
        if capability is not UNSET:
            field_dict["capability"] = capability
        if provider is not UNSET:
            field_dict["provider"] = provider
        if description is not UNSET:
            field_dict["description"] = description
        if option_schema is not UNSET:
            field_dict["option_schema"] = option_schema
        if defaults is not UNSET:
            field_dict["defaults"] = defaults
        if metadata is not UNSET:
            field_dict["metadata"] = metadata
        if field_links is not UNSET:
            field_dict["_links"] = field_links

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.backend_config_schema_out_defaults import BackendConfigSchemaOutDefaults
        from ..models.backend_config_schema_out_links_type_0 import BackendConfigSchemaOutLinksType0
        from ..models.backend_config_schema_out_metadata import BackendConfigSchemaOutMetadata
        from ..models.backend_config_schema_out_option_schema_type_0 import (
            BackendConfigSchemaOutOptionSchemaType0,
        )

        d = dict(src_dict)
        config_id = d.pop("config_id")

        backend = d.pop("backend")

        stage = d.pop("stage")

        display_name = d.pop("display_name")

        def _parse_capability(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        capability = _parse_capability(d.pop("capability", UNSET))

        def _parse_provider(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        provider = _parse_provider(d.pop("provider", UNSET))

        def _parse_description(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        description = _parse_description(d.pop("description", UNSET))

        def _parse_option_schema(
            data: object,
        ) -> BackendConfigSchemaOutOptionSchemaType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                option_schema_type_0 = BackendConfigSchemaOutOptionSchemaType0.from_dict(data)

                return option_schema_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(BackendConfigSchemaOutOptionSchemaType0 | None | Unset, data)

        option_schema = _parse_option_schema(d.pop("option_schema", UNSET))

        _defaults = d.pop("defaults", UNSET)
        defaults: BackendConfigSchemaOutDefaults | Unset
        if isinstance(_defaults, Unset):
            defaults = UNSET
        else:
            defaults = BackendConfigSchemaOutDefaults.from_dict(_defaults)

        _metadata = d.pop("metadata", UNSET)
        metadata: BackendConfigSchemaOutMetadata | Unset
        if isinstance(_metadata, Unset):
            metadata = UNSET
        else:
            metadata = BackendConfigSchemaOutMetadata.from_dict(_metadata)

        def _parse_field_links(data: object) -> BackendConfigSchemaOutLinksType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                field_links_type_0 = BackendConfigSchemaOutLinksType0.from_dict(data)

                return field_links_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(BackendConfigSchemaOutLinksType0 | None | Unset, data)

        field_links = _parse_field_links(d.pop("_links", UNSET))

        backend_config_schema_out = cls(
            config_id=config_id,
            backend=backend,
            stage=stage,
            display_name=display_name,
            capability=capability,
            provider=provider,
            description=description,
            option_schema=option_schema,
            defaults=defaults,
            metadata=metadata,
            field_links=field_links,
        )

        return backend_config_schema_out
