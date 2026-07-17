"""Typed-dataflow declaration models and cross-declaration graph validation."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from sfm_hub.models.validation import (
    _ATTRIBUTE_RE,
    _CONTRACT_ID_RE,
    _LOCAL_DECLARATION_ID_RE,
    _ROLE_RE,
    _SPECIAL_ROLE_RE,
    CapabilityId,
    _core_datatype_ids,
    _core_pipeline_ids,
    _core_processor_ids,
    _deny_core_ids_schema,
)


class PluginDataTypeManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type_id: str = Field(
        ...,
        pattern=_LOCAL_DECLARATION_ID_RE.pattern,
        json_schema_extra=_deny_core_ids_schema(_core_datatype_ids),
    )
    title: str
    kind: Literal["scene_input", "artifact"] = "artifact"
    description: str = ""

    @field_validator("type_id")
    @classmethod
    def _type_id_format(cls, type_id: str) -> str:
        if not _LOCAL_DECLARATION_ID_RE.match(type_id):
            raise ValueError(
                f"type_id must match {_LOCAL_DECLARATION_ID_RE.pattern!r}: {type_id!r}"
            )
        return type_id


class PluginPortSpecManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    datatype: str = Field(..., pattern=_CONTRACT_ID_RE.pattern)
    required: bool = True
    multiple: bool = False
    description: str = ""

    @field_validator("datatype")
    @classmethod
    def _datatype_format(cls, datatype: str) -> str:
        if not _CONTRACT_ID_RE.match(datatype):
            raise ValueError(f"datatype must match {_CONTRACT_ID_RE.pattern!r}: {datatype!r}")
        return datatype


class PluginAttributeManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., pattern=_ATTRIBUTE_RE.pattern)
    type: Literal["int", "float", "bool", "str", "enum", "datatype-ref", "object"]
    required: bool = False
    default: object | None = None
    enum: list[str] = Field(default_factory=list)
    min: int | float | None = None
    max: int | float | None = None
    description: str = ""

    @field_validator("name")
    @classmethod
    def _name_format(cls, name: str) -> str:
        if not _ATTRIBUTE_RE.match(name):
            raise ValueError(f"attribute name must match {_ATTRIBUTE_RE.pattern!r}: {name!r}")
        return name

    @model_validator(mode="after")
    def _enum_has_values(self) -> PluginAttributeManifest:
        if self.type == "enum" and not self.enum:
            raise ValueError("enum attributes must declare enum values")
        if self.enum and not all(isinstance(value, str) for value in self.enum):
            raise ValueError("attribute enum values must be strings")
        if self.required and "default" in self.model_fields_set:
            raise ValueError("attributes cannot be required and defaulted")
        return self


class PluginSpecialAttributeManifest(PluginAttributeManifest):
    name: str = Field(..., pattern=_SPECIAL_ROLE_RE.pattern)
    required: Literal[False] = False


class PluginProcessorManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    processor_id: str = Field(
        ...,
        pattern=_LOCAL_DECLARATION_ID_RE.pattern,
        json_schema_extra=_deny_core_ids_schema(_core_processor_ids),
    )
    title: str
    consumer: dict[str, PluginPortSpecManifest]
    supplier: dict[str, PluginPortSpecManifest]
    attributes: list[PluginAttributeManifest] = Field(default_factory=list)
    description: str = ""
    capabilities: list[CapabilityId] = Field(min_length=1)

    @field_validator("processor_id")
    @classmethod
    def _processor_id_format(cls, processor_id: str) -> str:
        if not _LOCAL_DECLARATION_ID_RE.match(processor_id):
            raise ValueError(
                f"processor_id must match {_LOCAL_DECLARATION_ID_RE.pattern!r}: {processor_id!r}"
            )
        return processor_id

    @field_validator("consumer", "supplier")
    @classmethod
    def _port_roles_format(
        cls,
        ports: dict[str, PluginPortSpecManifest],
    ) -> dict[str, PluginPortSpecManifest]:
        bad = [role for role in ports if not _ROLE_RE.match(role)]
        if bad:
            raise ValueError(
                f"port roles must match {_ROLE_RE.pattern!r}: {', '.join(sorted(bad))}"
            )
        return ports

    @field_validator("capabilities")
    @classmethod
    def _capabilities_are_unique(cls, capabilities: list[str]) -> list[str]:
        return sorted(set(capabilities))

    @model_validator(mode="after")
    def _attributes_unique(self) -> PluginProcessorManifest:
        names = [attr.name for attr in self.attributes]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"duplicate processor attributes: {', '.join(duplicates)}")
        return self


class PluginSpecialInputPortSpecManifest(PluginPortSpecManifest):
    required: Literal[False] = False


class PluginProcessorExtensionManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    processor_id: str = Field(..., pattern=_CONTRACT_ID_RE.pattern)
    special_inputs: dict[
        Annotated[str, Field(pattern=_SPECIAL_ROLE_RE.pattern)],
        PluginSpecialInputPortSpecManifest,
    ] = Field(
        default_factory=dict,
        description=(
            "Plugin-qualified extension input roles. JSON Schema validates the "
            "qualified-name shape; runtime PluginManifest validation also "
            "requires the prefix to match plugin_id."
        ),
        json_schema_extra={
            "additionalProperties": False,
            "propertyNames": {"pattern": _SPECIAL_ROLE_RE.pattern},
        },
    )
    special_attributes: list[PluginSpecialAttributeManifest] = Field(
        default_factory=list,
        description=(
            "Plugin-qualified extension attributes. JSON Schema validates the "
            "qualified-name shape; runtime PluginManifest validation also "
            "requires the prefix to match plugin_id."
        ),
    )

    @field_validator("processor_id")
    @classmethod
    def _processor_id_format(cls, processor_id: str) -> str:
        if not _CONTRACT_ID_RE.match(processor_id):
            raise ValueError(
                f"processor_id must match {_CONTRACT_ID_RE.pattern!r}: {processor_id!r}"
            )
        return processor_id

    @field_validator("special_inputs")
    @classmethod
    def _special_input_roles_format(
        cls,
        ports: dict[str, PluginSpecialInputPortSpecManifest],
    ) -> dict[str, PluginSpecialInputPortSpecManifest]:
        bad = [role for role in ports if not _SPECIAL_ROLE_RE.match(role)]
        if bad:
            raise ValueError(
                "special input roles must be plugin-qualified and match "
                f"{_SPECIAL_ROLE_RE.pattern!r}: {', '.join(sorted(bad))}"
            )
        return ports

    @model_validator(mode="after")
    def _special_attributes_unique(self) -> PluginProcessorExtensionManifest:
        required_inputs = [role for role, port in self.special_inputs.items() if port.required]
        if required_inputs:
            raise ValueError(
                "special_inputs must be optional; set required=false for: "
                + ", ".join(sorted(required_inputs))
            )
        required_attributes = [attr.name for attr in self.special_attributes if attr.required]
        if required_attributes:
            raise ValueError(
                "special_attributes must be optional; set required=false for: "
                + ", ".join(sorted(required_attributes))
            )
        names = [attr.name for attr in self.special_attributes]
        unqualified = sorted(name for name in names if "." not in name)
        if unqualified:
            raise ValueError(
                "special attribute names must be plugin-qualified: " + ", ".join(unqualified)
            )
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"duplicate special attributes: {', '.join(duplicates)}")
        return self


WireList = Annotated[
    list[str],
    Field(json_schema_extra={"uniqueItems": True}),
]


class PluginPipelineStepManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str = Field(..., pattern=_ROLE_RE.pattern)
    processor: str = Field(..., pattern=_CONTRACT_ID_RE.pattern)
    attributes: dict[str, object] = Field(default_factory=dict)
    wires: dict[str, str | WireList] = Field(default_factory=dict)

    @field_validator("ref")
    @classmethod
    def _ref_format(cls, ref: str) -> str:
        if not _ROLE_RE.match(ref):
            raise ValueError(f"step ref must match {_ROLE_RE.pattern!r}: {ref!r}")
        if ref == "inputs":
            raise ValueError("'inputs' is reserved for the synthetic pipeline input source")
        return ref

    @field_validator("processor")
    @classmethod
    def _processor_format(cls, processor: str) -> str:
        if not _CONTRACT_ID_RE.match(processor):
            raise ValueError(f"processor must match {_CONTRACT_ID_RE.pattern!r}: {processor!r}")
        return processor

    @field_validator("wires")
    @classmethod
    def _wire_arrays_are_unique(
        cls,
        wires: dict[str, str | list[str]],
    ) -> dict[str, str | list[str]]:
        for role, raw in wires.items():
            if not isinstance(raw, list):
                continue
            duplicates = sorted({value for value in raw if raw.count(value) > 1})
            if duplicates:
                raise ValueError(
                    f"wires.{role} must not contain duplicate supplier reference(s): "
                    + ", ".join(duplicates)
                )
        return wires


class PluginPipelineManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pipeline_id: str = Field(
        ...,
        pattern=_LOCAL_DECLARATION_ID_RE.pattern,
        json_schema_extra=_deny_core_ids_schema(_core_pipeline_ids),
    )
    title: str
    initial_inputs: list[Annotated[str, Field(pattern=_CONTRACT_ID_RE.pattern)]] = Field(
        default_factory=lambda: ["image_sequence"],
        min_length=1,
        json_schema_extra={"uniqueItems": True},
    )
    steps: list[PluginPipelineStepManifest] = Field(min_length=1)
    description: str = ""

    @field_validator("pipeline_id")
    @classmethod
    def _pipeline_id_format(cls, pipeline_id: str) -> str:
        if not _LOCAL_DECLARATION_ID_RE.match(pipeline_id):
            raise ValueError(
                f"pipeline_id must match {_LOCAL_DECLARATION_ID_RE.pattern!r}: {pipeline_id!r}"
            )
        return pipeline_id

    @field_validator("initial_inputs")
    @classmethod
    def _initial_inputs_format(cls, inputs: list[str]) -> list[str]:
        bad = [datatype for datatype in inputs if not _CONTRACT_ID_RE.match(datatype)]
        if bad:
            raise ValueError(
                f"initial_inputs must match {_CONTRACT_ID_RE.pattern!r}: " + ", ".join(sorted(bad))
            )
        duplicates = sorted({datatype for datatype in inputs if inputs.count(datatype) > 1})
        if duplicates:
            raise ValueError(
                "initial_inputs must not contain duplicate datatype(s): " + ", ".join(duplicates)
            )
        return inputs

    @model_validator(mode="after")
    def _refs_unique(self) -> PluginPipelineManifest:
        refs = [step.ref for step in self.steps]
        duplicates = sorted({ref for ref in refs if refs.count(ref) > 1})
        if duplicates:
            raise ValueError(f"duplicate pipeline step refs: {', '.join(duplicates)}")
        return self


def _validate_plugin_owned_declaration_ids(
    *,
    plugin_id: str,
    datatypes: list[PluginDataTypeManifest],
    processors: list[PluginProcessorManifest],
    pipelines: list[PluginPipelineManifest],
) -> None:
    declarations = [
        *[("type_id", row.type_id) for row in datatypes],
        *[("processor_id", row.processor_id) for row in processors],
        *[("pipeline_id", row.pipeline_id) for row in pipelines],
    ]
    for field, value in declarations:
        if "." in value:
            raise ValueError(
                f"plugin-owned {field} {value!r} must be a local declaration id; "
                f"{plugin_id!r} is applied during registry merge"
            )


def _core_processor_ports() -> dict[
    str,
    tuple[dict[str, object], dict[str, object]],
]:
    from sfmapi.server.core.processors import PROCESSORS_BY_ID

    return {
        processor_id: (processor.consumer, processor.supplier)
        for processor_id, processor in PROCESSORS_BY_ID.items()
    }


def _plugin_processor_ports(
    processors: list[PluginProcessorManifest],
) -> dict[str, tuple[dict[str, object], dict[str, object]]]:
    return {
        processor.processor_id: (processor.consumer, processor.supplier) for processor in processors
    }


def _core_processor_attributes() -> dict[str, list[PluginAttributeManifest]]:
    from sfmapi.server.core.processors import PROCESSORS_BY_ID

    return {
        processor_id: [
            PluginAttributeManifest.model_validate(attr.contract_dict())
            for attr in processor.attributes
        ]
        for processor_id, processor in PROCESSORS_BY_ID.items()
    }


def _plugin_processor_attributes(
    processors: list[PluginProcessorManifest],
) -> dict[str, list[PluginAttributeManifest]]:
    return {processor.processor_id: list(processor.attributes) for processor in processors}


def _wire_values(raw: object, *, multiple: bool) -> list[object]:
    if multiple:
        return list(raw) if isinstance(raw, list) else [raw]
    return [raw]


def _parse_wire_ref(value: object) -> tuple[str, str] | None:
    if not isinstance(value, str) or "." not in value:
        return None
    if value.startswith("inputs."):
        port = value.removeprefix("inputs.")
        return ("inputs", port) if port else None
    ref, port = value.rsplit(".", 1)
    if not ref or not port:
        return None
    return ref, port


def _value_matches_attribute(
    attr: PluginAttributeManifest,
    value: object,
    *,
    known_datatypes: set[str] | frozenset[str] | None = None,
) -> bool:
    if attr.type == "bool":
        return isinstance(value, bool)
    if attr.type == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if attr.type == "float":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if attr.type == "str":
        return isinstance(value, str)
    if attr.type == "enum":
        return value in attr.enum
    if attr.type == "datatype-ref":
        if not isinstance(value, str):
            return False
        if known_datatypes is not None:
            return value in known_datatypes
        return value in _core_datatype_ids()
    if attr.type == "object":
        return isinstance(value, dict)
    return False


def _validate_attribute_schema(
    attr: PluginAttributeManifest,
    *,
    known_datatypes: set[str] | frozenset[str],
) -> None:
    if attr.type != "enum" and attr.enum:
        raise ValueError(f"attribute {attr.name!r} uses enum values but type is {attr.type!r}")
    if attr.enum and not all(isinstance(value, str) for value in attr.enum):
        raise ValueError(f"attribute {attr.name!r} enum values must be strings")
    if attr.type == "enum" and len(set(map(repr, attr.enum))) != len(attr.enum):
        raise ValueError(f"attribute {attr.name!r} enum values must be unique")
    if attr.type not in {"int", "float"} and (attr.min is not None or attr.max is not None):
        raise ValueError(f"attribute {attr.name!r} min/max are only valid for numeric types")
    if attr.min is not None and attr.max is not None and attr.min > attr.max:
        raise ValueError(f"attribute {attr.name!r} min must be <= max")
    if "default" in attr.model_fields_set:
        if attr.default is None:
            raise ValueError(f"attribute {attr.name!r} default cannot be null")
        if not _value_matches_attribute(
            attr,
            attr.default,
            known_datatypes=known_datatypes,
        ):
            raise ValueError(f"attribute {attr.name!r} default must match type {attr.type!r}")
        if (
            attr.type in {"int", "float"}
            and isinstance(attr.default, (int, float))
            and not isinstance(attr.default, bool)
        ):
            if attr.min is not None and attr.default < attr.min:
                raise ValueError(f"attribute {attr.name!r} default must be >= min")
            if attr.max is not None and attr.default > attr.max:
                raise ValueError(f"attribute {attr.name!r} default must be <= max")


def _validate_step_attributes(
    *,
    pipeline_id: str,
    step: PluginPipelineStepManifest,
    attributes: list[PluginAttributeManifest],
    known_datatypes: set[str] | frozenset[str],
) -> None:
    by_name = {attr.name: attr for attr in attributes}
    unknown = sorted(set(step.attributes) - set(by_name))
    if unknown:
        raise ValueError(
            f"pipeline {pipeline_id!r} step {step.ref!r} uses unknown "
            f"attribute(s): {', '.join(unknown)}"
        )
    for attr in attributes:
        if attr.name not in step.attributes:
            if attr.required:
                raise ValueError(
                    f"pipeline {pipeline_id!r} step {step.ref!r} missing "
                    f"required attribute {attr.name!r}"
                )
            continue
        value = step.attributes[attr.name]
        if value is None or not _value_matches_attribute(
            attr,
            value,
            known_datatypes=known_datatypes,
        ):
            raise ValueError(
                f"pipeline {pipeline_id!r} step {step.ref!r} attribute "
                f"{attr.name!r} must be {attr.type}"
            )
        if attr.min is not None and isinstance(value, (int, float)) and value < attr.min:
            raise ValueError(
                f"pipeline {pipeline_id!r} step {step.ref!r} attribute "
                f"{attr.name!r} must be >= {attr.min}"
            )
        if attr.max is not None and isinstance(value, (int, float)) and value > attr.max:
            raise ValueError(
                f"pipeline {pipeline_id!r} step {step.ref!r} attribute "
                f"{attr.name!r} must be <= {attr.max}"
            )


def _requires_verified_match_graph(processor_id: str, role: str) -> bool:
    return processor_id in {"map", "triangulate"} and role == "matches"


def _validate_pipeline_graph(
    *,
    pipeline: PluginPipelineManifest,
    processor_ports: dict[str, tuple[dict[str, object], dict[str, object]]],
    processor_attributes: dict[str, list[PluginAttributeManifest]],
    known_datatypes: set[str] | frozenset[str],
) -> None:
    available: list[tuple[str, str, str, bool]] = [
        ("inputs", datatype, datatype, False) for datatype in pipeline.initial_inputs
    ]
    by_wire = {f"{ref}.{port}": (datatype, verified) for ref, port, datatype, verified in available}
    for step in pipeline.steps:
        ports = processor_ports.get(step.processor)
        if ports is None:
            continue
        _validate_step_attributes(
            pipeline_id=pipeline.pipeline_id,
            step=step,
            attributes=processor_attributes.get(step.processor, []),
            known_datatypes=known_datatypes,
        )
        consumer, supplier = ports
        unknown_wires = sorted(set(step.wires) - set(consumer))
        if unknown_wires:
            raise ValueError(
                f"pipeline {pipeline.pipeline_id!r} step {step.ref!r} wires "
                f"unknown consumer ports: {', '.join(unknown_wires)}"
            )
        for role, port in consumer.items():
            datatype = str(port.datatype)  # type: ignore[attr-defined]
            required = bool(port.required)  # type: ignore[attr-defined]
            multiple = bool(port.multiple)  # type: ignore[attr-defined]
            if role in step.wires:
                raw_wire = step.wires[role]
                if isinstance(raw_wire, list) and not multiple:
                    raise ValueError(
                        f"pipeline {pipeline.pipeline_id!r} step {step.ref!r} "
                        f"port {role!r} does not accept multiple inputs"
                    )
                values = _wire_values(raw_wire, multiple=multiple)
                if not values and required:
                    raise ValueError(
                        f"pipeline {pipeline.pipeline_id!r} step {step.ref!r} "
                        f"missing required input port {role!r}"
                    )
                wire_keys: list[str] = []
                for value in values:
                    parsed = _parse_wire_ref(value)
                    if parsed is None:
                        raise ValueError(
                            f"pipeline {pipeline.pipeline_id!r} step {step.ref!r} "
                            f"wire for port {role!r} must be 'step_ref.supplier_port'"
                        )
                    supplied = by_wire.get(f"{parsed[0]}.{parsed[1]}")
                    if supplied is None:
                        raise ValueError(
                            f"pipeline {pipeline.pipeline_id!r} step {step.ref!r} "
                            f"references unknown supplier port {parsed[0]}.{parsed[1]}"
                        )
                    supplied_datatype, supplied_verified = supplied
                    if supplied_datatype != datatype:
                        raise ValueError(
                            f"pipeline {pipeline.pipeline_id!r} step {step.ref!r} "
                            f"datatype mismatch for port {role!r}: expected "
                            f"{datatype}, got {supplied_datatype}"
                        )
                    if (
                        _requires_verified_match_graph(step.processor, role)
                        and not supplied_verified
                    ):
                        raise ValueError(
                            f"pipeline {pipeline.pipeline_id!r} step {step.ref!r} "
                            f"port {role!r} requires verified match_graph input"
                        )
                    wire_keys.append(f"{parsed[0]}.{parsed[1]}")
                distinct_wire_keys = set(wire_keys)
                if multiple and len(distinct_wire_keys) != len(wire_keys):
                    raise ValueError(
                        f"pipeline {pipeline.pipeline_id!r} step {step.ref!r} "
                        f"port {role!r} does not accept duplicate inputs"
                    )
                if multiple and required and len(distinct_wire_keys) < 2:
                    raise ValueError(
                        f"pipeline {pipeline.pipeline_id!r} step {step.ref!r} "
                        f"port {role!r} requires at least two distinct inputs"
                    )
                continue

            if not required:
                continue

            candidates = [s for s in available if s[2] == datatype]
            if not candidates and required:
                raise ValueError(
                    f"pipeline {pipeline.pipeline_id!r} step {step.ref!r} "
                    f"missing input datatype {datatype!r}"
                )
            if multiple and required and len({(s[0], s[1]) for s in candidates}) == 1:
                raise ValueError(
                    f"pipeline {pipeline.pipeline_id!r} step {step.ref!r} "
                    f"port {role!r} requires at least two distinct inputs"
                )
            if candidates and len(candidates) > 1 and not multiple:
                raise ValueError(
                    f"pipeline {pipeline.pipeline_id!r} step {step.ref!r} "
                    f"has ambiguous input for port {role!r}"
                )
            if _requires_verified_match_graph(step.processor, role) and not any(
                s[3] for s in candidates
            ):
                raise ValueError(
                    f"pipeline {pipeline.pipeline_id!r} step {step.ref!r} "
                    f"port {role!r} requires verified match_graph input"
                )
        for role, port in supplier.items():
            datatype = str(port.datatype)  # type: ignore[attr-defined]
            verified = (
                step.processor == "verify" and role == "matches" and datatype == "match_graph"
            )
            available.append((step.ref, role, datatype, verified))
            by_wire[f"{step.ref}.{role}"] = (datatype, verified)


def _validate_typed_extension_graph(
    *,
    datatypes: list[PluginDataTypeManifest],
    processors: list[PluginProcessorManifest],
    pipelines: list[PluginPipelineManifest],
    processor_extensions: list[PluginProcessorExtensionManifest],
    declared_capabilities: set[str] | None = None,
    provider_capability_sets: list[set[str]] | None = None,
    extension_namespace_prefixes: set[str] | None = None,
) -> None:
    datatype_ids = [dt.type_id for dt in datatypes]
    duplicate_dts = sorted({dt for dt in datatype_ids if datatype_ids.count(dt) > 1})
    if duplicate_dts:
        raise ValueError(f"duplicate datatypes: {', '.join(duplicate_dts)}")
    core_dts = _core_datatype_ids()
    core_shadow_dts = sorted(set(datatype_ids) & core_dts)
    if core_shadow_dts:
        raise ValueError(
            f"plugin datatypes cannot redefine core datatypes: {', '.join(core_shadow_dts)}"
        )

    processor_ids = [processor.processor_id for processor in processors]
    duplicate_processors = sorted({pid for pid in processor_ids if processor_ids.count(pid) > 1})
    if duplicate_processors:
        raise ValueError(f"duplicate processors: {', '.join(duplicate_processors)}")
    core_processors = _core_processor_ids()
    core_shadow_processors = sorted(set(processor_ids) & core_processors)
    if core_shadow_processors:
        raise ValueError(
            "plugin processors cannot redefine core processors; use "
            f"processor_extensions instead: {', '.join(core_shadow_processors)}"
        )

    if declared_capabilities is not None:
        for processor in processors:
            undeclared = sorted(set(processor.capabilities) - declared_capabilities)
            if undeclared:
                raise ValueError(
                    f"processor {processor.processor_id!r} references undeclared "
                    f"capabilities: {', '.join(undeclared)}"
                )
    if provider_capability_sets is not None:
        for processor in processors:
            required = set(processor.capabilities)
            if not any(required <= caps for caps in provider_capability_sets):
                raise ValueError(
                    f"processor {processor.processor_id!r} capabilities are not "
                    "declared together by any provider"
                )

    known_dts = core_dts | set(datatype_ids)
    for processor in processors:
        for attr in processor.attributes:
            _validate_attribute_schema(attr, known_datatypes=known_dts)
    for processor in processors:
        for role, port in {**processor.consumer, **processor.supplier}.items():
            if port.datatype not in known_dts:
                raise ValueError(
                    f"processor {processor.processor_id!r} port {role!r} "
                    f"references unknown datatype {port.datatype!r}"
                )
    known_processors = core_processors | set(processor_ids)
    extension_ids = [extension.processor_id for extension in processor_extensions]
    duplicate_extensions = sorted({pid for pid in extension_ids if extension_ids.count(pid) > 1})
    if duplicate_extensions:
        raise ValueError(f"duplicate processor_extensions: {', '.join(duplicate_extensions)}")
    for extension in processor_extensions:
        if extension.processor_id not in known_processors:
            raise ValueError(
                f"processor extension references unknown processor {extension.processor_id!r}"
            )
        for role, port in extension.special_inputs.items():
            if port.datatype not in known_dts:
                raise ValueError(
                    f"processor extension {extension.processor_id!r} "
                    f"input {role!r} references unknown datatype {port.datatype!r}"
                )
        for attr in extension.special_attributes:
            _validate_attribute_schema(attr, known_datatypes=known_dts)

    if extension_namespace_prefixes is not None:
        prefixes = tuple(f"{prefix}." for prefix in sorted(extension_namespace_prefixes))
        for extension in processor_extensions:
            special_input_names = sorted(extension.special_inputs)
            special_attribute_names = sorted(attr.name for attr in extension.special_attributes)
            bad = [
                name
                for name in [*special_input_names, *special_attribute_names]
                if not name.startswith(prefixes)
            ]
            if bad:
                raise ValueError(
                    "processor extension names must use the owning plugin namespace: "
                    + ", ".join(bad)
                )

    pipeline_ids = [pipeline.pipeline_id for pipeline in pipelines]
    duplicate_pipelines = sorted({pid for pid in pipeline_ids if pipeline_ids.count(pid) > 1})
    if duplicate_pipelines:
        raise ValueError(f"duplicate pipelines: {', '.join(duplicate_pipelines)}")
    core_pipelines = _core_pipeline_ids()
    core_shadow_pipelines = sorted(set(pipeline_ids) & core_pipelines)
    if core_shadow_pipelines:
        raise ValueError(
            f"plugin pipelines cannot redefine core pipelines: {', '.join(core_shadow_pipelines)}"
        )
    processor_ports = _core_processor_ports()
    processor_ports.update(_plugin_processor_ports(processors))
    processor_attributes = _core_processor_attributes()
    processor_attributes.update(_plugin_processor_attributes(processors))
    for extension in processor_extensions:
        consumer, supplier = processor_ports[extension.processor_id]
        collisions = sorted(set(extension.special_inputs) & set(consumer))
        if collisions:
            raise ValueError(
                f"processor extension {extension.processor_id!r} duplicates "
                f"consumer port(s): {', '.join(collisions)}"
            )
        merged_consumer = dict(consumer)
        merged_consumer.update(extension.special_inputs)
        processor_ports[extension.processor_id] = (merged_consumer, supplier)

        existing_attrs = processor_attributes.setdefault(extension.processor_id, [])
        existing_names = {attr.name for attr in existing_attrs}
        duplicate_attrs = sorted(
            attr.name for attr in extension.special_attributes if attr.name in existing_names
        )
        if duplicate_attrs:
            raise ValueError(
                f"processor extension {extension.processor_id!r} duplicates "
                f"attribute(s): {', '.join(duplicate_attrs)}"
            )
        processor_attributes[extension.processor_id] = [
            *existing_attrs,
            *extension.special_attributes,
        ]
    for pipeline in pipelines:
        unknown_inputs = sorted(set(pipeline.initial_inputs) - known_dts)
        if unknown_inputs:
            raise ValueError(
                f"pipeline {pipeline.pipeline_id!r} references unknown initial "
                f"datatype(s): {', '.join(unknown_inputs)}"
            )
        for step in pipeline.steps:
            if step.processor not in known_processors:
                raise ValueError(
                    f"pipeline {pipeline.pipeline_id!r} step {step.ref!r} "
                    f"references unknown processor {step.processor!r}"
                )
        _validate_pipeline_graph(
            pipeline=pipeline,
            processor_ports=processor_ports,
            processor_attributes=processor_attributes,
            known_datatypes=known_dts,
        )
