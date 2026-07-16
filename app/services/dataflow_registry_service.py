"""Effective typed-dataflow registry assembled from core plus active plugins."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Any

from app.core import pipelines as core_pipelines
from app.core.attributes import Attribute
from app.core.datatypes import CORE_DATA_TYPES, CORE_DATA_TYPES_BY_ID, DataType
from app.core.processors import CORE_PROCESSORS, PROCESSOR_ALIASES, PortSpec, Processor
from sfm_hub import discovery
from sfm_hub import registry as plugin_registry
from sfm_hub.models import (
    PluginAttributeManifest,
    PluginDataTypeManifest,
    PluginManifest,
    PluginPipelineManifest,
    PluginPortSpecManifest,
)
from sfm_hub.state import PluginState, load_state


@dataclass(frozen=True)
class EffectiveDataflowRegistry:
    data_types: tuple[DataType, ...]
    processors: tuple[Processor, ...]
    pipelines: tuple[dict[str, object], ...]
    data_type_aliases: dict[str, str]
    processor_aliases: dict[str, str]
    pipeline_aliases: dict[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "_data_types_by_id",
            {data_type.type_id: data_type for data_type in self.data_types},
        )
        object.__setattr__(
            self,
            "_processors_by_id",
            {processor.processor_id: processor for processor in self.processors},
        )
        object.__setattr__(
            self,
            "_pipelines_by_id",
            {str(pipeline["pipeline_id"]): pipeline for pipeline in self.pipelines},
        )

    def has_datatype(self, type_id: str) -> bool:
        return self.canonical_datatype(type_id) in self._data_types_by_id

    def canonical_datatype(self, type_id: str) -> str:
        return self.data_type_aliases.get(type_id, type_id)

    def processor_for(self, processor_id: str) -> Processor | None:
        return self._processors_by_id.get(self.processor_aliases.get(processor_id, processor_id))

    def pipeline_for(self, pipeline_id: str) -> dict[str, object] | None:
        canonical = self.pipeline_aliases.get(pipeline_id, pipeline_id)
        plugin_pipeline = self._pipelines_by_id.get(canonical)
        if plugin_pipeline is not None:
            return plugin_pipeline
        core_steps = core_pipelines.CANONICAL_PIPELINES.get(canonical)
        if core_steps is None:
            return None
        return {
            "pipeline_id": canonical,
            "kind": "legacy_canonical",
            "steps": list(core_steps),
        }


def active_manifests(
    *,
    state: PluginState | None = None,
    manifests: Iterable[PluginManifest] | None = None,
) -> list[PluginManifest]:
    state = state or load_state()
    manifests = list(manifests) if manifests is not None else plugin_registry.list_manifests()
    entry_point_installed = discovery.discovered_plugin_ids()
    active: list[PluginManifest] = []
    for manifest in manifests:
        state_row = state.installed.get(manifest.plugin_id)
        installed = state_row is not None or manifest.plugin_id in entry_point_installed
        enabled = installed and (state_row.enabled if state_row is not None else True)
        if enabled:
            active.append(manifest)
    return sorted(active, key=lambda item: item.plugin_id)


def _plugin_owned_id(plugin_id: str, local_id: str, core_ids: set[str] | frozenset[str]) -> str:
    if local_id in core_ids:
        return local_id
    prefix = f"{plugin_id}."
    if local_id.startswith(prefix):
        return local_id
    if "." in local_id:
        raise ValueError(
            f"plugin-owned id {local_id!r} must be local or use the owning plugin prefix {prefix!r}"
        )
    return f"{prefix}{local_id}"


def _datatype_from_manifest(
    data_type: PluginDataTypeManifest,
    *,
    type_id: str,
) -> DataType:
    return DataType(
        type_id,
        data_type.title,
        data_type.kind,
        data_type.description,
    )


def _port_from_manifest(
    port: PluginPortSpecManifest,
    *,
    datatype_map: dict[str, str],
) -> PortSpec:
    return PortSpec(
        datatype=datatype_map.get(port.datatype, port.datatype),
        required=port.required,
        multiple=port.multiple,
        description=port.description,
    )


def _attribute_from_manifest(
    attr: PluginAttributeManifest,
    *,
    datatype_lookup,
    datatype_map: dict[str, str] | None = None,
) -> Attribute:
    default = attr.default
    if attr.type == "datatype-ref" and isinstance(default, str) and datatype_map is not None:
        default = datatype_map.get(default, default)
    return Attribute(
        name=attr.name,
        type=attr.type,
        required=attr.required,
        default=default,
        has_default="default" in attr.model_fields_set,
        enum=tuple(attr.enum),
        min=attr.min,
        max=attr.max,
        description=attr.description,
        datatype_lookup=datatype_lookup,
    )


def _pipeline_from_manifest(
    pipeline: PluginPipelineManifest,
    *,
    pipeline_id: str,
    datatype_map: dict[str, str],
    processor_map: dict[str, str],
    processor_attributes: dict[str, dict[str, Attribute]],
    processor_consumers: dict[str, dict[str, PortSpec]],
) -> dict[str, object]:
    def canonical_wire(value: Any) -> Any:
        if isinstance(value, list):
            return [canonical_wire(item) for item in value]
        if isinstance(value, str) and value.startswith("inputs."):
            datatype = value.removeprefix("inputs.")
            return f"inputs.{datatype_map.get(datatype, datatype)}"
        return value

    def canonical_attributes(processor_id: str, attributes: dict[str, object]) -> dict[str, object]:
        schemas = processor_attributes.get(processor_id, {})
        out: dict[str, object] = {}
        for name, value in attributes.items():
            schema = schemas.get(name)
            if schema is not None and schema.type == "datatype-ref" and isinstance(value, str):
                out[name] = datatype_map.get(value, value)
            else:
                out[name] = value
        return out

    def canonical_step_wires(processor_id: str, wires: dict[str, object]) -> dict[str, object]:
        consumer = processor_consumers.get(processor_id, {})
        out: dict[str, object] = {}
        for role, value in wires.items():
            canonical = canonical_wire(value)
            port = consumer.get(role)
            if port is not None and port.multiple and not isinstance(canonical, list):
                canonical = [canonical]
            out[role] = canonical
        return out

    return {
        "pipeline_id": pipeline_id,
        "title": pipeline.title,
        "aliases": [],
        "initial_inputs": [
            datatype_map.get(type_id, type_id) for type_id in pipeline.initial_inputs
        ],
        "steps": [
            {
                "ref": step.ref,
                "processor": processor_id,
                "attributes": canonical_attributes(
                    processor_id,
                    dict(step.attributes),
                ),
                "wires": canonical_step_wires(processor_id, dict(step.wires)),
            }
            for step in pipeline.steps
            for processor_id in [processor_map.get(step.processor, step.processor)]
        ],
        "description": pipeline.description,
    }


def effective_registry(
    *,
    state: PluginState | None = None,
    manifests: Iterable[PluginManifest] | None = None,
) -> EffectiveDataflowRegistry:
    active = active_manifests(state=state, manifests=manifests)
    plugin_ids = [manifest.plugin_id for manifest in active]
    duplicate_plugin_ids = sorted(
        {plugin_id for plugin_id in plugin_ids if plugin_ids.count(plugin_id) > 1}
    )
    if duplicate_plugin_ids:
        raise ValueError("duplicate active plugin id(s): " + ", ".join(duplicate_plugin_ids))

    data_types_by_id = dict(CORE_DATA_TYPES_BY_ID)
    plugin_data_types: list[DataType] = []
    data_type_aliases: dict[str, str] = {}
    data_type_alias_candidates: dict[str, set[str]] = {}
    datatype_maps_by_plugin: dict[str, dict[str, str]] = {}
    core_datatype_ids = set(CORE_DATA_TYPES_BY_ID)
    for manifest in active:
        datatype_map = {type_id: type_id for type_id in core_datatype_ids}
        for row in manifest.datatypes:
            type_id = _plugin_owned_id(manifest.plugin_id, row.type_id, core_datatype_ids)
            if type_id in data_types_by_id:
                raise ValueError(
                    f"duplicate active DataType {type_id!r} from plugin {manifest.plugin_id!r}"
                )
            data_type = _datatype_from_manifest(row, type_id=type_id)
            data_types_by_id[type_id] = data_type
            plugin_data_types.append(data_type)
            datatype_map[row.type_id] = type_id
            datatype_map[type_id] = type_id
            if row.type_id != type_id and row.type_id not in core_datatype_ids:
                data_type_alias_candidates.setdefault(row.type_id, set()).add(type_id)
        datatype_maps_by_plugin[manifest.plugin_id] = datatype_map

    for alias, targets in sorted(data_type_alias_candidates.items()):
        if alias not in data_types_by_id and len(targets) == 1:
            data_type_aliases[alias] = next(iter(targets))

    def has_datatype(type_id: str) -> bool:
        return data_type_aliases.get(type_id, type_id) in data_types_by_id

    processors_by_id = {processor.processor_id: processor for processor in CORE_PROCESSORS}
    plugin_processor_ids: list[str] = []
    processor_aliases = dict(PROCESSOR_ALIASES)
    processor_alias_candidates: dict[str, set[str]] = {}
    processor_maps_by_plugin: dict[str, dict[str, str]] = {}
    core_processor_ids = {processor.processor_id for processor in CORE_PROCESSORS}
    for manifest in active:
        processor_map = {
            **{processor_id: processor_id for processor_id in core_processor_ids},
            **PROCESSOR_ALIASES,
        }
        datatype_map = datatype_maps_by_plugin[manifest.plugin_id]
        for row in manifest.processors:
            processor_id = _plugin_owned_id(
                manifest.plugin_id,
                row.processor_id,
                core_processor_ids,
            )
            if processor_id in processors_by_id:
                raise ValueError(
                    f"duplicate active Processor {processor_id!r} from plugin "
                    f"{manifest.plugin_id!r}"
                )
            processor = Processor(
                processor_id=processor_id,
                title=row.title,
                consumer={
                    role: _port_from_manifest(port, datatype_map=datatype_map)
                    for role, port in row.consumer.items()
                },
                supplier={
                    role: _port_from_manifest(port, datatype_map=datatype_map)
                    for role, port in row.supplier.items()
                },
                attributes=tuple(
                    _attribute_from_manifest(
                        attr,
                        datatype_lookup=has_datatype,
                        datatype_map=datatype_map,
                    )
                    for attr in row.attributes
                ),
                description=row.description,
                capabilities=tuple(row.capabilities),
            )
            processors_by_id[processor_id] = processor
            plugin_processor_ids.append(processor_id)
            processor_map[row.processor_id] = processor_id
            processor_map[processor_id] = processor_id
            if row.processor_id != processor_id and row.processor_id not in core_processor_ids:
                processor_alias_candidates.setdefault(row.processor_id, set()).add(processor_id)
        processor_maps_by_plugin[manifest.plugin_id] = processor_map

    for alias, targets in sorted(processor_alias_candidates.items()):
        if alias not in processors_by_id and alias not in processor_aliases and len(targets) == 1:
            target = next(iter(targets))
            processor_aliases[alias] = target
            processor = processors_by_id[target]
            processors_by_id[target] = replace(
                processor,
                aliases=tuple(sorted({*processor.aliases, alias})),
            )

    for manifest in active:
        datatype_map = datatype_maps_by_plugin[manifest.plugin_id]
        processor_map = processor_maps_by_plugin[manifest.plugin_id]
        for extension in manifest.processor_extensions:
            extension_processor_id = processor_map.get(
                extension.processor_id,
                extension.processor_id,
            )
            processor = processors_by_id.get(extension_processor_id)
            if processor is None:
                raise ValueError(
                    f"processor extension from plugin {manifest.plugin_id!r} "
                    f"references unknown Processor {extension.processor_id!r}"
                )
            special_inputs = dict(processor.special_inputs)
            for role, port in extension.special_inputs.items():
                if role in processor.consumer or role in special_inputs:
                    raise ValueError(
                        f"processor extension from plugin {manifest.plugin_id!r} "
                        f"duplicates input role {role!r} on "
                        f"{extension.processor_id!r}"
                    )
                special_inputs[role] = _port_from_manifest(
                    port,
                    datatype_map=datatype_map,
                )

            existing_attrs = {
                attr.name for attr in (*processor.attributes, *processor.special_attributes)
            }
            special_attributes = list(processor.special_attributes)
            for attr in extension.special_attributes:
                if attr.name in existing_attrs:
                    raise ValueError(
                        f"processor extension from plugin {manifest.plugin_id!r} "
                        f"duplicates attribute {attr.name!r} on "
                        f"{extension_processor_id!r}"
                    )
                existing_attrs.add(attr.name)
                special_attributes.append(
                    _attribute_from_manifest(
                        attr,
                        datatype_lookup=has_datatype,
                        datatype_map=datatype_map,
                    )
                )

            processors_by_id[extension_processor_id] = Processor(
                processor_id=processor.processor_id,
                title=processor.title,
                consumer=dict(processor.consumer),
                supplier=dict(processor.supplier),
                attributes=tuple(processor.attributes),
                description=processor.description,
                capabilities=tuple(processor.capabilities),
                config_stage=processor.config_stage,
                aliases=tuple(processor.aliases),
                special_inputs=special_inputs,
                special_attributes=tuple(special_attributes),
            )

    processors = tuple(
        list(processors_by_id[processor.processor_id] for processor in CORE_PROCESSORS)
        + [processors_by_id[processor_id] for processor_id in sorted(plugin_processor_ids)]
    )

    plugin_pipelines: list[dict[str, object]] = []
    pipeline_aliases: dict[str, str] = {}
    pipeline_alias_candidates: dict[str, set[str]] = {}
    pipelines_by_id = {
        pipeline_id: {"pipeline_id": pipeline_id}
        for pipeline_id in core_pipelines.CANONICAL_PIPELINES
    }
    core_pipeline_ids = set(core_pipelines.CANONICAL_PIPELINES)
    for manifest in active:
        datatype_map = datatype_maps_by_plugin[manifest.plugin_id]
        processor_map = processor_maps_by_plugin[manifest.plugin_id]
        processor_attributes = {
            processor_id: {
                attr.name: attr for attr in (*processor.attributes, *processor.special_attributes)
            }
            for processor_id, processor in processors_by_id.items()
        }
        processor_consumers = {
            processor_id: {**processor.consumer, **processor.special_inputs}
            for processor_id, processor in processors_by_id.items()
        }
        for row in manifest.pipelines:
            pipeline_id = _plugin_owned_id(
                manifest.plugin_id,
                row.pipeline_id,
                core_pipeline_ids,
            )
            if pipeline_id in pipelines_by_id:
                raise ValueError(
                    f"duplicate active Pipeline {pipeline_id!r} from plugin {manifest.plugin_id!r}"
                )
            pipeline = _pipeline_from_manifest(
                row,
                pipeline_id=pipeline_id,
                datatype_map=datatype_map,
                processor_map=processor_map,
                processor_attributes=processor_attributes,
                processor_consumers=processor_consumers,
            )
            steps = [
                core_pipelines.PipelineStep(
                    ref=str(step["ref"]),
                    processor=str(step["processor"]),
                    attributes=dict(step.get("attributes") or {}),
                    wires=dict(step.get("wires") or {}),
                )
                for step in pipeline["steps"]  # type: ignore[index]
                if isinstance(step, dict)
            ]
            errors = core_pipelines.validate_pipeline(
                steps,
                initial_inputs=tuple(pipeline["initial_inputs"]),  # type: ignore[arg-type]
                processor_lookup=lambda processor_id: processors_by_id.get(processor_id),
            )
            if errors:
                detail = "; ".join(error.message for error in errors)
                raise ValueError(
                    f"plugin pipeline {pipeline_id!r} is invalid after canonicalization: {detail}"
                )
            pipelines_by_id[pipeline_id] = pipeline
            plugin_pipelines.append(pipeline)
            if row.pipeline_id != pipeline_id and row.pipeline_id not in core_pipeline_ids:
                pipeline_alias_candidates.setdefault(row.pipeline_id, set()).add(pipeline_id)
    for alias, targets in sorted(pipeline_alias_candidates.items()):
        if alias not in pipelines_by_id and len(targets) == 1:
            pipeline_aliases[alias] = next(iter(targets))
    aliases_by_pipeline: dict[str, list[str]] = {}
    for alias, target in pipeline_aliases.items():
        aliases_by_pipeline.setdefault(target, []).append(alias)
    for pipeline in plugin_pipelines:
        pipeline["aliases"] = sorted(aliases_by_pipeline.get(str(pipeline["pipeline_id"]), []))

    return EffectiveDataflowRegistry(
        data_types=tuple(CORE_DATA_TYPES)
        + tuple(sorted(plugin_data_types, key=lambda item: item.type_id)),
        processors=processors,
        pipelines=tuple(sorted(plugin_pipelines, key=lambda item: str(item["pipeline_id"]))),
        data_type_aliases=data_type_aliases,
        processor_aliases=processor_aliases,
        pipeline_aliases=pipeline_aliases,
    )


def datatypes_contract(registry: EffectiveDataflowRegistry) -> dict[str, object]:
    aliases_by_type: dict[str, list[str]] = {}
    for alias, target in registry.data_type_aliases.items():
        aliases_by_type.setdefault(target, []).append(alias)
    return {
        "contract": "datatypes",
        "contract_schema_version": 1,
        "kinds": sorted({data_type.kind for data_type in registry.data_types}),
        "types": [
            {
                "type_id": data_type.type_id,
                "title": data_type.title,
                "kind": data_type.kind,
                "aliases": sorted(aliases_by_type.get(data_type.type_id, [])),
                "description": data_type.description,
            }
            for data_type in registry.data_types
        ],
    }


def processors_contract(registry: EffectiveDataflowRegistry) -> dict[str, object]:
    return {
        "contract": "processors",
        "contract_schema_version": 1,
        "processors": [processor.contract_dict() for processor in registry.processors],
        "rules": {
            "composition": (
                "A.supplier[out] -> B.consumer[in] is legal iff their "
                "PortSpec.datatype values match exactly; current match_graph "
                "verification is a compatibility refinement captured by the "
                "Pipeline contract until raw/verified match graphs split."
            ),
            "port_datatype": "singular",
            "plugin_core_contract": "closed",
            "special_inputs": "optional",
        },
    }


def pipelines_contract(registry: EffectiveDataflowRegistry) -> dict[str, object]:
    contract = core_pipelines.contract_dict()
    contract["plugin_pipelines"] = list(registry.pipelines)
    return contract


__all__ = [
    "EffectiveDataflowRegistry",
    "active_manifests",
    "datatypes_contract",
    "effective_registry",
    "pipelines_contract",
    "processors_contract",
]
