"""解析白表中的组件、端口、连接和信号映射引用，并用 armodel 生成 Composition/System ARXML。

白表按页逐行解析，出错的行记录 ERROR 后继续，全部处理完再统一失败；解析结果写入独立的 AUTOSAR
文档，已加载的输入模型保持不变，供后续配置复用。生成的连接器名称规则见 :func:`connector_name`。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

import armodel
from armodel import (
    ARLiteral, AUTOSAR, ApplicationSwComponentType,
    AtomicSwComponentType, ComplexDeviceDriverSwComponentType,
    ComponentInSystemInstanceRef, CompositionSwComponentType,
    EcuAbstractionSwComponentType, EcuInstance, PPortInCompositionInstanceRef, PPortPrototype,
    PRPortPrototype, RPortInCompositionInstanceRef, RPortPrototype, RefType,
    SenderReceiverToSignalGroupMapping, SenderReceiverToSignalMapping,
    ServiceSwComponentType, TRefType, VariableDataPrototypeInSystemInstanceRef,
)

from armodel.models.M2.AUTOSARTemplates.AutosarTopLevelStructure import AUTOSARDoc
from armodel.models.M2.AUTOSARTemplates.SWComponentTemplate.PortInterface import (
    DataPrototypeMapping, PortInterface, SenderReceiverInterface,
)

from ..common.armodel_compat import (
    SystemArxmlWriter, get_inner_port_iref, set_communication_direction, set_inner_port_iref,
)
from ..common.names import NAME_HASH_LENGTH, autosar_path, generated_name, short_name
from .arxml_model import ArxmlModel
from .values import class_destination, ref_value
from .white_table import (
    ASSEMBLY_SHEET, COMPONENTS_SHEET, DELEGATION_SHEET, SIGNAL_GROUP_SHEET, SIGNAL_SHEET,
    CompositionInput, TableRow,
)

LOGGER = logging.getLogger(__name__)
# 部分组件类型在较旧的 armodel 中不存在，按名称可选导入。
COMPONENT_DESTINATIONS = tuple(
    (component_class, destination)
    for component_class, destination in (
        (CompositionSwComponentType, "COMPOSITION-SW-COMPONENT-TYPE"),
        (ApplicationSwComponentType, "APPLICATION-SW-COMPONENT-TYPE"),
        (ComplexDeviceDriverSwComponentType, "COMPLEX-DEVICE-DRIVER-SW-COMPONENT-TYPE"),
        (EcuAbstractionSwComponentType, "ECU-ABSTRACTION-SW-COMPONENT-TYPE"),
        (ServiceSwComponentType, "SERVICE-SW-COMPONENT-TYPE"),
        (getattr(armodel, "SensorActuatorSwComponentType", None), "SENSOR-ACTUATOR-SW-COMPONENT-TYPE"),
        (getattr(armodel, "NvBlockSwComponentType", None), "NV-BLOCK-SW-COMPONENT-TYPE"),
        (getattr(armodel, "ServiceProxySwComponentType", None), "SERVICE-PROXY-SW-COMPONENT-TYPE"),
        (getattr(armodel, "ParameterSwComponentType", None), "PARAMETER-SW-COMPONENT-TYPE"),
    )
    if component_class is not None
)
ASSEMBLY_PREFIX = "ac_"
DELEGATION_PREFIX = "dlc_"


def connector_name(prefix: str, *parts: str) -> str:
    """生成连接器 SHORT-NAME：``<前缀><各部分以 _ 连接>``。

    超过 SHORT-NAME 长度上限时截断并追加完整名称的哈希；调用方仍须检查名称冲突。
    """

    return generated_name(prefix + "_".join(parts))


def disambiguated_connector_name(name: str, *parts: str) -> str:
    """用 ``_`` 拼接产生歧义时（如 ``a_b`` + ``c`` 与 ``a`` + ``b_c``），按各部分的结构化哈希区分。"""

    digest = sha256("\0".join(parts).encode("utf-8")).hexdigest()[:NAME_HASH_LENGTH]
    return generated_name(f"{name}_{digest}")


def _reference(destination: str, path: str) -> RefType:
    return RefType().setDest(destination).setValue(path)


def _iref_target_port(iref: object) -> str:
    for getter in ("getTargetPPortRef", "getTargetRPortRef"):
        method = getattr(iref, getter, None)
        if method is not None and method() is not None:
            return ref_value(method())
    return ""


def _iref_context(iref: object) -> str:
    return ref_value(iref.getContextComponentRef()) if iref is not None else ""


def _warn_unmapped_elements(
    mapping: str, pairs: dict[str, str], provider_elements: dict, requester_elements: dict,
) -> None:
    unmapped_provider = sorted(set(provider_elements) - set(pairs))
    unmapped_requester = sorted(set(requester_elements) - set(pairs.values()))
    if unmapped_provider or unmapped_requester:
        LOGGER.warning(
            "Interface mapping %s leaves data elements unmapped: provider %s, requester %s",
            mapping, unmapped_provider or "none", unmapped_requester or "none")


@dataclass(frozen=True)
class _Component:
    name: str
    type_path: str
    destination: str
    instance_path: str
    implementation: str
    action: str
    exists: bool = False


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
        interface_mappings: dict[tuple[str, str], dict[str, str]], system_mapping: str = "",
        mergeable_interface_mapping_sets: frozenset[str] = frozenset(),
    ) -> None:
        self.document = document
        self.data = data
        self.interface_mismatch = interface_mismatch
        self.configured_interface_mappings = interface_mappings
        self.mergeable_interface_mapping_sets = mergeable_interface_mapping_sets
        self.generated_interface_mappings: dict[str, _InterfaceMapping] = {}
        self.model = ArxmlModel(document, [], [])
        self.composition_path = autosar_path(data.general["Composition"], "General.Composition", 2)
        system_name = data.general["System"]
        if system_name.startswith("/"):
            autosar_path(system_name, "General.System", 2)
        else:
            short_name(system_name, "General.System")
        for key in ("RootSwCompositionPrototype", "EcuInstance"):
            autosar_path(data.general[key], f"General.{key}")
        self.system_mapping_name = generated_name(f"SystemMapping_{system_mapping}") if system_mapping else ""
        self.context_components: list[str] = []
        self.system_path = ""
        self.system_category = "SYSTEM_DESCRIPTION"
        self.system_version = ""
        self.write_fibex_ecu = False
        self.root_type = ""
        self.components: dict[str, _Component] = {}
        self.aliases: dict[str, _Component] = {}
        self.ambiguous_aliases: set[str] = set()
        self.ports: dict[tuple[str, str], _Endpoint] = {}
        self.assemblies: dict[str, _Assembly] = {}
        self.delegations: dict[str, _Delegation] = {}
        self.outer_ports: dict[str, _Endpoint] = {}
        self.existing_outer_ports: set[str] = set()
        # 输入 Composition 中已有、内容相同的连接器，不再重复写出。
        self.reused_connectors: set[str] = set()
        self.signals: list[_SignalMapping] = []
        self.objects: dict[tuple[str, str], object] = {}

    def lookup(self, method: str, path: str):
        key = (method, path)
        if key not in self.objects:
            self.objects[key] = getattr(self.document, method)(path)
        return self.objects[key]

    def implementation_of(self, type_path: str, component_type: AtomicSwComponentType) -> str:
        """返回原子组件唯一的 SWC-IMPLEMENTATION；缺失或不唯一只警告，SwcToImplMapping 为 0..*。"""

        behavior = component_type.getInternalBehavior()
        if behavior is None:
            LOGGER.warning("%s has no SWC-INTERNAL-BEHAVIOR; SwcToImplMapping is not generated", type_path)
            return ""
        behavior_path = behavior.getFullName()
        candidates = self.model.implementations(behavior_path)
        if not candidates:
            LOGGER.warning(
                "No SWC-IMPLEMENTATION references %s; SwcToImplMapping is not generated", behavior_path)
            return ""
        if len(candidates) == 1:
            return candidates[0]
        chosen = self.lookup("getImplementation", behavior_path)
        chosen_path = chosen.getFullName() if chosen is not None else candidates[0]
        LOGGER.warning(
            "Multiple SWC-IMPLEMENTATIONs reference %s (%s); using %s",
            behavior_path, ", ".join(candidates), chosen_path)
        return chosen_path

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
        instance_path = name if name.startswith("/") else f"{self.composition_path}/{name}"
        if instance_path.rsplit("/", 1)[0] != self.composition_path:
            raise ValueError(f"prototype is outside target composition: {instance_path}")
        existing = self.lookup("find", instance_path)
        if action == "reference" or existing is not None:
            if existing is None or not hasattr(existing, "getTypeTRef"):
                raise ValueError(f"Reference prototype does not exist: {instance_path}")
            if ref_value(existing.getTypeTRef()) != type_path:
                raise ValueError(f"prototype type mismatch: {instance_path}")
        if action == "add" and existing is not None:
            LOGGER.info(
                "%s: prototype %s already exists; its definition is reused",
                row.location, instance_path)
        implementation = ""
        if action == "add" and isinstance(component_type, AtomicSwComponentType):
            implementation = self.implementation_of(type_path, component_type)
        name = instance_path.rsplit("/", 1)[-1]
        component = _Component(
            name, type_path, destination, instance_path, implementation, action, existing is not None)
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
            if isinstance(port, PRPortPrototype):
                raise ValueError(f"PR-PORT-PROTOTYPE '{port_path}' is not supported by this tool")
            if isinstance(port, PPortPrototype):
                destination = "P-PORT-PROTOTYPE"
                interface = port.getProvidedInterfaceTRef()
            elif isinstance(port, RPortPrototype):
                destination = "R-PORT-PROTOTYPE"
                interface = port.getRequiredInterfaceTRef()
            else:
                raise ValueError(f"missing or unsupported port '{port_path}'")
            interface_path = ref_value(interface)
            interface_object = self.lookup("find", interface_path) if interface_path else None
            if not isinstance(interface_object, PortInterface):
                raise ValueError(f"port '{port.getFullName()}' has a missing or invalid interface: '{interface_path}'")
            # DEST 按引用对象的实际类型写出，输入中错误的 DEST 只提示。
            interface_destination = class_destination(interface_object)
            declared = str(interface.getDest() or "")
            if declared != interface_destination:
                LOGGER.warning(
                    "Port %s declares interface DEST %s but %s is a %s; using %s",
                    port.getFullName(), declared or "(empty)", interface_path, interface_destination,
                    interface_destination)
            self.ports[key] = _Endpoint(
                component, port.getFullName(), destination, interface_path, interface_destination)
        endpoint = self.ports[key]
        if expected and endpoint.destination != expected:
            raise ValueError(f"port '{endpoint.port_path}' must be {expected}")
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
        pairs = self.configured_interface_mappings.get((provider.interface_path, requester.interface_path))
        if pairs is not None:
            if (not set(pairs).issubset(first_elements)
                    or not set(pairs.values()).issubset(second_elements)
                    or len(set(pairs.values())) != len(pairs)):
                raise ValueError(
                    "interfaceMappings.dataElements must reference existing data elements of both interfaces "
                    "and map them one-to-one")
            pairs = dict(pairs)
        existing: list[tuple[str, dict[str, str]]] = []
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
            # 浅校验：引用须存在且一一对应；未覆盖的元素只警告。
            if (len(mapped_pairs) != len(references) or len(set(mapped_pairs.values())) != len(mapped_pairs)
                    or not set(mapped_pairs).issubset(first_elements)
                    or not set(mapped_pairs.values()).issubset(second_elements)):
                raise ValueError(
                    f"Existing interface mapping {mapping.getFullName()} must reference existing data elements "
                    "of both interfaces one-to-one")
            if pairs is not None and any(mapped_pairs.get(name) != target for name, target in pairs.items()):
                continue
            existing.append((mapping.getFullName(), mapped_pairs))
        if len(existing) > 1:
            raise ValueError(
                f"Ambiguous existing interface mappings: {', '.join(sorted(path for path, _ in existing))}")
        if existing:
            path, mapped_pairs = existing[0]
            _warn_unmapped_elements(path, mapped_pairs, first_elements, second_elements)
            LOGGER.info("Reuse interface mapping: %s", path)
            return path
        if not allow_create:
            return ""
        if not first_elements or not second_elements:
            raise ValueError(
                f"Cannot infer interface mapping {provider.interface_path} -> {requester.interface_path}; "
                "interfaces must contain data elements")
        if pairs is None and len(first_elements) == len(second_elements) == 1:
            pairs = {next(iter(first_elements)): next(iter(second_elements))}
        else:
            pairs = {} if pairs is None else pairs
            remaining_first = set(first_elements) - set(pairs)
            remaining_second = set(second_elements) - set(pairs.values())
            pairs.update({name: name for name in remaining_first & remaining_second})
        if not pairs:
            raise ValueError(
                f"Cannot infer interface mapping {provider.interface_path} -> {requester.interface_path}; "
                "no data elements can be paired by name, configure interfaceMappings.dataElements")
        package_path, _, composition_name = self.composition_path.rpartition("/")
        set_path = f"{package_path}/{generated_name(composition_name + '_InterfaceMappings')}"
        if self.lookup("find", set_path) is not None and set_path not in self.mergeable_interface_mapping_sets:
            raise ValueError(
                f"PORT-INTERFACE-MAPPING-SET {set_path} already exists in the loaded ARXML; its "
                "PORT-INTERFACE-MAPPINGS cannot be split across files, add the mapping there or configure "
                "interfaceMappings to reuse an existing mapping")
        _warn_unmapped_elements(
            f"{provider.interface_path} -> {requester.interface_path}", pairs, first_elements, second_elements)
        references: list[tuple[str, str]] = []
        for first_name, second_name in sorted(pairs.items()):
            first_element, second_element = first_elements[first_name], second_elements[second_name]
            references.append((first_element.getFullName(), second_element.getFullName()))
        identity = repr(references).encode("utf-8")
        name = "im_" + sha256(identity).hexdigest()[:20]
        mapping = _InterfaceMapping(name, tuple(references))
        if name in self.generated_interface_mappings and self.generated_interface_mappings[name] != mapping:
            raise ValueError(f"Conflicting generated interface mapping: {name}")
        path = f"{set_path}/{name}"
        if self.lookup("find", path) is not None:
            raise ValueError(f"Generated interface mapping already exists in input: {path}")
        self.generated_interface_mappings[name] = mapping
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
        parts = (provider.component.name, values["PPort"], requester.component.name, values["RPort"])
        name, connector = self._claim_connector(
            ASSEMBLY_PREFIX, parts, lambda name: _Assembly(name, provider, requester, mapping_path))
        self.assemblies[name] = connector

    def _claim_connector(self, prefix: str, parts: tuple[str, ...], build):
        """返回本次连接器的名称和对象；``_`` 拼接产生的同名不同连接改用结构化哈希区分。"""

        name = connector_name(prefix, *parts)
        connector = build(name)
        current = self.assemblies.get(name) or self.delegations.get(name)
        if current is None or current == connector:
            return name, connector
        hashed = disambiguated_connector_name(name, *parts)
        LOGGER.warning("Connector name %s is ambiguous for %s; using %s", name, " / ".join(parts), hashed)
        connector = build(hashed)
        current = self.assemblies.get(hashed) or self.delegations.get(hashed)
        if current is not None and current != connector:
            raise ValueError(f"conflicting connector name '{hashed}'")
        return hashed, connector

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
                raise ValueError(
                    f"outer port name '{self.composition_path}/{outer}' is already used by a "
                    f"{class_destination(port)}")
            if (current[0], ref_value(current[1])) != (inner.destination, inner.interface_path):
                raise ValueError(
                    f"existing outer port '{self.composition_path}/{outer}' ({current[0]}, {ref_value(current[1])}) "
                    f"does not match inner port ({inner.destination}, {inner.interface_path})")
            self.existing_outer_ports.add(outer)
        parts = (outer, inner.component.name, values["Inner Port"])
        name, connector = self._claim_connector(
            DELEGATION_PREFIX, parts, lambda name: _Delegation(name, inner, outer))
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
            self.model.validate_signal_mapping(signal_path)
        signal = self.lookup("findSystemSignalGroup" if group else "findSystemSignal", signal_path)
        if signal is None:
            raise ValueError(f"signal{' group' if group else ''} '{signal_path}' is not defined")
        mapping = _SignalMapping(endpoint, data_path, signal.getFullName(), direction, group)
        if mapping in self.signals:
            LOGGER.warning("%s: duplicate signal mapping %s -> %s skipped", row.location, data_path, signal_path)
            return
        if group:
            LOGGER.warning(
                "%s: SENDER-RECEIVER-TO-SIGNAL-GROUP-MAPPING requires a TYPE-MAPPING, which the tool does not "
                "derive; complete it for %s during integration", row.location, data_path)
        self.signals.append(mapping)

    def resolve_system(self) -> None:
        """定位已加载的 System，校验 ECU、根组合原型与映射名称，并求根组合到目标 Composition 的实例链。

        System 的 FIBEX-ELEMENTS 和 SystemMapping 的子聚合不可拆分（非 atpSplitable），已有内容时不在
        拆分片段中重复写出；SYSTEM-VERSION 为 1..1，只沿用输入值，不臆造。
        """

        general = self.data.general
        system = self.model.find_system(general["System"])
        if system is None:
            raise ValueError(f"System is not defined in the loaded ARXML: {general['System']}")
        self.system_path = system.getFullName()
        self.system_category = ref_value(system.getCategory()) or self.system_category
        self.system_version = ref_value(system.getSystemVersion())
        if not self.system_version:
            LOGGER.warning(
                "System %s has no SYSTEM-VERSION (multiplicity 1); the output does not invent one",
                self.system_path)

        ecu_path = general["EcuInstance"]
        if not isinstance(self.lookup("find", ecu_path), EcuInstance):
            raise ValueError(f"ECU instance is not defined: {ecu_path}")
        system_ecus = ArxmlModel.system_ecu_instances(system)
        self.write_fibex_ecu = not list(system.getFibexElementRefs())
        if not self.write_fibex_ecu and ecu_path not in system_ecus:
            LOGGER.warning(
                "System %s FIBEX-ELEMENTS do not reference %s; FIBEX-ELEMENTS cannot be split across files, "
                "add the ECU-INSTANCE to the System manually", self.system_path, ecu_path)

        if self.system_mapping_name:
            existing_mappings = [mapping.getShortName() or "" for mapping in system.getMappings()]
            if self.system_mapping_name in existing_mappings:
                raise ValueError(
                    f"SystemMapping {self.system_path}/{self.system_mapping_name} already exists in the loaded "
                    "ARXML; its mappings cannot be split across files, configure a different general.systemMapping")
            same_case = [name for name in existing_mappings
                         if name.casefold() == self.system_mapping_name.casefold()]
            if same_case:
                LOGGER.warning(
                    "SystemMapping %s differs only in case from existing %s/%s; AUTOSAR tools may treat "
                    "them as the same name", self.system_mapping_name, self.system_path, same_case[0])

        root_path = general["RootSwCompositionPrototype"]
        if root_path.rpartition("/")[0] != self.system_path:
            raise ValueError(
                f"RootSwCompositionPrototype {root_path} does not belong to System {self.system_path}")
        root = self.lookup("find", root_path)
        if root is None or not hasattr(root, "getSoftwareCompositionTRef"):
            raise ValueError(f"RootSwCompositionPrototype is not defined: {root_path}")
        self.root_type = ref_value(root.getSoftwareCompositionTRef())
        chains = self.model.composition_chains(self.root_type, self.composition_path)
        if len(chains) != 1:
            raise ValueError(
                f"General.Composition {self.composition_path} must be instantiated exactly once under "
                f"{self.root_type}, found {len(chains)}")
        self.context_components = list(chains[0])
        for component in self.components.values():
            if component.action == "add" and component.destination == "COMPOSITION-SW-COMPONENT-TYPE":
                LOGGER.warning(
                    "%s is a composition prototype; SwcToEcuMapping normally references atomic "
                    "SW component prototypes", component.instance_path)

    def check_composition_names(self) -> int:
        """目标 Composition 的 COMPONENTS、PORTS、CONNECTORS 共用一个命名空间，同名对象报错。

        与输入中同名同内容的连接器直接复用；连接同一对端口但名称不同的已有连接器只警告并复用。
        仅大小写不同的名称在 AUTOSAR 中视为冲突，这里只警告，留给后续工具链校验。返回记录的 ERROR 数量。
        """

        composition = self.lookup("find", self.composition_path)
        existing: dict[str, tuple[str, object]] = {}
        existing_connections: dict[tuple[str, ...], str] = {}
        if isinstance(composition, CompositionSwComponentType):
            pass_through = getattr(composition, "getPassThroughSwConnectors", lambda: [])()
            for kind, items in (
                ("prototype", composition.getComponents()), ("port", composition.getPorts()),
                ("assembly connector", composition.getAssemblySwConnectors()),
                ("delegation connector", composition.getDelegationSwConnectors()),
                ("pass-through connector", pass_through),
            ):
                for item in items:
                    name = item.getShortName()
                    if name:
                        existing.setdefault(name, (kind, item))
                    key = self._existing_connection(kind, item)
                    if key:
                        existing_connections.setdefault(key, name)

        new: list[tuple[str, str, object]] = [
            (component.name, "prototype", component) for component in self.components.values()
            if component.action == "add" and not component.exists
        ]
        new += [(name, "port", endpoint) for name, endpoint in self.outer_ports.items()
                if name not in self.existing_outer_ports]
        new += [(name, "assembly connector", connector) for name, connector in self.assemblies.items()]
        new += [(name, "delegation connector", connector) for name, connector in self.delegations.items()]

        failures = 0
        claimed: dict[str, str] = {}
        for name, kind, item in new:
            if name in claimed:
                LOGGER.error("%s: new %s %s conflicts with new %s %s; SHORT-NAMEs must be unique",
                             self.composition_path, kind, name, claimed[name], name)
                failures += 1
                continue
            claimed[name] = kind
            key = self._new_connection(kind, item)
            if name in existing:
                existing_kind, existing_item = existing[name]
                if existing_kind == kind and key and key == self._existing_connection(existing_kind, existing_item):
                    LOGGER.info("Reuse existing %s %s/%s", kind, self.composition_path, name)
                    self.reused_connectors.add(name)
                    continue
                LOGGER.error("%s: new %s %s conflicts with existing %s %s; SHORT-NAMEs must be unique",
                             self.composition_path, kind, name, existing_kind, name)
                failures += 1
                continue
            if key and key in existing_connections:
                LOGGER.warning(
                    "%s: %s %s connects the same ports as existing connector %s; it is not generated",
                    self.composition_path, kind, name, existing_connections[key])
                self.reused_connectors.add(name)

        names = list(existing) + [name for name in claimed if name not in existing]
        first: dict[str, str] = {}
        for name in names:
            other = first.setdefault(name.casefold(), name)
            if other != name and name in claimed:
                LOGGER.warning(
                    "%s: %s differs only in case from %s; AUTOSAR tools may treat them as the same SHORT-NAME",
                    self.composition_path, name, other)
        return failures

    def _existing_connection(self, kind: str, connector: object) -> tuple[str, ...]:
        try:
            if kind == "assembly connector":
                provider, requester = connector.getProviderIRef(), connector.getRequesterIRef()
                return ("assembly", _iref_context(provider), _iref_target_port(provider),
                        _iref_context(requester), _iref_target_port(requester))
            if kind == "delegation connector":
                inner = get_inner_port_iref(connector)
                return ("delegation", _iref_context(inner), _iref_target_port(inner),
                        ref_value(connector.getOuterPortRef()))
        except AttributeError:
            pass
        return ()

    def _new_connection(self, kind: str, connector: object) -> tuple[str, ...]:
        if kind == "assembly connector":
            return ("assembly", connector.provider.component.instance_path, connector.provider.port_path,
                    connector.requester.component.instance_path, connector.requester.port_path)
        if kind == "delegation connector":
            return ("delegation", connector.inner.component.instance_path, connector.inner.port_path,
                    f"{self.composition_path}/{connector.outer_name}")
        return ()

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
        failures += self.check_composition_names()
        if failures:
            raise ValueError(f"{failures} white table row(s) could not be resolved; see the messages above")


def _component_iref(component: _Component, root: str, context: list[str]) -> ComponentInSystemInstanceRef:
    iref = ComponentInSystemInstanceRef().setContextCompositionRef(
        _reference("ROOT-SW-COMPOSITION-PROTOTYPE", root))
    for path in context:
        iref.addContextComponentRef(_reference("SW-COMPONENT-PROTOTYPE", path))
    return iref.setTargetComponentRef(_reference("SW-COMPONENT-PROTOTYPE", component.instance_path))


def _write_model(document: AUTOSARDoc, resolver: _Resolver, generate_system: bool, additive: bool = False) -> None:
    parts = resolver.composition_path.strip("/").split("/")
    package = document.createARPackage(parts[0])
    for package_name in parts[1:-1]:
        package = package.createARPackage(package_name)
    composition = package.createCompositionSwComponentType(parts[-1])
    if resolver.generated_interface_mappings:
        mapping_set = package.createPortInterfaceMappingSet(generated_name(f"{parts[-1]}_InterfaceMappings"))
        for name, definition in sorted(resolver.generated_interface_mappings.items()):
            interface_mapping = mapping_set.createVariableAndParameterInterfaceMapping(name)
            for first, second in definition.elements:
                element_mapping = DataPrototypeMapping()
                element_mapping.setFirstDataPrototypeRef(_reference("VARIABLE-DATA-PROTOTYPE", first))
                element_mapping.setSecondDataPrototypeRef(_reference("VARIABLE-DATA-PROTOTYPE", second))
                interface_mapping.addDataMapping(element_mapping)
    added = sorted((component for component in resolver.components.values()
                    if component.action == "add" and (not additive or not component.exists)),
                   key=lambda component: component.name)
    for component in added:
        if component.exists:
            continue
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
        if name in resolver.reused_connectors:
            continue
        assembly = composition.createAssemblySwConnector(name)
        assembly.setProviderIRef(connector.provider.composition_iref())
        assembly.setRequesterIRef(connector.requester.composition_iref())
        if connector.mapping_path:
            assembly.setMappingRef(_reference("VARIABLE-AND-PARAMETER-INTERFACE-MAPPING", connector.mapping_path))
    for name, connector in sorted(resolver.delegations.items()):
        if name in resolver.reused_connectors:
            continue
        delegation = composition.createDelegationSwConnector(name)
        set_inner_port_iref(delegation, connector.inner.composition_iref())
        delegation.setOuterPortRef(_reference(connector.inner.destination,
                                              f"{resolver.composition_path}/{connector.outer_name}"))

    if not generate_system:
        if resolver.signals:
            raise ValueError("SenderReceiverToSignal mappings require a SYSTEM; set config.generateSystem to true")
        return

    general = resolver.data.general
    system_parts = resolver.system_path.strip("/").split("/")
    package = document.createARPackage(system_parts[0])
    for package_name in system_parts[1:-1]:
        package = package.createARPackage(package_name)
    system = package.createSystem(system_parts[-1])
    system.setCategory(resolver.system_category)
    if resolver.system_version:
        system.setSystemVersion(ARLiteral().setValue(resolver.system_version))
    if resolver.write_fibex_ecu:
        system.addFibexElementRef(_reference("ECU-INSTANCE", general["EcuInstance"]))
    # SwcToEcuMapping.component 为 1..*：没有新增组件且没有信号映射时不生成空的 SystemMapping。
    mapping = None
    if added or resolver.signals:
        mapping = system.createSystemMapping(resolver.system_mapping_name)
    root = general["RootSwCompositionPrototype"]
    system.createRootSoftwareComposition(root.rsplit("/", 1)[-1]).setSoftwareCompositionTRef(
        TRefType().setDest("COMPOSITION-SW-COMPONENT-TYPE").setValue(resolver.root_type))
    if mapping is None:
        return
    context = resolver.context_components
    if added:
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
    additive: bool = False,
    mergeable_interface_mapping_sets: frozenset[str] = frozenset(),
) -> Path:
    resolver = _Resolver(
        document, data, interface_mismatch, interface_mappings, system_mapping, mergeable_interface_mapping_sets)
    resolver.resolve()
    if generate_system:
        resolver.resolve_system()
    # 写到独立文档，保留已加载的模型供后续配置复用；arRelease 只决定输出声明，不与输入版本比对。
    output = AUTOSARDoc()
    output.setARRelease(ar_release)
    _write_model(output, resolver, generate_system, additive)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    SystemArxmlWriter().save(str(output_file), output)
    LOGGER.info("Composition ARXML done: %s assemblies, %s delegations, %s signal mappings -> %s",
                len(resolver.assemblies), len(resolver.delegations), len(resolver.signals), output_file)
    return output_file
