"""Resolve white-table references once and build the output using armodel."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path

from armodel import (
    ARLiteral, ARXMLWriter, AUTOSAR, ApplicationSwComponentType,
    AtomicSwComponentType, ComplexDeviceDriverSwComponentType,
    ComponentInSystemInstanceRef, CompositionSwComponentType,
    EcuAbstractionSwComponentType, PPortInCompositionInstanceRef, PPortPrototype,
    RPortInCompositionInstanceRef, RPortPrototype, RefType,
    SenderReceiverToSignalGroupMapping, SenderReceiverToSignalMapping,
    ServiceSwComponentType, TRefType, VariableDataPrototypeInSystemInstanceRef,
)

from armodel.models.M2.AUTOSARTemplates.AutosarTopLevelStructure import AUTOSARDoc
from armodel.models.M2.AUTOSARTemplates.SWComponentTemplate.PortInterface import (
    DataPrototypeMapping, PortInterface, SenderReceiverInterface,
)
from armodel.models.M2.AUTOSARTemplates.CommonStructure.ImplementationDataTypes import (
    ImplementationDataType, ImplementationDataTypeElement,
)
from armodel.models.M2.AUTOSARTemplates.SWComponentTemplate.Datatype.Datatypes import (
    ApplicationArrayDataType, ApplicationDataType, ApplicationPrimitiveDataType,
    ApplicationRecordDataType, DataTypeMappingSet,
)
from armodel.models.M2.MSR.AsamHdo.BaseTypes import SwBaseType

from .white_table_reader import CompositionInput, TableRow, read_composition_input
from .arxml_model import ArxmlModel

DEFAULT_AR_RELEASE = "R19-11"
COMPONENT_DESTINATIONS = (
    (CompositionSwComponentType, "COMPOSITION-SW-COMPONENT-TYPE"),
    (ApplicationSwComponentType, "APPLICATION-SW-COMPONENT-TYPE"),
    (ComplexDeviceDriverSwComponentType, "COMPLEX-DEVICE-DRIVER-SW-COMPONENT-TYPE"),
    (EcuAbstractionSwComponentType, "ECU-ABSTRACTION-SW-COMPONENT-TYPE"),
    (ServiceSwComponentType, "SERVICE-SW-COMPONENT-TYPE"),
)


def _reference(destination: str, path: str) -> RefType:
    return RefType().setDest(destination).setValue(path)


def _value(reference) -> str:
    return str(reference.getValue() or "").strip() if reference is not None else ""


def _type_value(value) -> str:
    if hasattr(value, "getValue"):
        value = value.getValue()
    return "" if value is None else str(value).strip()


def _positive_size(value, label: str) -> int:
    try:
        size = Decimal(_type_value(value))
        if size.is_finite() and size > 0 and size == size.to_integral_value():
            return int(size)
    except InvalidOperation:
        pass
    raise ValueError(f"Missing or invalid {label}: {_type_value(value)}")


class _UnresolvedDataType(ValueError):
    pass


@dataclass(frozen=True)
class _Component:
    name: str
    type_path: str
    destination: str
    instance_path: str
    implementation: str
    action: str


@dataclass(frozen=True)
class _Endpoint:
    component: _Component
    port_path: str
    destination: str
    interface_path: str
    interface_destination: str

    def composition_iref(self):
        component_ref = _reference("SW-COMPONENT-PROTOTYPE", self.component.instance_path)
        port_ref = _reference(self.destination, self.port_path)
        if self.destination == "P-PORT-PROTOTYPE":
            return (PPortInCompositionInstanceRef().setContextComponentRef(component_ref)
                    .setTargetPPortRef(port_ref))
        return (RPortInCompositionInstanceRef().setContextComponentRef(component_ref)
                .setTargetRPortRef(port_ref))


@dataclass(frozen=True)
class _Assembly:
    name: str
    provider: _Endpoint
    requester: _Endpoint
    mapping_path: str = ""


@dataclass(frozen=True)
class _InterfaceMapping:
    name: str
    elements: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class _Delegation:
    name: str
    inner: _Endpoint
    outer_name: str


@dataclass(frozen=True)
class _SignalMapping:
    endpoint: _Endpoint
    data_path: str
    signal_path: str
    direction: str
    group: bool


class _Resolver:
    def __init__(
        self, document: AUTOSAR, data: CompositionInput, replace: bool,
        interface_mismatch: str = "error",
        interface_mappings: list[dict] | None = None,
    ) -> None:
        if interface_mismatch not in ("error", "skip", "map"):
            raise ValueError("interfaceMismatch must be error, skip or map")
        self.document = document
        self.data = data
        self.replace = replace
        self.interface_mismatch = interface_mismatch
        self.interface_mapping_specs: dict[tuple[str, str], dict[str, str]] = {}
        if interface_mappings is not None and not isinstance(interface_mappings, list):
            raise ValueError("interfaceMappings must be a list")
        for spec in interface_mappings or []:
            if not isinstance(spec, dict) or set(spec) != {"providerInterface", "requesterInterface", "dataElements"}:
                raise ValueError("interfaceMappings entries require providerInterface, requesterInterface and dataElements")
            first, second, elements = spec["providerInterface"], spec["requesterInterface"], spec["dataElements"]
            if not all(isinstance(path, str) and path.startswith("/") for path in (first, second)):
                raise ValueError("interfaceMappings interface paths must be absolute AUTOSAR paths")
            if (not isinstance(elements, dict) or not elements
                    or not all(isinstance(key, str) and key and isinstance(value, str) and value
                               for key, value in elements.items())):
                raise ValueError("interfaceMappings.dataElements must map provider names to requester names")
            key = (first, second)
            if key in self.interface_mapping_specs:
                raise ValueError(f"Duplicate interfaceMappings entry: {first} -> {second}")
            self.interface_mapping_specs[key] = elements
        self.interface_mappings: dict[str, _InterfaceMapping] = {}
        self.signal_model = ArxmlModel(document, [], [])
        self.composition_path = data.general["Composition"].rstrip("/")
        parts = self.composition_path.split("/")
        if not self.composition_path.startswith("/") or len(parts) < 3 or not all(parts[1:]):
            raise ValueError("General.Composition must be an absolute AUTOSAR path with a package")
        self.context_components: list[str] = []
        self.system_category = "SYSTEM_DESCRIPTION"
        self.system_version = "1.0.0"
        self.root_type = ""
        self.components: dict[str, _Component] = {}
        self.aliases: dict[str, _Component] = {}
        self.ambiguous_aliases: set[str] = set()
        self.ports: dict[tuple[str, str], _Endpoint] = {}
        self.assemblies: dict[str, _Assembly] = {}
        self.delegations: dict[str, _Delegation] = {}
        self.outer_ports: dict[str, _Endpoint] = {}
        self.existing_outer_ports: set[str] = set()
        self.signals: list[_SignalMapping] = []
        self.objects: dict[tuple[str, str], object] = {}

    def lookup(self, method: str, path: str):
        key = (method, path)
        if key not in self.objects:
            self.objects[key] = getattr(self.document, method)(path)
        return self.objects[key]

    def resolve_components(self) -> None:
        for row in self.data.tables["Components"]:
            values = row.values
            if not all(values.values()):
                raise ValueError(f"{row.location}: all component fields are required")
            name, type_path = values["Prototype"], values["Type"]
            action = values["Action"].lower()
            if action not in ("add", "reference"):
                raise ValueError(f"{row.location}: invalid Action '{values['Action']}'")
            if "/" in name and (action == "add" or not name.startswith("/")):
                raise ValueError(f"{row.location}: Add requires a prototype SHORT-NAME")
            component_type = self.lookup("find", type_path)
            destination = next((dest for kind, dest in COMPONENT_DESTINATIONS
                                if isinstance(component_type, kind)), "")
            if not destination:
                raise ValueError(f"{row.location}: missing or unsupported component type '{type_path}'")
            implementation = ""
            if action == "add" and isinstance(component_type, AtomicSwComponentType):
                behavior = component_type.getInternalBehavior()
                impl = self.lookup("getImplementation", behavior.getFullName()) if behavior else None
                if impl is not None:
                    implementation = impl.getFullName()
                else:
                    raise ValueError(f"{row.location}: no implementation for {type_path}")
            instance_path = name if name.startswith("/") else f"{self.composition_path}/{name}"
            if instance_path.rsplit("/", 1)[0] != self.composition_path:
                raise ValueError(f"{row.location}: prototype is outside target composition: {instance_path}")
            existing = self.lookup("find", instance_path)
            if action == "reference" or existing is not None:
                if existing is None or not hasattr(existing, "getTypeTRef"):
                    raise ValueError(f"{row.location}: Reference prototype does not exist: {instance_path}")
                if _value(existing.getTypeTRef()) != type_path:
                    raise ValueError(f"{row.location}: prototype type mismatch: {instance_path}")
            name = instance_path.rsplit("/", 1)[-1]
            component = _Component(name, type_path, destination, instance_path, implementation, action)
            if name in self.components and self.components[name] != component:
                raise ValueError(f"{row.location}: conflicting prototype '{name}'")
            self.components[name] = component
            for alias in (values["SW-C"], name, instance_path):
                if alias in self.aliases and self.aliases[alias] != component:
                    raise ValueError(f"{row.location}: ambiguous component alias '{alias}'")
                self.aliases[alias] = component
        primary_aliases = set(self.aliases)
        for component in self.components.values():
            for alias in (component.type_path, component.type_path.rsplit("/", 1)[-1]):
                if alias in primary_aliases or alias in self.ambiguous_aliases:
                    continue
                if alias in self.aliases and self.aliases[alias] != component:
                    del self.aliases[alias]
                    self.ambiguous_aliases.add(alias)
                else:
                    self.aliases[alias] = component

    def endpoint(self, component_name: str, port_name: str, expected: str = "") -> _Endpoint:
        if component_name in self.ambiguous_aliases:
            raise ValueError(f"ambiguous component '{component_name}'; use its prototype name")
        if component_name not in self.aliases:
            raise ValueError(f"unknown component '{component_name}'")
        component = self.aliases[component_name]
        key = (component.name, port_name)
        if key not in self.ports:
            port_path = f"{component.type_path}/{port_name}"
            port = self.lookup("findPort", port_path)
            if isinstance(port, PPortPrototype):
                destination = "P-PORT-PROTOTYPE"
                interface = port.getProvidedInterfaceTRef()
            elif isinstance(port, RPortPrototype):
                destination = "R-PORT-PROTOTYPE"
                interface = port.getRequiredInterfaceTRef()
            else:
                raise ValueError(f"missing or unsupported port '{port_path}'")
            self.ports[key] = _Endpoint(component, port.getFullName(), destination,
                                        _value(interface), str(interface.getDest() or "") if interface else "")
        endpoint = self.ports[key]
        if expected and endpoint.destination != expected:
            raise ValueError(f"port '{endpoint.port_path}' must be {expected}")
        if (not endpoint.interface_path or not endpoint.interface_destination
                or not isinstance(self.lookup("find", endpoint.interface_path), PortInterface)):
            raise ValueError(
                f"port '{endpoint.port_path}' has a missing or invalid interface: '{endpoint.interface_path}'")
        return endpoint

    def connector_name(self, row: TableRow, default: str) -> str:
        name = default if self.replace or not row.values["Connection"] else row.values["Connection"]
        return name[:255]

    def type_signature(self, reference, component: _Component, active: tuple[str, ...] = ()) -> tuple:
        path = _value(reference)
        if not path or path in active:
            raise ValueError(f"Missing or cyclic data type reference: {' -> '.join(active + (path,))}")
        data_type = self.lookup("find", path)
        if data_type is None:
            raise ValueError(f"Data type is not defined: {path}")
        destinations = {
            "SW-BASE-TYPE": SwBaseType,
            "IMPLEMENTATION-DATA-TYPE": ImplementationDataType,
            "APPLICATION-PRIMITIVE-DATA-TYPE": ApplicationPrimitiveDataType,
            "APPLICATION-ARRAY-DATA-TYPE": ApplicationArrayDataType,
            "APPLICATION-RECORD-DATA-TYPE": ApplicationRecordDataType,
        }
        destination = str(reference.getDest() or "")
        expected = destinations.get(destination)
        if expected is None or not isinstance(data_type, expected):
            raise ValueError(f"Unsupported or incorrect data type DEST '{destination}': {path}")
        active = active + (path,)
        if isinstance(data_type, SwBaseType):
            definition = data_type.getBaseTypeDefinition()
            if definition is None:
                raise ValueError(f"Missing base type definition: {path}")
            width = _positive_size(definition.getBaseTypeSize(), f"BASE-TYPE-SIZE of {path}")
            encoding = _type_value(definition.getBaseTypeEncoding()).upper()
            if encoding not in {"NONE", "2C", "1C", "SM", "IEEE754", "BOOLEAN"}:
                raise ValueError(f"Missing or unsupported BASE-TYPE-ENCODING '{encoding}': {path}")
            return ("scalar", width, encoding, _type_value(definition.getByteOrder()))
        if isinstance(data_type, ApplicationDataType):
            component_type = self.lookup("find", component.type_path)
            behavior = component_type.getInternalBehavior() if isinstance(component_type, AtomicSwComponentType) else None
            mapped: dict[str, RefType] = {}
            for mapping_ref in behavior.getDataTypeMappingRefs() if behavior is not None else []:
                mapping_set = self.lookup("find", _value(mapping_ref))
                if not isinstance(mapping_set, DataTypeMappingSet):
                    raise ValueError(f"DataTypeMappingSet is not defined: {_value(mapping_ref)}")
                for mapping in mapping_set.getDataTypeMaps():
                    if _value(mapping.getApplicationDataTypeRef()) == path:
                        implementation = mapping.getImplementationDataTypeRef()
                        mapped[_value(implementation)] = implementation
            if len(mapped) > 1:
                raise ValueError(f"Ambiguous implementation type for {path} in {component.type_path}: {sorted(mapped)}")
            if mapped:
                return self.type_signature(next(iter(mapped.values())), component, active)
            if not isinstance(data_type, ApplicationPrimitiveDataType):
                raise ValueError(f"No component-scoped implementation mapping for application type: {path}")
            return self._property_type_signature(data_type, component, active)
        return self._implementation_signature(data_type, component, active)

    def _property_type_signature(self, data_type, component: _Component, active: tuple[str, ...]) -> tuple:
        props = data_type.getSwDataDefProps()
        path = data_type.getFullName()
        if props is None:
            raise ValueError(f"Cannot resolve final data type; SW-DATA-DEF-PROPS is missing: {path}")
        if props.getSwPointerTargetProps() is not None or props.getSwBitRepresentation() is not None:
            raise ValueError(f"Pointer or bit-field data type is not supported for interface mapping: {path}")
        references = [reference for reference in (props.getImplementationDataTypeRef(), props.getBaseTypeRef())
                      if _value(reference)]
        if not references:
            raise ValueError(f"Cannot resolve final data type; no implementation/base type reference: {path}")
        signatures = [self.type_signature(reference, component, active) for reference in references]
        if any(signature != signatures[0] for signature in signatures[1:]):
            raise ValueError(f"Conflicting implementation/base type definitions: {path}")
        return signatures[0]

    def _implementation_signature(self, data_type, component: _Component, active: tuple[str, ...]) -> tuple:
        category = _type_value(data_type.getCategory()).upper()
        path = data_type.getFullName()
        if category in ("VALUE", "TYPE_REFERENCE"):
            if data_type.getSubElements():
                raise ValueError(f"Unexpected sub-elements on scalar/alias type: {path}")
            return self._property_type_signature(data_type, component, active)
        if category not in ("ARRAY", "STRUCTURE"):
            raise ValueError(f"Unsupported implementation type category '{category}': {path}")
        if isinstance(data_type, ImplementationDataType) and (
                _type_value(data_type.getDynamicArraySizeProfile())
                or _type_value(data_type.getIsStructWithOptionalElement()).lower() not in ("", "false", "0")):
            raise ValueError(f"Dynamic or optional composite type cannot be mapped automatically: {path}")
        elements = data_type.getSubElements()
        if not elements or (category == "ARRAY" and len(elements) != 1):
            raise ValueError(f"Invalid {category} data type elements: {path}")
        signatures = []
        for element in elements:
            if not isinstance(element, ImplementationDataTypeElement):
                raise ValueError(f"Unsupported data type element in {path}")
            if _type_value(element.getIsOptional()).lower() not in ("", "false", "0"):
                raise ValueError(f"Optional data type element cannot be mapped automatically: {element.getFullName()}")
            if category == "ARRAY":
                if _type_value(element.getArraySizeSemantics()) != "FIXED-SIZE":
                    raise ValueError(f"Only fixed-size arrays support interface mapping: {path}")
                size = _positive_size(element.getArraySize(), f"ARRAY-SIZE of {path}")
            else:
                if element.getArraySize() is not None:
                    raise ValueError(f"Array dimensions require an explicit ARRAY type: {element.getFullName()}")
                size = None
            signature = self._implementation_signature(element, component, active)
            signatures.append((element.getShortName() if category == "STRUCTURE" else size, signature))
        return (category.lower(), tuple(signatures))

    def validate_element_types(self, first_element, second_element, provider: _Endpoint, requester: _Endpoint) -> None:
        first_type, second_type = first_element.getTypeTRef(), second_element.getTypeTRef()
        try:
            for element in (first_element, second_element):
                props = element.getSwDataDefProps()
                if props is not None and (
                        props.getSwBitRepresentation() is not None or props.getSwPointerTargetProps() is not None
                        or _value(props.getBaseTypeRef()) or _value(props.getImplementationDataTypeRef())):
                    raise ValueError(f"Element-level type overrides are not supported: {element.getFullName()}")
            first_signature = self.type_signature(first_type, provider.component)
            second_signature = self.type_signature(second_type, requester.component)
        except ValueError as exc:
            raise _UnresolvedDataType(
                f"Cannot resolve interface data types: {first_element.getFullName()} -> "
                f"{second_element.getFullName()}: {exc}") from exc
        if first_signature != second_signature:
            raise ValueError(
                f"Interface final data type mismatch (bit width, encoding/signedness or shape): "
                f"{first_element.getFullName()} ({_value(first_type)}: {first_signature}) -> "
                f"{second_element.getFullName()} ({_value(second_type)}: {second_signature})")

    def interface_mapping(self, provider: _Endpoint, requester: _Endpoint) -> str:
        first = self.lookup("find", provider.interface_path)
        second = self.lookup("find", requester.interface_path)
        if (not isinstance(first, SenderReceiverInterface) or not isinstance(second, SenderReceiverInterface)
                or provider.interface_destination != "SENDER-RECEIVER-INTERFACE"
                or requester.interface_destination != "SENDER-RECEIVER-INTERFACE"):
            raise ValueError("interfaceMismatch=map currently supports only SenderReceiverInterface pairs")
        first_elements = {element.getShortName(): element for element in first.getDataElements()}
        second_elements = {element.getShortName(): element for element in second.getDataElements()}
        pairs = self.interface_mapping_specs.get((provider.interface_path, requester.interface_path))
        if pairs is None:
            if not first_elements or set(first_elements) != set(second_elements):
                raise ValueError(
                    f"Cannot infer interface mapping {provider.interface_path} -> {requester.interface_path}; "
                    "configure interfaceMappings.dataElements explicitly")
            pairs = {name: name for name in first_elements}
        if (set(pairs) != set(first_elements) or set(pairs.values()) != set(second_elements)
                or len(set(pairs.values())) != len(pairs)):
            raise ValueError("interfaceMappings.dataElements must cover both interfaces exactly once")
        references: list[tuple[str, str]] = []
        for first_name, second_name in sorted(pairs.items()):
            first_element, second_element = first_elements[first_name], second_elements[second_name]
            self.validate_element_types(first_element, second_element, provider, requester)
            references.append((first_element.getFullName(), second_element.getFullName()))
        identity = repr(references).encode("utf-8")
        name = "im_" + sha256(identity).hexdigest()[:20]
        mapping = _InterfaceMapping(name, tuple(references))
        if name in self.interface_mappings and self.interface_mappings[name] != mapping:
            raise ValueError(f"Conflicting generated interface mapping: {name}")
        path = f"{self.composition_path}_InterfaceMappings/{name}"
        if self.lookup("find", path) is not None:
            raise ValueError(f"Generated interface mapping already exists in input: {path}")
        self.interface_mappings[name] = mapping
        return path

    def add_assembly(self, row: TableRow) -> None:
        values = row.values
        provider = self.endpoint(values["Provider SW-C"], values["PPort"], "P-PORT-PROTOTYPE")
        requester = self.endpoint(values["Requester SW-C"], values["RPort"], "R-PORT-PROTOTYPE")
        mapping_path = ""
        if (provider.interface_path, provider.interface_destination) != (
                requester.interface_path, requester.interface_destination):
            message = (f"interface mismatch: {provider.port_path} ({provider.interface_path}) -> "
                       f"{requester.port_path} ({requester.interface_path})")
            if self.interface_mismatch == "skip":
                logging.warning("Skip %s: %s", row.location, message)
                return
            if self.interface_mismatch == "error":
                raise ValueError(message)
            mapping_path = self.interface_mapping(provider, requester)
        else:
            interface = self.lookup("find", provider.interface_path)
            if isinstance(interface, SenderReceiverInterface):
                for element in interface.getDataElements():
                    try:
                        self.validate_element_types(element, element, provider, requester)
                    except _UnresolvedDataType as exc:
                        # 同一接口无需映射，类型无法唯一解析时只提示。
                        logging.warning("%s: %s", row.location, exc)
        name = self.connector_name(row, f"ac_{provider.component.name}_{values['PPort']}_"
                                   f"{requester.component.name}_{values['RPort']}")
        connector = _Assembly(name, provider, requester, mapping_path)
        if name in self.delegations or (name in self.assemblies and self.assemblies[name] != connector):
            raise RuntimeError(f"{row.location}: conflicting connector name '{name}'")
        self.assemblies[name] = connector

    def add_delegation(self, row: TableRow) -> None:
        values = row.values
        inner = self.endpoint(values["Inner SW-C"], values["Inner Port"])
        outer = values["Outer Port"]
        if not outer or "/" in outer or not inner.interface_path or not inner.interface_destination:
            raise ValueError("delegation requires an outer port SHORT-NAME and an inner interface reference")
        existing = self.outer_ports.get(outer)
        if existing and (existing.destination, existing.interface_path, existing.interface_destination) != (
                inner.destination, inner.interface_path, inner.interface_destination):
            raise RuntimeError(f"{row.location}: conflicting outer port '{outer}'")
        port = self.lookup("findPort", f"{self.composition_path}/{outer}")
        if port is not None:
            if isinstance(port, PPortPrototype):
                current = ("P-PORT-PROTOTYPE", port.getProvidedInterfaceTRef())
            elif isinstance(port, RPortPrototype):
                current = ("R-PORT-PROTOTYPE", port.getRequiredInterfaceTRef())
            else:
                raise ValueError(f"unsupported outer port '{self.composition_path}/{outer}'")
            if (current[0], _value(current[1])) != (inner.destination, inner.interface_path):
                raise ValueError(
                    f"existing outer port '{self.composition_path}/{outer}' ({current[0]}, {_value(current[1])}) "
                    f"does not match inner port ({inner.destination}, {inner.interface_path})")
            self.existing_outer_ports.add(outer)
        name = self.connector_name(row, f"dc_{outer}_{inner.component.name}{values['Inner Port']}")
        connector = _Delegation(name, inner, outer)
        if name in self.assemblies or (name in self.delegations and self.delegations[name] != connector):
            raise RuntimeError(f"{row.location}: conflicting connector name '{name}'")
        self.delegations[name] = connector
        self.outer_ports[outer] = inner

    def add_signal(self, row: TableRow) -> None:
        values = row.values
        group = row.sheet == "SenderReceiverToSignalGroup"
        direction = "" if group else values["Direction"].upper()
        if not group and direction not in ("IN", "OUT"):
            raise ValueError(f"invalid signal direction '{direction}'")
        expected = "" if group else {"IN": "R-PORT-PROTOTYPE", "OUT": "P-PORT-PROTOTYPE"}[direction]
        endpoint = self.endpoint(values["SW-C"], values["Port"], expected)
        if not isinstance(self.lookup("find", endpoint.interface_path), SenderReceiverInterface):
            raise ValueError("signal mapping requires a SenderReceiverInterface")
        if not endpoint.interface_path or not values["DataElement"]:
            raise ValueError("signal mapping requires an interface reference and DataElement")
        data_path = f"{endpoint.interface_path}/{values['DataElement']}"
        data_element = self.lookup("findVariableDataPrototype", data_path)
        if data_element is None:
            raise ValueError(f"DataElement '{data_path}' is not defined")
        signal_path = values["SignalGroup" if group else "Signal"]
        if not group:
            self.signal_model.validate_signal_mapping(signal_path, direction, self.data.general["EcuInstance"])
        signal = self.lookup("findSystemSignalGroup" if group else "findSystemSignal", signal_path)
        if signal is None:
            raise ValueError(f"signal{' group' if group else ''} '{signal_path}' is not defined")
        if group:
            type_path = _value(data_element.getTypeTRef())
            data_type = self.lookup("findImplementationDataType", type_path)
            if data_type is None or _value(data_type.getCategory()) != "STRUCTURE":
                raise ValueError(f"signal group DataElement '{data_path}' requires a STRUCTURE implementation type")
        self.signals.append(_SignalMapping(endpoint, data_path, signal.getFullName(), direction, group))

    def resolve_system(self) -> None:
        """从已加载的同路径 System 读取 CATEGORY/版本/根组合，并求根组合到目标 Composition 的实例链。"""

        general = self.data.general
        system = self.lookup("find", general["System"]) if general["System"].startswith("/") else None
        if system is not None:
            self.system_category = _value(system.getCategory()) or self.system_category
            self.system_version = _type_value(system.getSystemVersion())
        root = self.lookup("find", general["RootSwCompositionPrototype"])
        if root is None or not hasattr(root, "getSoftwareCompositionTRef"):
            raise ValueError(f"RootSwCompositionPrototype is not defined: {general['RootSwCompositionPrototype']}")
        self.root_type = _value(root.getSoftwareCompositionTRef())
        chains = self.composition_chains(self.root_type, ())
        if len(chains) != 1:
            raise ValueError(
                f"General.Composition {self.composition_path} must be instantiated exactly once under "
                f"{self.root_type}, found {len(chains)}")
        self.context_components = list(chains[0])

    def composition_chains(self, type_path: str, chain: tuple[str, ...]) -> list[tuple[str, ...]]:
        if type_path == self.composition_path:
            return [chain]
        composition = self.lookup("find", type_path)
        if not isinstance(composition, CompositionSwComponentType) or len(chain) > 16:
            return []
        chains = []
        for prototype in composition.getComponents():
            chains += self.composition_chains(_value(prototype.getTypeTRef()), chain + (prototype.getFullName(),))
        return chains

    def resolve(self) -> None:
        self.resolve_components()
        handlers = {
            "AssemblySwConnection": self.add_assembly,
            "DelegationSwConnection": self.add_delegation,
            "SenderReceiverToSignal": self.add_signal,
            "SenderReceiverToSignalGroup": self.add_signal,
        }
        for sheet, handler in handlers.items():
            for row in self.data.tables[sheet]:
                try:
                    handler(row)
                except ValueError as exc:
                    raise ValueError(f"{row.location}: {exc}") from exc


def _component_iref(component: _Component, root: str, context: list[str]) -> ComponentInSystemInstanceRef:
    iref = ComponentInSystemInstanceRef().setContextCompositionRef(
        _reference("ROOT-SW-COMPOSITION-PROTOTYPE", root))
    for path in context:
        iref.addContextComponentRef(_reference("SW-COMPONENT-PROTOTYPE", path))
    return iref.setTargetComponentRef(_reference("SW-COMPONENT-PROTOTYPE", component.instance_path))


class _Writer(ARXMLWriter):
    # armodel 不写 ComponentInSystemInstanceRef 的 CONTEXT-COMPONENT-REF，嵌套 Composition 需要它。
    def setComponentInSystemInstanceRef(self, element, tag_name, ref):
        if ref is not None:
            child_element = ET.SubElement(element, tag_name)
            self.writeARObjectAttributes(child_element, ref)
            self.setChildElementOptionalRefType(child_element, "BASE-REF", ref.getBaseRef())
            self.setChildElementOptionalRefType(child_element, "CONTEXT-COMPOSITION-REF", ref.getContextCompositionRef())
            for context in ref.getContextComponentRefs():
                self.setChildElementOptionalRefType(child_element, "CONTEXT-COMPONENT-REF", context)
            self.setChildElementOptionalRefType(child_element, "TARGET-COMPONENT-REF", ref.getTargetComponentRef())


def _write_model(
    document: AUTOSARDoc, resolved: _Resolver, system_mapping: str | None, generate_system: bool,
) -> None:
    parts = resolved.composition_path.strip("/").split("/")
    package = document.createARPackage(parts[0])
    for package_name in parts[1:-1]:
        package = package.createARPackage(package_name)
    composition = package.createCompositionSwComponentType(parts[-1])
    if resolved.interface_mappings:
        mapping_set = package.createPortInterfaceMappingSet(f"{parts[-1]}_InterfaceMappings")
        for name, definition in sorted(resolved.interface_mappings.items()):
            interface_mapping = mapping_set.createVariableAndParameterInterfaceMapping(name)
            for first, second in definition.elements:
                element_mapping = DataPrototypeMapping()
                element_mapping.setFirstDataPrototypeRef(_reference("VARIABLE-DATA-PROTOTYPE", first))
                element_mapping.setSecondDataPrototypeRef(_reference("VARIABLE-DATA-PROTOTYPE", second))
                interface_mapping.addDataMapping(element_mapping)
    added = sorted((component for component in resolved.components.values() if component.action == "add"),
                   key=lambda component: component.name)
    for component in added:
        prototype = composition.createSwComponentPrototype(component.name)
        prototype.setTypeTRef(TRefType().setDest(component.destination).setValue(component.type_path))
    for name, endpoint in sorted(resolved.outer_ports.items()):
        if name in resolved.existing_outer_ports:
            continue
        interface = TRefType().setDest(endpoint.interface_destination).setValue(endpoint.interface_path)
        if endpoint.destination == "P-PORT-PROTOTYPE":
            composition.createPPortPrototype(name).setProvidedInterfaceTRef(interface)
        else:
            composition.createRPortPrototype(name).setRequiredInterfaceTRef(interface)
    for name, connector in sorted(resolved.assemblies.items()):
        assembly = composition.createAssemblySwConnector(name)
        assembly.setProviderIRef(connector.provider.composition_iref())
        assembly.setRequesterIRef(connector.requester.composition_iref())
        if connector.mapping_path:
            assembly.setMappingRef(_reference("VARIABLE-AND-PARAMETER-INTERFACE-MAPPING", connector.mapping_path))
    for name, connector in sorted(resolved.delegations.items()):
        delegation = composition.createDelegationSwConnector(name)
        delegation.setInnerPortIRref(connector.inner.composition_iref())
        delegation.setOuterPortRef(_reference(connector.inner.destination,
                                              f"{resolved.composition_path}/{connector.outer_name}"))

    if not generate_system:
        if resolved.signals:
            raise ValueError("SenderReceiverToSignal mappings require a SYSTEM; set config.generateSystem to true")
        return

    general = resolved.data.general
    system_path = general["System"]
    if system_path.startswith("/"):
        system_parts = system_path.strip("/").split("/")
        package = document.createARPackage(system_parts[0])
        for package_name in system_parts[1:-1]:
            package = package.createARPackage(package_name)
        system_path = system_parts[-1]
    system = package.createSystem(system_path)
    system.setCategory(resolved.system_category)
    if resolved.system_version:
        system.setSystemVersion(ARLiteral().setValue(resolved.system_version))
    system.addFibexElementRef(_reference("ECU-INSTANCE", general["EcuInstance"]))
    mapping_name = system_mapping or f"{parts[-1]}Impl"
    mapping = system.createSystemMapping(f"SystemMapping_{mapping_name}")
    root = general["RootSwCompositionPrototype"]
    if root.rsplit("/", 1)[0] == system.getFullName():
        system.createRootSoftwareComposition(root.rsplit("/", 1)[-1]).setSoftwareCompositionTRef(
            TRefType().setDest("COMPOSITION-SW-COMPONENT-TYPE").setValue(resolved.root_type))
    context = resolved.context_components
    ecu_mapping = mapping.createSwcToEcuMapping("SwcToEcuMapping")
    ecu_mapping.setEcuInstanceRef(_reference("ECU-INSTANCE", general["EcuInstance"]))
    for component in added:
        ecu_mapping.addComponentIRef(_component_iref(component, root, context))
        if component.implementation:
            impl_mapping = mapping.createSwcToImplMapping(f"{component.name}_ImplMapping")
            impl_mapping.setComponentImplementationRef(_reference("SWC-IMPLEMENTATION", component.implementation))
            impl_mapping.addComponentIRef(_component_iref(component, root, context))
    for signal in resolved.signals:
        endpoint = signal.endpoint
        iref = VariableDataPrototypeInSystemInstanceRef()
        iref.setContextCompositionRef(_reference("ROOT-SW-COMPOSITION-PROTOTYPE", root))
        for path in context:
            iref.addContextComponentRef(_reference("SW-COMPONENT-PROTOTYPE", path))
        iref.addContextComponentRef(_reference("SW-COMPONENT-PROTOTYPE", endpoint.component.instance_path))
        iref.setContextPortRef(_reference(endpoint.destination, endpoint.port_path))
        iref.setTargetDataPrototypeRef(_reference("VARIABLE-DATA-PROTOTYPE", signal.data_path))
        if signal.group:
            group_mapping = SenderReceiverToSignalGroupMapping()
            group_mapping.setDataElementIRef(iref)
            group_mapping.setSignalGroupRef(_reference("SYSTEM-SIGNAL-GROUP", signal.signal_path))
            mapping.addDataMapping(group_mapping)
        else:
            signal_mapping = SenderReceiverToSignalMapping()
            signal_mapping.setDataElementIRef(iref)
            signal_mapping.setCommunicationDirection(ARLiteral().setValue(signal.direction))
            signal_mapping.setSystemSignalRef(_reference("SYSTEM-SIGNAL", signal.signal_path))
            mapping.addDataMapping(signal_mapping)


def generate_composition_arxml(
    document: AUTOSAR,
    white_table: Path,
    output_file: Path,
    system_mapping: str | None = None,
    replace: bool = True,
    ar_release: str = DEFAULT_AR_RELEASE,
    interface_mismatch: str = "error",
    interface_mappings: list[dict] | None = None,
    generate_system: bool = True,
) -> Path:
    data = read_composition_input(white_table)
    resolved = _Resolver(document, data, replace, interface_mismatch, interface_mappings)
    resolved.resolve()
    if generate_system:
        resolved.resolve_system()
    # 写到独立文档，保留已加载的模型供后续配置复用。
    output = AUTOSARDoc()
    output.setARRelease(ar_release)
    _write_model(output, resolved, system_mapping, generate_system)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    _Writer().save(str(output_file), output)
    logging.info("Composition ARXML done: %s assemblies, %s delegations, %s signal mappings -> %s",
                 len(resolved.assemblies), len(resolved.delegations), len(resolved.signals), output_file)
    return output_file
