"""解析白表中的组件、端口、连接和信号映射引用，并用 armodel 生成 Composition/System ARXML。

白表按页逐行解析，出错的行记录 ERROR 后继续，全部处理完再统一失败；解析结果写入独立的 AUTOSAR
文档，已加载的输入模型保持不变，供后续配置复用。生成的连接器名称规则见 :func:`connector_name`。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from armodel import (
    ARLiteral, AUTOSAR, ApplicationSwComponentType,
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

from ..common.armodel_compat import SystemArxmlWriter, set_communication_direction, set_inner_port_iref
from ..common.names import MAX_SHORT_NAME as MAX_SHORT_NAME
from ..common.names import autosar_path, generated_name, short_name
from .arxml_model import ArxmlModel
from .values import ref_value
from .white_table import (
    ASSEMBLY_SHEET, COMPONENTS_SHEET, DELEGATION_SHEET, SIGNAL_GROUP_SHEET, SIGNAL_SHEET,
    CompositionInput, TableRow,
)

LOGGER = logging.getLogger(__name__)
COMPONENT_DESTINATIONS = (
    (CompositionSwComponentType, "COMPOSITION-SW-COMPONENT-TYPE"),
    (ApplicationSwComponentType, "APPLICATION-SW-COMPONENT-TYPE"),
    (ComplexDeviceDriverSwComponentType, "COMPLEX-DEVICE-DRIVER-SW-COMPONENT-TYPE"),
    (EcuAbstractionSwComponentType, "ECU-ABSTRACTION-SW-COMPONENT-TYPE"),
    (ServiceSwComponentType, "SERVICE-SW-COMPONENT-TYPE"),
)
ASSEMBLY_PREFIX = "ac_"
DELEGATION_PREFIX = "dlc_"


def connector_name(prefix: str, *parts: str) -> str:
    """生成连接器 SHORT-NAME：``<前缀><各部分以 _ 连接>``。

    超过 :data:`MAX_SHORT_NAME` 时截断并追加完整名称的哈希；调用方仍须检查名称冲突。
    """

    return generated_name(prefix + "_".join(parts))


def _reference(destination: str, path: str) -> RefType:
    return RefType().setDest(destination).setValue(path)


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
        self, document: AUTOSAR, data: CompositionInput, interface_mismatch: str,
        interface_mappings: dict[tuple[str, str], dict[str, str]],
    ) -> None:
        self.document = document
        self.data = data
        self.interface_mismatch = interface_mismatch
        self.interface_mapping_specs = interface_mappings
        self.interface_mappings: dict[str, _InterfaceMapping] = {}
        self.model = ArxmlModel(document, [], [])
        self.composition_path = autosar_path(data.general["Composition"], "General.Composition", 2)
        system_name = data.general["System"]
        if system_name.startswith("/"):
            autosar_path(system_name, "General.System", 2)
        else:
            short_name(system_name, "General.System")
        for key in ("RootSwCompositionPrototype", "EcuInstance"):
            autosar_path(data.general[key], f"General.{key}")
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

    def add_component(self, row: TableRow) -> None:
        values = row.values
        if not all(values.values()):
            raise ValueError("all component fields are required")
        name, type_path = values["Prototype"], values["Type"]
        autosar_path(type_path, "Component Type")
        action = values["Action"].lower()
        if action not in ("add", "reference"):
            raise ValueError(f"invalid Action '{values['Action']}'")
        if "/" in name and (action == "add" or not name.startswith("/")):
            raise ValueError("Add requires a prototype SHORT-NAME")
        if name.startswith("/"):
            autosar_path(name, "Prototype")
        else:
            short_name(name, "Prototype")
        component_type = self.lookup("find", type_path)
        destination = next((dest for kind, dest in COMPONENT_DESTINATIONS
                            if isinstance(component_type, kind)), "")
        if not destination:
            raise ValueError(f"missing or unsupported component type '{type_path}'")
        implementation = ""
        if action == "add" and isinstance(component_type, AtomicSwComponentType):
            behavior = component_type.getInternalBehavior()
            impl = self.lookup("getImplementation", behavior.getFullName()) if behavior else None
            if impl is None:
                raise ValueError(f"no implementation for {type_path}")
            implementation = impl.getFullName()
        instance_path = name if name.startswith("/") else f"{self.composition_path}/{name}"
        if instance_path.rsplit("/", 1)[0] != self.composition_path:
            raise ValueError(f"prototype is outside target composition: {instance_path}")
        existing = self.lookup("find", instance_path)
        if action == "reference" or existing is not None:
            if existing is None or not hasattr(existing, "getTypeTRef"):
                raise ValueError(f"Reference prototype does not exist: {instance_path}")
            if ref_value(existing.getTypeTRef()) != type_path:
                raise ValueError(f"prototype type mismatch: {instance_path}")
        name = instance_path.rsplit("/", 1)[-1]
        component = _Component(name, type_path, destination, instance_path, implementation, action)
        if name in self.components and self.components[name] != component:
            raise ValueError(f"conflicting prototype '{name}'")
        self.components[name] = component
        for alias in (values["SW-C"], name, instance_path):
            if alias in self.aliases and self.aliases[alias] != component:
                raise ValueError(f"ambiguous component alias '{alias}'")
            self.aliases[alias] = component

    def add_type_aliases(self) -> None:
        """类型路径和类型短名也可引用组件，但不覆盖实例名等主别名，多个组件共用时视为歧义。"""

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
        short_name(port_name, "Port")
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
                                        ref_value(interface), str(interface.getDest() or "") if interface else "")
        endpoint = self.ports[key]
        if expected and endpoint.destination != expected:
            raise ValueError(f"port '{endpoint.port_path}' must be {expected}")
        if (not endpoint.interface_path or not endpoint.interface_destination
                or not isinstance(self.lookup("find", endpoint.interface_path), PortInterface)):
            raise ValueError(
                f"port '{endpoint.port_path}' has a missing or invalid interface: '{endpoint.interface_path}'")
        return endpoint

    def interface_mapping(self, provider: _Endpoint, requester: _Endpoint, allow_create: bool) -> str:
        first = self.lookup("find", provider.interface_path)
        second = self.lookup("find", requester.interface_path)
        if (not isinstance(first, SenderReceiverInterface) or not isinstance(second, SenderReceiverInterface)
                or provider.interface_destination != "SENDER-RECEIVER-INTERFACE"
                or requester.interface_destination != "SENDER-RECEIVER-INTERFACE"):
            if not allow_create:
                return ""
            raise ValueError("interfaceMismatch=map currently supports only SenderReceiverInterface pairs")
        first_elements = {element.getShortName(): element for element in first.getDataElements()}
        second_elements = {element.getShortName(): element for element in second.getDataElements()}
        pairs = self.interface_mapping_specs.get((provider.interface_path, requester.interface_path))
        if pairs is not None:
            if (not set(pairs).issubset(first_elements)
                    or not set(pairs.values()).issubset(second_elements)
                    or len(set(pairs.values())) != len(pairs)):
                raise ValueError(
                    "interfaceMappings.dataElements must cover both interfaces exactly once; "
                    "explicit pairs must reference existing elements with unique targets")
            pairs = dict(pairs)
        existing: list[str] = []
        for mapping in self.model.interface_mappings():
            references = []
            for item in mapping.getDataMappings():
                first_ref, second_ref = item.getFirstDataPrototypeRef(), item.getSecondDataPrototypeRef()
                if (first_ref is None or second_ref is None
                        or first_ref.getDest() != "VARIABLE-DATA-PROTOTYPE"
                        or second_ref.getDest() != "VARIABLE-DATA-PROTOTYPE"):
                    references = []
                    break
                references.append((ref_value(first_ref), ref_value(second_ref)))
            if not references:
                continue
            if all(a.rpartition("/")[0] == requester.interface_path
                   and b.rpartition("/")[0] == provider.interface_path for a, b in references):
                references = [(b, a) for a, b in references]
            if not all(a.rpartition("/")[0] == provider.interface_path
                       and b.rpartition("/")[0] == requester.interface_path for a, b in references):
                continue
            mapped_pairs = {a.rpartition("/")[2]: b.rpartition("/")[2] for a, b in references}
            if (len(mapped_pairs) != len(references) or set(mapped_pairs) != set(first_elements)
                    or set(mapped_pairs.values()) != set(second_elements)
                    or len(set(mapped_pairs.values())) != len(mapped_pairs)):
                raise ValueError(f"Existing interface mapping must cover both interfaces exactly once: "
                                 f"{mapping.getFullName()}")
            if pairs is not None and any(mapped_pairs[name] != target for name, target in pairs.items()):
                continue
            existing.append(mapping.getFullName())
        if len(existing) > 1:
            raise ValueError(f"Ambiguous existing interface mappings: {', '.join(sorted(existing))}")
        if existing:
            LOGGER.info("Reuse interface mapping: %s", existing[0])
            return existing[0]
        if not allow_create:
            return ""
        if pairs is None and len(first_elements) == len(second_elements) == 1:
            pairs = {next(iter(first_elements)): next(iter(second_elements))}
        else:
            pairs = {} if pairs is None else pairs
            remaining_first = set(first_elements) - set(pairs)
            remaining_second = set(second_elements) - set(pairs.values())
            pairs.update({name: name for name in remaining_first & remaining_second})
        if not first_elements or not second_elements:
            raise ValueError(
                f"Cannot infer interface mapping {provider.interface_path} -> {requester.interface_path}; "
                "interfaces must contain data elements")
        if (set(pairs) != set(first_elements) or set(pairs.values()) != set(second_elements)
                or len(set(pairs.values())) != len(pairs)):
            raise ValueError(
                f"Cannot infer interface mapping {provider.interface_path} -> {requester.interface_path}; "
                "interfaceMappings.dataElements must cover both interfaces exactly once after same-name pairing")
        references: list[tuple[str, str]] = []
        for first_name, second_name in sorted(pairs.items()):
            first_element, second_element = first_elements[first_name], second_elements[second_name]
            references.append((first_element.getFullName(), second_element.getFullName()))
        identity = repr(references).encode("utf-8")
        name = "im_" + sha256(identity).hexdigest()[:20]
        mapping = _InterfaceMapping(name, tuple(references))
        if name in self.interface_mappings and self.interface_mappings[name] != mapping:
            raise ValueError(f"Conflicting generated interface mapping: {name}")
        package_path, _, composition_name = self.composition_path.rpartition("/")
        path = f"{package_path}/{generated_name(composition_name + '_InterfaceMappings')}/{name}"
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
            mapping_path = self.interface_mapping(
                provider, requester, allow_create=self.interface_mismatch == "map")
            if not mapping_path and self.interface_mismatch == "skip":
                LOGGER.warning("Skip %s: %s", row.location, message)
                return
            if not mapping_path and self.interface_mismatch == "error":
                raise ValueError(message)
        name = connector_name(
            ASSEMBLY_PREFIX, provider.component.name, values["PPort"], requester.component.name, values["RPort"])
        connector = _Assembly(name, provider, requester, mapping_path)
        if name in self.delegations or (name in self.assemblies and self.assemblies[name] != connector):
            raise ValueError(f"conflicting connector name '{name}'")
        self.assemblies[name] = connector

    def add_delegation(self, row: TableRow) -> None:
        values = row.values
        inner = self.endpoint(values["Inner SW-C"], values["Inner Port"])
        outer = values["Outer Port"]
        short_name(outer, "Outer Port")
        if not outer or "/" in outer or not inner.interface_path or not inner.interface_destination:
            raise ValueError("delegation requires an outer port SHORT-NAME and an inner interface reference")
        existing = self.outer_ports.get(outer)
        if existing and (existing.destination, existing.interface_path, existing.interface_destination) != (
                inner.destination, inner.interface_path, inner.interface_destination):
            raise ValueError(f"conflicting outer port '{outer}'")
        port = self.lookup("findPort", f"{self.composition_path}/{outer}")
        if port is not None:
            if isinstance(port, PPortPrototype):
                current = ("P-PORT-PROTOTYPE", port.getProvidedInterfaceTRef())
            elif isinstance(port, RPortPrototype):
                current = ("R-PORT-PROTOTYPE", port.getRequiredInterfaceTRef())
            else:
                raise ValueError(f"unsupported outer port '{self.composition_path}/{outer}'")
            if (current[0], ref_value(current[1])) != (inner.destination, inner.interface_path):
                raise ValueError(
                    f"existing outer port '{self.composition_path}/{outer}' ({current[0]}, {ref_value(current[1])}) "
                    f"does not match inner port ({inner.destination}, {inner.interface_path})")
            self.existing_outer_ports.add(outer)
        name = connector_name(DELEGATION_PREFIX, outer, inner.component.name, values["Inner Port"])
        connector = _Delegation(name, inner, outer)
        if name in self.assemblies or (name in self.delegations and self.delegations[name] != connector):
            raise ValueError(f"conflicting connector name '{name}'")
        self.delegations[name] = connector
        self.outer_ports[outer] = inner

    def add_signal(self, row: TableRow) -> None:
        values = row.values
        group = row.sheet == SIGNAL_GROUP_SHEET
        direction = "" if group else values["Direction"].upper()
        if not group and direction not in ("IN", "OUT"):
            raise ValueError(f"invalid signal direction '{direction}'")
        expected = "" if group else {"IN": "R-PORT-PROTOTYPE", "OUT": "P-PORT-PROTOTYPE"}[direction]
        endpoint = self.endpoint(values["SW-C"], values["Port"], expected)
        if not isinstance(self.lookup("find", endpoint.interface_path), SenderReceiverInterface):
            raise ValueError("signal mapping requires a SenderReceiverInterface")
        if not endpoint.interface_path or not values["DataElement"]:
            raise ValueError("signal mapping requires an interface reference and DataElement")
        short_name(values["DataElement"], "DataElement")
        data_path = f"{endpoint.interface_path}/{values['DataElement']}"
        data_element = self.lookup("findVariableDataPrototype", data_path)
        if data_element is None:
            raise ValueError(f"DataElement '{data_path}' is not defined")
        signal_path = values["SignalGroup" if group else "Signal"]
        autosar_path(signal_path, "SignalGroup" if group else "Signal")
        if not group:
            self.model.validate_signal_mapping(signal_path, direction, self.data.general["EcuInstance"])
        signal = self.lookup("findSystemSignalGroup" if group else "findSystemSignal", signal_path)
        if signal is None:
            raise ValueError(f"signal{' group' if group else ''} '{signal_path}' is not defined")
        if group:
            type_path = ref_value(data_element.getTypeTRef())
            data_type = self.lookup("findImplementationDataType", type_path)
            if data_type is None or ref_value(data_type.getCategory()) != "STRUCTURE":
                raise ValueError(f"signal group DataElement '{data_path}' requires a STRUCTURE implementation type")
        self.signals.append(_SignalMapping(endpoint, data_path, signal.getFullName(), direction, group))

    def resolve_system(self) -> None:
        """从已加载的同路径 System 读取 CATEGORY/版本/根组合，并求根组合到目标 Composition 的实例链。"""

        general = self.data.general
        system = self.lookup("find", general["System"]) if general["System"].startswith("/") else None
        if system is not None:
            self.system_category = ref_value(system.getCategory()) or self.system_category
            self.system_version = ref_value(system.getSystemVersion())
        root = self.lookup("find", general["RootSwCompositionPrototype"])
        if root is None or not hasattr(root, "getSoftwareCompositionTRef"):
            raise ValueError(f"RootSwCompositionPrototype is not defined: {general['RootSwCompositionPrototype']}")
        self.root_type = ref_value(root.getSoftwareCompositionTRef())
        chains = self.model.composition_chains(self.root_type, self.composition_path)
        if len(chains) != 1:
            raise ValueError(
                f"General.Composition {self.composition_path} must be instantiated exactly once under "
                f"{self.root_type}, found {len(chains)}")
        self.context_components = list(chains[0])

    def resolve(self) -> None:
        """逐页逐行解析白表；出错的行记录 ERROR 后继续，全部处理完再统一失败。"""

        handlers = {
            COMPONENTS_SHEET: self.add_component,
            ASSEMBLY_SHEET: self.add_assembly,
            DELEGATION_SHEET: self.add_delegation,
            SIGNAL_SHEET: self.add_signal,
            SIGNAL_GROUP_SHEET: self.add_signal,
        }
        failures = 0
        for sheet, handler in handlers.items():
            for row in self.data.tables[sheet]:
                try:
                    handler(row)
                except ValueError as exc:
                    LOGGER.error("%s: %s", row.location, exc)
                    failures += 1
            if sheet == COMPONENTS_SHEET:
                self.add_type_aliases()
        if failures:
            raise ValueError(f"{failures} white table row(s) could not be resolved; see the messages above")


def _component_iref(component: _Component, root: str, context: list[str]) -> ComponentInSystemInstanceRef:
    iref = ComponentInSystemInstanceRef().setContextCompositionRef(
        _reference("ROOT-SW-COMPOSITION-PROTOTYPE", root))
    for path in context:
        iref.addContextComponentRef(_reference("SW-COMPONENT-PROTOTYPE", path))
    return iref.setTargetComponentRef(_reference("SW-COMPONENT-PROTOTYPE", component.instance_path))


def _write_model(
    document: AUTOSARDoc, resolver: _Resolver, system_mapping: str, generate_system: bool,
) -> None:
    parts = resolver.composition_path.strip("/").split("/")
    package = document.createARPackage(parts[0])
    for package_name in parts[1:-1]:
        package = package.createARPackage(package_name)
    composition = package.createCompositionSwComponentType(parts[-1])
    if resolver.interface_mappings:
        mapping_set = package.createPortInterfaceMappingSet(generated_name(f"{parts[-1]}_InterfaceMappings"))
        for name, definition in sorted(resolver.interface_mappings.items()):
            interface_mapping = mapping_set.createVariableAndParameterInterfaceMapping(name)
            for first, second in definition.elements:
                element_mapping = DataPrototypeMapping()
                element_mapping.setFirstDataPrototypeRef(_reference("VARIABLE-DATA-PROTOTYPE", first))
                element_mapping.setSecondDataPrototypeRef(_reference("VARIABLE-DATA-PROTOTYPE", second))
                interface_mapping.addDataMapping(element_mapping)
    added = sorted((component for component in resolver.components.values() if component.action == "add"),
                   key=lambda component: component.name)
    for component in added:
        prototype = composition.createSwComponentPrototype(component.name)
        prototype.setTypeTRef(TRefType().setDest(component.destination).setValue(component.type_path))
    for name, endpoint in sorted(resolver.outer_ports.items()):
        if name in resolver.existing_outer_ports:
            continue
        interface = TRefType().setDest(endpoint.interface_destination).setValue(endpoint.interface_path)
        if endpoint.destination == "P-PORT-PROTOTYPE":
            composition.createPPortPrototype(name).setProvidedInterfaceTRef(interface)
        else:
            composition.createRPortPrototype(name).setRequiredInterfaceTRef(interface)
    for name, connector in sorted(resolver.assemblies.items()):
        assembly = composition.createAssemblySwConnector(name)
        assembly.setProviderIRef(connector.provider.composition_iref())
        assembly.setRequesterIRef(connector.requester.composition_iref())
        if connector.mapping_path:
            assembly.setMappingRef(_reference("VARIABLE-AND-PARAMETER-INTERFACE-MAPPING", connector.mapping_path))
    for name, connector in sorted(resolver.delegations.items()):
        delegation = composition.createDelegationSwConnector(name)
        set_inner_port_iref(delegation, connector.inner.composition_iref())
        delegation.setOuterPortRef(_reference(connector.inner.destination,
                                              f"{resolver.composition_path}/{connector.outer_name}"))

    if not generate_system:
        if resolver.signals:
            raise ValueError("SenderReceiverToSignal mappings require a SYSTEM; set config.generateSystem to true")
        return

    general = resolver.data.general
    system_path = general["System"]
    if system_path.startswith("/"):
        system_parts = system_path.strip("/").split("/")
        package = document.createARPackage(system_parts[0])
        for package_name in system_parts[1:-1]:
            package = package.createARPackage(package_name)
        system_path = system_parts[-1]
    system = package.createSystem(system_path)
    system.setCategory(resolver.system_category)
    if resolver.system_version:
        system.setSystemVersion(ARLiteral().setValue(resolver.system_version))
    system.addFibexElementRef(_reference("ECU-INSTANCE", general["EcuInstance"]))
    mapping = system.createSystemMapping(generated_name(f"SystemMapping_{system_mapping}"))
    root = general["RootSwCompositionPrototype"]
    if root.rsplit("/", 1)[0] == system.getFullName():
        system.createRootSoftwareComposition(root.rsplit("/", 1)[-1]).setSoftwareCompositionTRef(
            TRefType().setDest("COMPOSITION-SW-COMPONENT-TYPE").setValue(resolver.root_type))
    context = resolver.context_components
    ecu_mapping = mapping.createSwcToEcuMapping("SwcToEcuMapping")
    ecu_mapping.setEcuInstanceRef(_reference("ECU-INSTANCE", general["EcuInstance"]))
    for component in added:
        ecu_mapping.addComponentIRef(_component_iref(component, root, context))
        if component.implementation:
            impl_mapping = mapping.createSwcToImplMapping(generated_name(f"{component.name}_ImplMapping"))
            impl_mapping.setComponentImplementationRef(_reference("SWC-IMPLEMENTATION", component.implementation))
            impl_mapping.addComponentIRef(_component_iref(component, root, context))
    for signal in resolver.signals:
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
            set_communication_direction(signal_mapping, ARLiteral().setValue(signal.direction))
            signal_mapping.setSystemSignalRef(_reference("SYSTEM-SIGNAL", signal.signal_path))
            mapping.addDataMapping(signal_mapping)


def generate_composition_arxml(
    document: AUTOSAR,
    data: CompositionInput,
    output_file: Path,
    system_mapping: str,
    ar_release: str,
    interface_mismatch: str,
    interface_mappings: dict[tuple[str, str], dict[str, str]],
    generate_system: bool,
) -> Path:
    resolver = _Resolver(document, data, interface_mismatch, interface_mappings)
    resolver.resolve()
    if generate_system:
        resolver.resolve_system()
    # 写到独立文档，保留已加载的模型供后续配置复用。
    output = AUTOSARDoc()
    output.setARRelease(ar_release)
    _write_model(output, resolver, system_mapping, generate_system)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    SystemArxmlWriter().save(str(output_file), output)
    LOGGER.info("Composition ARXML done: %s assemblies, %s delegations, %s signal mappings -> %s",
                len(resolver.assemblies), len(resolver.delegations), len(resolver.signals), output_file)
    return output_file
