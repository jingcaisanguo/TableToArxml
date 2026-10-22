"""在清单列出的 ARXML 中定位组件、接口、runnable，把工作簿请求补全为可直接生成的描述。"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from ..common.messages import clean_message
from . import xml_patch
from ..common.names import generated_name
from .arxml_model import AccessInfo, InterfaceSummary, PortInfo
from .constants import (
    ACCESS_CONTAINERS,
    ACCESS_DIRECTIONS,
    ACCESS_READ,
    ACCESS_RECEIVE_BY_ARGUMENT,
    ACCESS_RECEIVE_BY_VALUE,
    ACCESS_SEND,
    ACCESS_WRITE,
    CLIENT_SERVER_INTERFACE,
    DEFAULT_ACCESS_KINDS,
    DIRECTION_LABELS,
    DIRECTION_PROVIDED,
    SENDER_RECEIVER_INTERFACE,
)
from .store import DefinitionKind, SourceStore
from .workbook import AccessSpec, ComponentSpec, PortSpec

LOGGER = logging.getLogger(__name__)

# ``Access Name`` 留空时按端口名生成的默认前缀。
ACCESS_NAME_PREFIXES = {
    ACCESS_SEND: "dsp_",
    ACCESS_RECEIVE_BY_ARGUMENT: "drparg_",
    ACCESS_RECEIVE_BY_VALUE: "drpval_",
    ACCESS_READ: "dra_",
    ACCESS_WRITE: "dwa_",
}


@dataclass(frozen=True)
class ResolvedPort:
    """补全了目标文件、接口元类和 ComSpec 引用之后的端口请求。

    ComSpec 的种类由数据元素的 ``SW-IMPL-POLICY`` 决定（constr_1129–1132）：``queued_sender_elements``
    是 ``sender_elements`` 中需要生成 ``QUEUED-SENDER-COM-SPEC`` 的队列元素。
    """

    spec: PortSpec
    component_path: str
    component_file: Path
    interface_dest: str
    update_elements: tuple[str, ...] = ()
    queued_elements: tuple[tuple[str, int], ...] = ()
    sender_elements: tuple[tuple[str, float | None, bool], ...] = ()
    queued_sender_elements: tuple[str, ...] = ()

    @property
    def port_path(self) -> str:
        return f"{self.component_path}/{self.spec.name}"


@dataclass(frozen=True)
class ResolvedAccess:
    """补全了 runnable 路径、访问点类别和引用目标之后的访问点请求；``reused`` 表示复用已有访问点。"""

    spec: AccessSpec
    component_path: str
    component_file: Path
    runnable_path: str
    kind: str
    name: str
    port_path: str
    data_path: str
    reused: bool = False

    @property
    def container(self) -> str:
        return ACCESS_CONTAINERS[self.kind]

    @property
    def access_path(self) -> str:
        return f"{self.runnable_path}/{self.name}"


@dataclass(frozen=True)
class LocatedAccess:
    """访问点请求在组件文件中定位到的信息，尚未确定数据元素和名称。

    ``update_elements`` 是该端口接收端 ComSpec 中 ``ENABLE-UPDATE=true`` 的数据元素（已有或本次新建）。
    """

    spec: AccessSpec
    component_path: str
    component_file: Path
    runnable_path: str
    kind: str
    interface_path: str
    update_elements: tuple[str, ...] = ()

    @property
    def port_path(self) -> str:
        return f"{self.component_path}/{self.spec.port_name}"


@dataclass(frozen=True)
class RunnableNamespace:
    """runnable 的命名空间：全部直接子对象的 SHORT-NAME（区分大小写）及各类已有访问点。"""

    names: tuple[str, ...]
    accesses: dict[str, list[AccessInfo]]


def find_definition(
    arxml_files: Sequence[Path],
    autosar_path: str,
    definition_kind: DefinitionKind,
    store: SourceStore,
) -> tuple[Path, str] | None:
    """在清单范围内定位 AUTOSAR 路径，结果在文件未修改前复用。

    ``definition_kind`` 为 ``component`` 或 ``interface``；接口返回其 ``DEST``，组件返回空串。
    存在多个定义时使用清单中的第一个，并记录 warning。
    单个候选文件的解析失败不中断流程，但会在错误信息中一并报告。
    """
    memo_key = (tuple(arxml_files), definition_kind, autosar_path)
    if memo_key not in store.definitions:
        store.definitions[memo_key] = _find_definition(arxml_files, autosar_path, definition_kind, store)
    return store.definitions[memo_key]


def _find_definition(
    arxml_files: Sequence[Path],
    autosar_path: str,
    definition_kind: DefinitionKind,
    store: SourceStore,
) -> tuple[Path, str] | None:
    short_name = autosar_path.rsplit("/", maxsplit=1)[-1]
    matches: list[tuple[Path, str]] = []
    failures: list[str] = []
    # 先按 SHORT-NAME 反向索引预筛候选文件，避免逐个解析整份清单。
    for candidate in store.short_name_index(arxml_files).get(short_name, []):
        try:
            summary = store.summary(candidate)
        except (OSError, ValueError, RuntimeError, SyntaxError) as error:
            failures.append(f"{candidate}: {clean_message(error)}")
            continue
        if definition_kind == "component":
            if autosar_path in summary.components:
                matches.append((candidate, ""))
            continue
        interface = summary.interfaces.get(autosar_path)
        if interface is None:
            continue
        if not interface.dest:
            if not matches:
                LOGGER.error(
                    "Unsupported port interface type at %s: %s",
                    autosar_path,
                    interface.type_name,
                )
                return None
            continue
        matches.append((candidate, interface.dest))

    if failures:
        LOGGER.error(
            "Cannot determine the first %s at %s; parser failures: %s",
            definition_kind, autosar_path, "; ".join(failures),
        )
        return None
    if len(matches) == 1:
        return matches[0]
    if matches:
        LOGGER.warning(
            "Multiple definitions found for %s %s: %s; using %s for SWC resolution. "
            "The AUTOSAR importer should validate duplicate definitions",
            definition_kind,
            autosar_path,
            ", ".join(str(path) for path, _ in matches),
            matches[0][0],
        )
        return matches[0]
    if not matches:
        detail = f"; parser failures: {'; '.join(failures)}" if failures else ""
        LOGGER.error("No %s found at %s in listed ARXML%s", definition_kind, autosar_path, detail)
        return None
    return None


def interface_summary(
    arxml_files: Sequence[Path],
    interface_path: str,
    store: SourceStore,
) -> InterfaceSummary | None:
    """返回首个接口定义的摘要；无法确定首个定义时返回 ``None``。"""

    found = find_definition(arxml_files, interface_path, "interface", store)
    if found is None:
        return None
    return store.summary(found[0]).interfaces[interface_path]


def interface_elements(
    arxml_files: Sequence[Path],
    interface_path: str,
    store: SourceStore,
) -> list[str]:
    """返回接口的数据元素简称；接口不可解析时返回空列表。"""

    summary = interface_summary(arxml_files, interface_path, store)
    return [] if summary is None else list(summary.data_elements)


def resolve_components(
    components: dict[str, ComponentSpec],
    arxml_files: Sequence[Path],
    store: SourceStore,
) -> dict[str, Path]:
    """定位每个组件的首个定义文件；无法确定首个定义的组件不出现在结果中。"""

    component_files: dict[str, Path] = {}
    for component in components.values():
        found = find_definition(arxml_files, component.path, "component", store)
        if found is not None:
            component_files[component.key] = found[0]
    return component_files


def _com_spec_paths(
    port: PortSpec,
    component_path: str,
    existing: PortInfo | None,
    elements: list[str],
) -> tuple[str, ...] | None:
    """确定 ComSpec 引用的数据元素路径：新建端口和没有 ComSpec 的已有端口展开接口全部元素，
    其他已有端口沿用其 ComSpec 的引用。"""

    data_paths: tuple[str, ...] = () if existing is None else tuple(dict.fromkeys(
        [*existing.update_elements]
        + [path for path, _ in existing.queued_elements]
        + [path for path, _, _ in existing.sender_elements]
    ))
    if not data_paths:
        if not elements:
            LOGGER.error(
                "Skipped port %s: ComSpec flags require data elements but interface %s has none",
                port.name,
                port.interface_path,
            )
            return None
        return tuple(f"{port.interface_path}/{element}" for element in elements)
    if any(path.rpartition("/")[0] != port.interface_path for path in data_paths):
        LOGGER.error(
            "Port %s/%s already exists with conflicting ComSpec references; "
            "please choose a different port name",
            component_path,
            port.name,
        )
        return None
    return data_paths


def _warn_ignored_port_fields(port: PortSpec, interface_dest: str) -> None:
    """对已填写、但在当前端口方向、接口类型或 Create RTE Artifact 设置下不生效的列给出 WARNING。"""

    provided = port.provided_fields
    ignored: list[str] = []
    data_interface = interface_dest == SENDER_RECEIVER_INTERFACE
    if not port.create_rte_artifact:
        artifact_fields = (
            "Runnable Name",
            "Runnable Symbol",
            "Minimum Start Interval",
            "Can Be Invoked Concurrently",
        )
        ignored.extend(name for name in artifact_fields if name in provided)
        if "Access Kind" in provided:
            ignored.append("Access Kind")
    else:
        if interface_dest != CLIENT_SERVER_INTERFACE:
            ignored.extend(
                name
                for name in (
                    "Runnable Symbol",
                    "Minimum Start Interval",
                    "Can Be Invoked Concurrently",
                )
                if name in provided
            )
        if not data_interface and interface_dest != CLIENT_SERVER_INTERFACE:
            if "Runnable Name" in provided:
                ignored.append("Runnable Name")
        if not data_interface and "Access Kind" in provided:
            ignored.append("Access Kind")

    if ignored:
        LOGGER.warning(
            "Port %s: column(s) %s do not apply to %s %s and will be ignored",
            port.name,
            ", ".join(ignored),
            DIRECTION_LABELS[port.direction],
            interface_dest,
        )


def resolve_ports(
    components: dict[str, ComponentSpec],
    ports: Sequence[PortSpec],
    component_files: dict[str, Path],
    arxml_files: Sequence[Path],
    store: SourceStore,
) -> list[ResolvedPort]:
    """为每条端口请求补全目标 ARXML 文件和接口 ``DEST``。

    接口只查找一次，多条端口共用查找结果。新建端口按接口元素生成 ComSpec；
    已有端口只读取自身 ComSpec 中的引用，不展开接口元素。无法解析的组件或接口
    只记录 error，对应的端口请求被跳过，其余请求继续处理。
    """
    existing_ports: dict[tuple[str, str], PortInfo] = {}
    for component_key in {port.component_key for port in ports}:
        if component_key not in component_files:
            continue
        document = store.load(component_files[component_key])
        for port in ports:
            if port.component_key != component_key:
                continue
            existing = document.read_port(components[component_key].path, port.name)
            if existing is not None:
                existing_ports[(component_key, port.name)] = existing

    interface_dests: dict[str, str] = {}
    for interface_path in sorted({port.interface_path for port in ports}):
        found = find_definition(arxml_files, interface_path, "interface", store)
        if found is not None:
            interface_dests[interface_path] = found[1]

    resolved: list[ResolvedPort] = []
    for port in ports:
        component_path = components[port.component_key].path
        if port.component_key not in component_files:
            LOGGER.error("Skipped port %s: component %s is unresolved", port.name, component_path)
            continue
        if port.interface_path not in interface_dests:
            LOGGER.error("Skipped port %s: interface %s is unresolved", port.name, port.interface_path)
            continue
        interface_dest = interface_dests[port.interface_path]
        _warn_ignored_port_fields(port, interface_dest)
        if (
            port.create_rte_artifact
            and port.direction == DIRECTION_PROVIDED
            and interface_dest == SENDER_RECEIVER_INTERFACE
            and not port.runnable_name
        ):
            LOGGER.error(
                "Skipped port %s: Runnable Name is required for a Sender-Receiver "
                "P-Port when Create RTE Artifact is Y",
                port.name,
            )
            continue

        update_elements: tuple[str, ...] = ()
        queued_elements: tuple[tuple[str, int], ...] = ()
        sender_elements: tuple[tuple[str, float | None, bool], ...] = ()
        queued_senders: tuple[str, ...] = ()
        if port.needs_com_specs:
            existing = existing_ports.get((port.component_key, port.name))
            elements = interface_elements(arxml_files, port.interface_path, store)
            data_paths = _com_spec_paths(port, component_path, existing, elements)
            if data_paths is None:
                continue
            summary = interface_summary(arxml_files, port.interface_path, store)
            queued_names = set(() if summary is None else summary.queued_data_elements)
            queued_paths = tuple(path for path in data_paths if path.rpartition("/")[2] in queued_names)
            plain_paths = tuple(path for path in data_paths if path not in queued_paths)
            if port.direction == DIRECTION_PROVIDED:
                sender_elements = tuple(
                    (data_path, port.ack_timeout, port.uses_protection) for data_path in data_paths
                )
                queued_senders = queued_paths
            else:
                # 列对所选数据元素都不适用时只提示并忽略该列，端口照常生成，由 EB 做完整校验。
                if port.enable_update and not plain_paths:
                    LOGGER.warning(
                        "Port %s: Enable Update applies only to non-queued data elements, "
                        "but all ComSpec data elements of %s are queued (SW-IMPL-POLICY=QUEUED); "
                        "column ignored",
                        port.name, port.interface_path,
                    )
                if port.queue_length > 0 and not queued_paths:
                    LOGGER.warning(
                        "Port %s: Queue Length applies only to queued data elements "
                        "(SW-IMPL-POLICY=QUEUED), but no ComSpec data element of %s is queued; "
                        "column ignored",
                        port.name, port.interface_path,
                    )
                if port.enable_update:
                    update_elements = plain_paths
                if port.queue_length > 0:
                    queued_elements = tuple((data_path, port.queue_length) for data_path in queued_paths)

        resolved.append(
            ResolvedPort(
                spec=port,
                component_path=component_path,
                component_file=component_files[port.component_key],
                interface_dest=interface_dest,
                update_elements=update_elements,
                queued_elements=queued_elements,
                sender_elements=sender_elements,
                queued_sender_elements=queued_senders,
            )
        )
    return resolved


def expand_port_accesses(
    ports: Sequence[ResolvedPort],
    arxml_files: Sequence[Path],
    store: SourceStore,
) -> list[AccessSpec]:
    """把端口行上的 Y 展开为 SR 接口全部数据元素的访问点请求。

    ``Access Kind`` 留空时 P-Port 取 Send、R-Port 取 ReceiveByArgument；方向匹配已在读表时校验。
    """

    specs: list[AccessSpec] = []
    for port in ports:
        if not port.spec.create_rte_artifact or port.interface_dest != SENDER_RECEIVER_INTERFACE:
            continue
        runnable = port.spec.runnable_name
        if not runnable:
            LOGGER.error("Skipped automatic data access for %s: Runnable is required", port.port_path)
            continue
        elements = interface_elements(arxml_files, port.spec.interface_path, store)
        if not elements:
            LOGGER.error(
                "Skipped automatic data access for %s: interface has no data elements", port.port_path
            )
            continue
        kind = port.spec.access_kind or DEFAULT_ACCESS_KINDS[port.spec.direction]
        specs.extend(
            AccessSpec(
                component_key=port.spec.component_key,
                runnable_name=runnable,
                port_name=port.spec.name,
                data_element=element,
                kind=kind,
                name="",
            )
            for element in elements
        )
    return specs


def _port_from_document(
    document,
    source: bytes,
    component_path: str,
    port_name: str,
    planned: ResolvedPort | None,
) -> tuple[str, str, tuple[str, ...]] | str:
    """返回端口的方向、接口路径和 ``ENABLE-UPDATE`` 元素；无法作为访问点目标时返回错误描述。"""

    existing = document.read_port(component_path, port_name)
    if existing is not None and existing.direction in DEFAULT_ACCESS_KINDS:
        return existing.direction, existing.interface_path, existing.update_elements
    if existing is None and planned is not None:
        return planned.spec.direction, planned.spec.interface_path, planned.update_elements
    port_path = f"{component_path}/{port_name}"
    if (existing is not None and existing.type_name == "PRPortPrototype") or (
        xml_patch.element_tag(source, port_path) == "PR-PORT-PROTOTYPE"
    ):
        return f"PR-Port {port_path} is not supported; use a P-Port or R-Port"
    return f"port {port_path} is neither planned in this run nor an existing port prototype"


def locate_accesses(
    components: dict[str, ComponentSpec],
    specs: Sequence[AccessSpec],
    resolved_ports: Sequence[ResolvedPort],
    component_files: dict[str, Path],
    store: SourceStore,
) -> tuple[list[LocatedAccess], dict[str, RunnableNamespace]]:
    """按组件文件分组解析 runnable、端口方向和已有访问点。

    每个组件文件只加载一次；同时返回相关 runnable 的命名空间（全部直接子对象名称和各类已有访问点），
    供后续复用已有访问点和自动命名避让。
    """
    # 本次处理成功的端口，按“组件简称 + 端口名”索引。
    planned = {(port.spec.component_key, port.spec.name): port for port in resolved_ports}
    specs_by_key: dict[str, list[AccessSpec]] = defaultdict(list)
    for spec in specs:
        if spec.component_key not in component_files:
            LOGGER.error(
                "Skipped data access for port %s: component %s is unresolved",
                spec.port_name,
                components[spec.component_key].path,
            )
            continue
        specs_by_key[spec.component_key].append(spec)

    located: list[LocatedAccess] = []
    namespaces: dict[str, RunnableNamespace] = {}
    for component_key, component_specs in specs_by_key.items():
        component_path = components[component_key].path
        component_file = component_files[component_key]
        document = store.load(component_file)
        source = store.read(component_file)

        for spec in component_specs:
            runnable_path = document.find_runnable(component_path, spec.runnable_name)
            if runnable_path is None:
                LOGGER.error(
                    "Skipped data access for port %s: runnable %s not found below %s",
                    spec.port_name,
                    spec.runnable_name,
                    component_path,
                )
                continue

            port = _port_from_document(
                document, source, component_path, spec.port_name,
                planned.get((component_key, spec.port_name)),
            )
            if isinstance(port, str):
                LOGGER.error("Skipped data access for port %s: %s", spec.port_name, port)
                continue
            direction, interface_path, update_elements = port

            kind = spec.kind or DEFAULT_ACCESS_KINDS[direction]
            if ACCESS_DIRECTIONS[kind] != direction:
                LOGGER.error(
                    "Skipped data access for port %s: Access Kind %s requires a "
                    "%s-Port but %s is a %s-Port",
                    spec.port_name,
                    kind,
                    ACCESS_DIRECTIONS[kind],
                    spec.port_name,
                    direction,
                )
                continue

            if runnable_path not in namespaces:
                namespaces[runnable_path] = RunnableNamespace(
                    xml_patch.child_names(source, runnable_path),
                    {
                        access_kind: document.list_accesses(runnable_path, access_kind)
                        for access_kind in ACCESS_CONTAINERS
                    },
                )
            located.append(
                LocatedAccess(
                    spec=spec,
                    component_path=component_path,
                    component_file=component_file,
                    runnable_path=runnable_path,
                    kind=kind,
                    interface_path=interface_path,
                    update_elements=update_elements,
                )
            )
    return located, namespaces


def generate_access_name(
    taken: Mapping[str, str],
    used: Mapping[str, str],
    kind: str,
    port_name: str,
    data_element: str,
) -> str | None:
    """为留空的 ``Access Name`` 生成一个在该 runnable 命名空间内不冲突的名称。

    ``taken`` 与 ``used`` 以 casefold 名称为键，分别是 runnable 已有的全部直接子对象（各类访问点、
    服务器调用点等）和本次运行已分配的名称。自动命名主动避开仅大小写不同的名称，减少 EB 校验时的提示。
    优先使用 ``<前缀><端口名>``，被占用时依次尝试追加数据元素名和序号。
    """
    base = f"{ACCESS_NAME_PREFIXES[kind]}{port_name}"
    candidates = [base, f"{base}_{data_element}"]
    candidates += [f"{base}_{data_element}{index}" for index in range(2, 100)]
    for raw_candidate in candidates:
        candidate = generated_name(raw_candidate)
        if candidate.casefold() not in taken and candidate.casefold() not in used:
            return candidate
    return None


def resolve_data_element(spec: AccessSpec, interface_path: str, elements: Sequence[str]) -> str | None:
    """确定访问点指向的数据元素；无法唯一确定时记录 error 并返回 ``None``。"""

    if not elements:
        LOGGER.error(
            "Skipped data access for port %s: interface %s has no data elements",
            spec.port_name,
            interface_path,
        )
        return None
    if not spec.data_element:
        if len(elements) != 1:
            LOGGER.error(
                "Skipped data access for port %s: interface %s has %d data "
                "elements %s, fill in the Data Element column",
                spec.port_name,
                interface_path,
                len(elements),
                list(elements),
            )
            return None
        return elements[0]
    if spec.data_element not in elements:
        LOGGER.error(
            "Skipped data access for port %s: data element %s not found in interface %s, available: %s",
            spec.port_name,
            spec.data_element,
            interface_path,
            list(elements),
        )
        return None
    return spec.data_element


def _access_kind_problem(item: LocatedAccess, data_path: str, queued: bool) -> str | None:
    """访问点类别与数据元素实现策略、接收端 ComSpec 的组合提示；属于 AUTOSAR 规则，只警告，由 EB 校验。"""

    if queued and item.kind == ACCESS_READ:
        return "AUTOSAR does not allow DataReadAccess for queued data elements"
    if queued and item.kind == ACCESS_RECEIVE_BY_VALUE:
        return "AUTOSAR does not allow ReceiveByValue for queued data elements"
    if item.kind == ACCESS_READ and data_path in item.update_elements:
        return "AUTOSAR does not allow DataReadAccess when the receiver ComSpec has ENABLE-UPDATE=true"
    return None


def resolve_accesses(
    components: dict[str, ComponentSpec],
    specs: Sequence[AccessSpec],
    resolved_ports: Sequence[ResolvedPort],
    component_files: dict[str, Path],
    arxml_files: Sequence[Path],
    store: SourceStore,
) -> tuple[list[ResolvedAccess], int]:
    """把每条访问点请求补全为可直接生成 VARIABLE-ACCESS 的完整描述，返回结果和被合并的重复请求数。

    先按组件文件分组定位 runnable、端口方向和已有访问点，再解析数据元素并生成名称：
    后端的模型是全局单例，加载接口所在文件会使组件文档失效，因此两个阶段必须分开。
    解析出数据元素后按 ``(runnable, 类别, 端口, 数据元素)`` 的原文合并重复请求；已有访问点引用相同
    端口和数据元素时直接复用（不论名称）。访问类别与队列策略、ENABLE-UPDATE 的 AUTOSAR 组合规则只警告；
    其他条件不满足时记录 error 并跳过该条请求。
    """
    located, namespaces = locate_accesses(
        components, specs, resolved_ports, component_files, store
    )

    groups: dict[tuple[str, str, str, str], list[tuple[LocatedAccess, str]]] = {}
    for item in located:
        summary = interface_summary(arxml_files, item.interface_path, store)
        elements = () if summary is None else summary.data_elements
        data_element = resolve_data_element(item.spec, item.interface_path, elements)
        if data_element is None:
            continue
        data_path = f"{item.interface_path}/{data_element}"
        problem = _access_kind_problem(
            item, data_path, summary is not None and data_element in summary.queued_data_elements
        )
        if problem is not None:
            LOGGER.warning(
                "Data access for port %s data element %s in %s: %s; created anyway, "
                "left to EB validation",
                item.spec.port_name, data_element, item.runnable_path, problem,
            )
        # 仅大小写不同的路径是不同对象，按原文去重。
        key = (item.runnable_path, item.kind, item.port_path, data_path)
        groups.setdefault(key, []).append((item, data_path))

    resolved: list[ResolvedAccess] = []
    merged = 0
    used: dict[str, set[str]] = defaultdict(set)
    for requests in groups.values():
        item, data_path = requests[0]
        names = list(dict.fromkeys(request.spec.name for request, _ in requests if request.spec.name))
        context = f"{item.runnable_path} ({item.spec.port_name} -> {data_path.rpartition('/')[2]})"
        if len(names) > 1:
            LOGGER.error(
                "Skipped data access %s: workbook rows request the same access with different "
                "Access Names %s; keep one row",
                context, ", ".join(names),
            )
            continue
        if len(requests) > 1:
            merged += len(requests) - 1
            LOGGER.info("Merged %d duplicate data access request(s) for %s", len(requests) - 1, context)
        if names:
            item = next(request for request, _ in requests if request.spec.name)

        namespace = namespaces[item.runnable_path]
        existing = next(
            (
                access for access in namespace.accesses[item.kind]
                if access.port_path == item.port_path and access.data_path == data_path
            ),
            None,
        )
        if existing is not None:
            LOGGER.warning("Data access %s already exists as %s; skipped", context, existing.name)
            name = existing.name
        elif names:
            name = names[0]
            if name in namespace.names:
                LOGGER.error(
                    "Skipped data access %s: SHORT-NAME %s is already used by %s/%s; "
                    "please choose a different access name",
                    context, name, item.runnable_path, name,
                )
                continue
            if name in used[item.runnable_path]:
                LOGGER.error(
                    "Skipped data access %s/%s: the name is already used in this run; "
                    "fill in a unique Access Name",
                    item.runnable_path,
                    name,
                )
                continue
            similar = [
                other for other in (*namespace.names, *used[item.runnable_path])
                if other.casefold() == name.casefold()
            ]
            if similar:
                LOGGER.warning(
                    "Data access %s/%s: SHORT-NAME differs only in case from %s; "
                    "created as a distinct object, left to EB validation",
                    item.runnable_path, name, ", ".join(sorted(set(similar))),
                )
        else:
            generated = generate_access_name(
                {other.casefold(): other for other in namespace.names},
                {other.casefold(): other for other in used[item.runnable_path]},
                item.kind, item.spec.port_name, data_path.rpartition("/")[2],
            )
            if generated is None:
                LOGGER.error(
                    "Skipped data access for port %s in %s: unable to generate a "
                    "free access name, fill in the Access Name column",
                    item.spec.port_name,
                    item.runnable_path,
                )
                continue
            name = generated
        used[item.runnable_path].add(name)

        resolved.append(
            ResolvedAccess(
                spec=item.spec,
                component_path=item.component_path,
                component_file=item.component_file,
                runnable_path=item.runnable_path,
                kind=item.kind,
                name=name,
                port_path=item.port_path,
                data_path=data_path,
                reused=existing is not None,
            )
        )
    return resolved, merged
