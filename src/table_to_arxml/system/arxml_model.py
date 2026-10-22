"""System 工具的 ARXML 模型查询层。

:class:`ArxmlModel` 把一组 ARXML 合并成一个模型，提供组件、System、组合实例链、CAN/LIN 通道与信号查询，
并向外暴露底层 AUTOSAR 文档供白表解析和 composition 生成复用。armodel 是工具的直接依赖：
composition 生成（:mod:`.arxml_generator`）同样直接使用其模型类，与具体版本相关的绕行代码集中在
:mod:`table_to_arxml.common.armodel_compat`。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Iterable, Optional

from armodel import CompositionSwComponentType, SwcImplementation, System
from armodel.models.M2.AUTOSARTemplates.AutosarTopLevelStructure import AUTOSAR
from armodel.models.M2.AUTOSARTemplates.GenericStructure.GeneralTemplateClasses.ARPackage import (
    ARPackage,
)
from armodel.models.M2.AUTOSARTemplates.SWComponentTemplate.Components import SwComponentType
from armodel.models.M2.AUTOSARTemplates.SWComponentTemplate.PortInterface import (
    PortInterfaceMappingSet, VariableAndParameterInterfaceMapping,
)
from armodel.models.M2.AUTOSARTemplates.SystemTemplate.Fibex.FibexCore.CoreCommunication import (
    ISignal, ISignalGroup, SystemSignal, SystemSignalGroup,
)
from armodel.models.M2.AUTOSARTemplates.SystemTemplate.Fibex.FibexCore.CoreTopology import (
    CommunicationCluster,
)

from ..common.armodel_compat import new_parser, reset_document
from .values import ref_value

LOGGER = logging.getLogger(__name__)

MODULE_CAN = "CAN"
MODULE_LIN = "LIN"
BUS_CLUSTER_TYPES = {"CanCluster": MODULE_CAN, "LinCluster": MODULE_LIN}
# 组合嵌套的最大层数；超过即视为模型异常（例如组合之间循环引用）。
MAX_COMPOSITION_DEPTH = 16
_SCHEMA_LOCATION = re.compile(r"schemaLocation\s*=\s*[\"'][^\"']*?([\w.-]+\.xsd)\s*[\"']")


def bus_module(cluster: object) -> str:
    """返回通信簇的总线类型（``CAN``/``LIN``）；其他总线返回空串。"""

    return BUS_CLUSTER_TYPES.get(type(cluster).__name__, "")


class ArxmlModel:
    """把一组 ARXML 合并成一个模型。

    armodel 的 ``AUTOSAR`` 是全局单例，因此每次加载前都会清空，同一进程内只应
    存在一个有效模型。
    """

    def __init__(self, document: AUTOSAR, paths: list[Path], diagnostics: list[str]) -> None:
        self.paths = paths
        self.diagnostics = diagnostics
        self._document = document
        self._elements: Optional[list] = None
        self._groups: Optional[dict[str, set[str]]] = None
        self._composition_children: Optional[dict[str, list[tuple[str, str]]]] = None

    @property
    def document(self) -> AUTOSAR:
        """底层 AUTOSAR 文档，供 composition 生成直接复用，避免二次解析。"""

        return self._document

    @classmethod
    def load(cls, paths: Iterable[Path]) -> "ArxmlModel":
        """解析给定的全部 ARXML；不支持的元素记入诊断后跳过。"""

        document = reset_document()
        diagnostics: list[str] = []
        parser = new_parser(diagnostics, LOGGER)
        paths = list(paths)
        loaded: list[Path] = []
        for index, path in enumerate(paths, start=1):
            LOGGER.info("Parsing ARXML %d/%d: %s", index, len(paths), path)
            parser.load(str(path), document)
            loaded.append(path)
        return cls(document, loaded, diagnostics)

    def add(self, path: Path) -> None:
        """追加加载一个 ARXML（如本次刚生成的结果），供后续配置引用。"""

        LOGGER.info("Parsing ARXML: %s", path)
        new_parser(self.diagnostics, LOGGER).load(str(path), self._document)
        self.paths.append(path)
        self.invalidate()

    def invalidate(self) -> None:
        """底层文档被外部改写后清空查询缓存。"""

        self._elements = None
        self._groups = None
        self._composition_children = None

    # -- 遍历 ---------------------------------------------------------------

    def _all_elements(self) -> list:
        """递归展开全部 AR-PACKAGE 下的元素，结果缓存复用。"""

        if self._elements is None:
            elements: list = []
            stack: list[ARPackage] = list(self._document.getARPackages())
            while stack:
                package = stack.pop()
                elements.extend(package.getElements())
                stack.extend(package.getARPackages())
            self._elements = elements
        return self._elements

    def _elements_of(self, element_type) -> list:
        return [item for item in self._all_elements() if isinstance(item, element_type)]

    # -- 组件 ---------------------------------------------------------------

    def interface_mappings(self) -> list[VariableAndParameterInterfaceMapping]:
        return [
            mapping
            for mapping_set in self._elements_of(PortInterfaceMappingSet)
            for mapping in mapping_set.getPortInterfaceMappings()
            if isinstance(mapping, VariableAndParameterInterfaceMapping)
        ]

    def component_prototypes(self, composition_path: str | None = None) -> dict[str, str]:
        """返回指定 composition 中的 prototype 完整路径及类型路径。"""

        prototypes: dict[str, str] = {}
        for composition in self._elements_of(CompositionSwComponentType):
            if composition_path is not None and composition.getFullName() != composition_path:
                continue
            for prototype in composition.getComponents():
                name = prototype.getFullName()
                type_path = ref_value(prototype.getTypeTRef())
                if name and type_path:
                    prototypes[name] = type_path
        return prototypes

    def composition_types(self) -> set[str]:
        """返回全部 Composition 类型的完整路径。"""

        return {composition.getFullName() for composition in self._elements_of(CompositionSwComponentType)}

    def component_types(self) -> dict[str, str]:
        """返回 ``组件类型完整路径 -> 组件类型简称``，不合并同短名类型。"""

        result: dict[str, str] = {}
        for component in self._elements_of(SwComponentType):
            if isinstance(component, CompositionSwComponentType):
                continue
            name = component.getShortName()
            if name:
                result[component.getFullName()] = name
        return result

    # -- System / Topology --------------------------------------------------

    def find_system(self, system_name: str):
        """按 SHORT-NAME 或完整路径查找 System；未定义时返回 None，重名时报错。"""

        key = "getFullName" if system_name.startswith("/") else "getShortName"
        systems = [system for system in self._elements_of(System)
                   if getattr(system, key)() == system_name]
        if len(systems) > 1:
            raise ValueError(
                f"Ambiguous System '{system_name}'; use its full path: "
                + ", ".join(system.getFullName() for system in systems))
        return systems[0] if systems else None

    def root_composition_path(self, system_name: str) -> str:
        """返回指定 System 下 ROOT-SOFTWARE-COMPOSITION 的完整路径。

        AUTOSAR 规定每个 System 最多只有一个 ROOT-SOFTWARE-COMPOSITION，因此确定了
        System 就能唯一确定它；该 System 未定义时返回空串。
        """

        system = self.find_system(system_name)
        if system is None:
            raise ValueError(f"System '{system_name}' was not found in the loaded ARXML files")
        root = system.getRootSoftwareComposition()
        return "" if root is None else (root.getFullName() or "")

    def _children_of_compositions(self) -> dict[str, list[tuple[str, str]]]:
        """``组合类型路径 -> [(子实例完整路径, 子实例类型路径)]``，结果缓存复用。"""

        if self._composition_children is None:
            self._composition_children = {
                composition.getFullName(): [
                    (prototype.getFullName(), ref_value(prototype.getTypeTRef()))
                    for prototype in composition.getComponents()
                ]
                for composition in self._elements_of(CompositionSwComponentType)
            }
        return self._composition_children

    def composition_chains(self, root_type: str, target_type: str) -> list[tuple[str, ...]]:
        """返回从 ``root_type`` 逐层实例化到 ``target_type`` 的全部实例链。

        每条链是途经的组件实例完整路径，不含根组合本身；``root_type`` 等于目标时返回一条空链。
        嵌套超过 :data:`MAX_COMPOSITION_DEPTH` 层的分支视为无效。
        """

        children = self._children_of_compositions()

        def walk(type_path: str, chain: tuple[str, ...]) -> list[tuple[str, ...]]:
            if type_path == target_type:
                return [chain]
            if len(chain) >= MAX_COMPOSITION_DEPTH:
                return []
            return [
                found
                for instance_path, child_type in children.get(type_path, ())
                for found in walk(child_type, chain + (instance_path,))
            ]

        return walk(root_type, ())

    def systems_containing(self, composition_path: str) -> list:
        """返回根组合逐层实例化了 ``composition_path`` 的全部 System。"""

        systems = []
        for system in self._elements_of(System):
            root = system.getRootSoftwareComposition()
            if root is not None and self.composition_chains(
                    ref_value(root.getSoftwareCompositionTRef()), composition_path):
                systems.append(system)
        return systems

    @staticmethod
    def system_ecu_instances(system) -> list[str]:
        """返回 System 的 FIBEX-ELEMENTS 中引用的 ECU-INSTANCE 路径。"""

        return [ref_value(ref) for ref in system.getFibexElementRefs() if ref.getDest() == "ECU-INSTANCE"]

    # -- CAN/LIN ------------------------------------------------------------

    def channels(self) -> dict[str, set[str]]:
        """返回 ``物理通道短名 -> 所属总线类型集合（CAN/LIN）``。

        通道名只用于拼接信号路径，不同簇中的同名通道不视为错误。
        """

        result: dict[str, set[str]] = {}
        for cluster in self._elements_of(CommunicationCluster):
            module = bus_module(cluster)
            if not module:
                continue
            for channel in cluster.getPhysicalChannels():
                name = channel.getShortName()
                if name:
                    result.setdefault(name, set()).add(module)
        return result

    def implementations(self, behavior_path: str) -> list[str]:
        """返回 BEHAVIOR-REF 指向 ``behavior_path`` 的全部 SWC-IMPLEMENTATION 完整路径。"""

        return sorted(
            implementation.getFullName()
            for implementation in self._elements_of(SwcImplementation)
            if ref_value(implementation.getBehaviorRef()) == behavior_path
        )

    def schema_files(self) -> set[str]:
        """返回已加载 ARXML 在 ``xsi:schemaLocation`` 中声明的 XSD 文件名。"""

        schemas: set[str] = set()
        for path in self.paths:
            try:
                with open(path, "rb") as stream:
                    head = stream.read(4096).decode("utf-8", "replace")
            except OSError:
                continue
            match = _SCHEMA_LOCATION.search(head)
            if match:
                schemas.add(match.group(1))
        return schemas

    def signal_groups(self) -> dict[str, set[str]]:
        """按 SystemSignal 完整路径返回其所属的 System/I-SignalGroup 完整路径；不追踪总线 triggering。"""

        if self._groups is not None:
            return self._groups
        group_members: dict[str, set[str]] = {}
        for group in self._elements_of(SystemSignalGroup):
            for reference in group.getSystemSignalRefs():
                path = ref_value(reference)
                if path:
                    group_members.setdefault(path, set()).add(group.getFullName())
        for group in self._elements_of(ISignalGroup):
            for signal_ref in group.getISignalRefs():
                signal = self._document.find(ref_value(signal_ref))
                if isinstance(signal, ISignal) and ref_value(signal.getSystemSignalRef()):
                    group_members.setdefault(ref_value(signal.getSystemSignalRef()), set()).add(group.getFullName())
        self._groups = group_members
        return group_members

    def validate_signal_mapping(self, signal_path: str) -> None:
        signal = self._document.find(signal_path)
        if isinstance(signal, (SystemSignalGroup, ISignalGroup)):
            raise ValueError(f"Signal group '{signal_path}' cannot be mapped as an individual signal")
        if not isinstance(signal, SystemSignal):
            raise ValueError(f"SystemSignal is not defined: {signal_path}")
        groups = self.signal_groups().get(signal_path, set())
        if groups:
            LOGGER.warning(
                f"Signal '{signal_path}' belongs to signal group(s) {', '.join(sorted(groups))}; "
                "mapping the member individually; verify group semantics during integration")
