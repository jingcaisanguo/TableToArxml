"""在清单列出的 ARXML 中定位组件、接口、runnable，把工作簿请求补全为可直接生成的描述。"""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .lib.arxml_backend import (
    ACCESS_CONTAINERS,
    ACCESS_DIRECTIONS,
    ACCESS_READ,
    ACCESS_RECEIVE_BY_ARGUMENT,
    ACCESS_RECEIVE_BY_VALUE,
    ACCESS_SEND,
    ACCESS_WRITE,
    AccessInfo,
    ArxmlBackend,
    InterfaceSummary,
    PortInfo,
    clean_message,
)
from .store import SourceStore
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
# ``Access Kind`` 留空时按端口方向推导的默认类别。
DEFAULT_ACCESS_KINDS = {"P": ACCESS_SEND, "R": ACCESS_RECEIVE_BY_ARGUMENT}


@dataclass(frozen=True)
class ResolvedPort:
    """补全了目标文件、接口元类和 ComSpec 引用之后的端口请求。"""

    spec: PortSpec
    component_path: str
    component_file: Path
    interface_dest: str
    update_elements: tuple[str, ...] = ()
    queued_elements: tuple[tuple[str, int], ...] = ()
    sender_elements: tuple[tuple[str, float | None, bool], ...] = ()

    @property
    def port_path(self) -> str:
        return f"{self.component_path}/{self.spec.name}"


@dataclass(frozen=True)
class ResolvedAccess:
    """补全了 runnable 路径、访问点类别和引用目标之后的访问点请求。"""

    spec: AccessSpec
    component_path: str
    component_file: Path
    runnable_path: str
    kind: str
    name: str
    port_path: str
    data_path: str

    @property
    def container(self) -> str:
        return ACCESS_CONTAINERS[self.kind]

    @property
    def access_path(self) -> str:
        return f"{self.runnable_path}/{self.name}"


@dataclass(frozen=True)
class LocatedAccess:
    """访问点请求在组件文件中定位到的信息，尚未确定数据元素和名称。"""

    spec: AccessSpec
    component_path: str
    component_file: Path
    runnable_path: str
    kind: str
    interface_path: str
    name_exists: bool = False


def read_arxml_list(
    list_file: Path, project_root: Path, strict: bool = False,
    exclude_arxml: Sequence[str] = (),
) -> list[Path]:
    """读取 ARXML 清单，并把每条记录转为存在的绝对路径。

    空行和 ``#`` 注释行会忽略；Windows 反斜杠先转换，因此不依赖当前工作目录。
    默认对非法或缺失的条目记录 WARNING 并跳过，不中断其余条目的处理；
    传入 ``strict=True``（``--strict-list``）时遇到第一个问题立即抛出异常。
    """
    excluded_names: set[str] = set()
    excluded_paths: set[Path] = set()
    for value in exclude_arxml:
        excluded = Path(value.strip().strip('"').replace("\\", "/"))
        if excluded.name == str(excluded):
            excluded_names.add(excluded.name.casefold())
        else:
            excluded_paths.add((project_root / excluded).resolve())
    files: list[Path] = []
    seen: set[str] = set()
    for line_number, line in enumerate(
        list_file.read_text(encoding="utf-8-sig").splitlines(), start=1
    ):
        listed_path = line.strip().strip('"')
        if not listed_path or listed_path.startswith("#"):
            continue
        path = Path(listed_path.replace("\\", "/"))
        absolute_path = (path if path.is_absolute() else project_root / path).resolve()
        if absolute_path.name.casefold() in excluded_names or absolute_path in excluded_paths:
            LOGGER.info("Exclude ARXML file: %s", absolute_path)
            continue
        key = os.path.normcase(str(absolute_path))
        if key in seen:
            LOGGER.info("Skip duplicate ARXML entry at %s:%d: %s", list_file, line_number, absolute_path)
            continue
        seen.add(key)
        if absolute_path.suffix.casefold() != ".arxml":
            message = f"Non-ARXML record at {list_file}:{line_number}"
            if strict:
                raise ValueError(message)
            LOGGER.warning("%s; skipped", message)
            continue
        if not absolute_path.is_file():
            message = f"Listed ARXML not found at {list_file}:{line_number}: {absolute_path}"
            if strict:
                raise FileNotFoundError(message)
            LOGGER.warning("%s; skipped", message)
            continue
        files.append(absolute_path)
    if not files:
        raise ValueError(f"No usable ARXML files found in {list_file}")
    return files


def find_definition(
    backend: ArxmlBackend,
    arxml_files: Sequence[Path],
    autosar_path: str,
    kind: str,
    store: SourceStore,
) -> tuple[Path, str] | None:
    """在清单范围内定位一个 AUTOSAR 路径的唯一定义，结果在文件未修改前复用。

    ``kind`` 为 ``component`` 或 ``interface``；接口返回其 ``DEST``，组件返回空串。
    找不到或存在多个定义时记录 error 并返回 ``None``，由调用方跳过相关条目；
    单个候选文件的解析失败不中断流程，但会在错误信息中一并报告。
    """
    memo_key = (kind, autosar_path)
    if memo_key not in store.definitions:
        store.definitions[memo_key] = _find_definition(backend, arxml_files, autosar_path, kind, store)
    return store.definitions[memo_key]


def _find_definition(
    backend: ArxmlBackend,
    arxml_files: Sequence[Path],
    autosar_path: str,
    kind: str,
    store: SourceStore,
) -> tuple[Path, str] | None:
    short_name = autosar_path.rsplit("/", maxsplit=1)[-1]
    matches: list[tuple[Path, str]] = []
    failures: list[str] = []
    # 先按 SHORT-NAME 反向索引预筛候选文件，避免逐个解析整份清单。
    for candidate in store.short_name_index(arxml_files).get(short_name, []):
        try:
            summary = store.summary(backend, candidate)
        except (OSError, ValueError, RuntimeError, SyntaxError) as error:
            failures.append(f"{candidate}: {clean_message(error)}")
            continue
        if kind == "component":
            if autosar_path in summary.components:
                matches.append((candidate, ""))
            continue
        interface = summary.interfaces.get(autosar_path)
        if interface is None:
            continue
        if not interface.dest:
            LOGGER.error("Unsupported port interface type at %s: %s", autosar_path, interface.type_name)
            continue
        matches.append((candidate, interface.dest))

    if len(matches) == 1:
        return matches[0]
    if not matches:
        detail = f"; parser failures: {'; '.join(failures)}" if failures else ""
        LOGGER.error("No %s found at %s in listed ARXML%s", kind, autosar_path, detail)
        return None
    matched_files = ", ".join(str(path) for path, _ in matches)
    LOGGER.error("Multiple definitions found for %s: %s", autosar_path, matched_files)
    return None


def interface_summary(
    backend: ArxmlBackend,
    arxml_files: Sequence[Path],
    interface_path: str,
    store: SourceStore,
) -> InterfaceSummary | None:
    """返回接口的摘要；接口无法唯一定位时返回 ``None``。"""

    found = find_definition(backend, arxml_files, interface_path, "interface", store)
    if found is None:
        return None
    return store.summary(backend, found[0]).interfaces[interface_path]


def interface_elements(
    backend: ArxmlBackend,
    arxml_files: Sequence[Path],
    interface_path: str,
    store: SourceStore,
) -> list[str]:
    """返回接口的数据元素简称；接口不可解析时返回空列表。"""

    summary = interface_summary(backend, arxml_files, interface_path, store)
    return [] if summary is None else list(summary.data_elements)


def resolve_components(
    backend: ArxmlBackend,
    components: dict[str, ComponentSpec],
    arxml_files: Sequence[Path],
    store: SourceStore,
) -> dict[str, Path]:
    """定位每个组件所在的 ARXML；无法唯一定位的组件不会出现在结果中。"""

    component_files: dict[str, Path] = {}
    for component in components.values():
        found = find_definition(backend, arxml_files, component.path, "component", store)
        if found is not None:
            component_files[component.key] = found[0]
    return component_files


def _com_spec_paths(
    port: PortSpec,
    component_path: str,
    existing: PortInfo | None,
    elements: list[str],
) -> tuple[str, ...] | None:
    """确定 ComSpec 引用的数据元素路径：新建端口展开接口全部元素，已有端口沿用其 ComSpec 的引用。"""

    if existing is None:
        if not elements:
            LOGGER.error(
                "Skipped port %s: ComSpec flags require data elements but interface %s has none",
                port.name,
                port.interface_path,
            )
            return None
        return tuple(f"{port.interface_path}/{element}" for element in elements)
    data_paths = tuple(dict.fromkeys(
        [*existing.update_elements]
        + [path for path, _ in existing.queued_elements]
        + [path for path, _, _ in existing.sender_elements]
    ))
    if not data_paths or any(path.rpartition("/")[0] != port.interface_path for path in data_paths):
        LOGGER.error(
            "Port %s/%s already exists with missing or conflicting ComSpec references; "
            "please choose a different port name",
            component_path,
            port.name,
        )
        return None
    return data_paths


def _warn_ignored_port_fields(port: PortSpec, interface_dest: str) -> None:
    """Warn about filled port columns that do not apply to this artifact/interface combination."""

    provided = port.provided_fields
    ignored: list[str] = []
    data_interface = interface_dest == "SENDER-RECEIVER-INTERFACE"
    if not port.create_rte_artifact:
        artifact_fields = (
            ("Runnable Name",)
            if port.direction == "R"
            else (
                "Runnable Name",
                "Runnable Symbol",
                "Minimum Start Interval",
                "Can Be Invoked Concurrently",
            )
        )
        ignored.extend(name for name in artifact_fields if name in provided)
        if "Access Kind" in provided:
            ignored.append("Access Kind")
    else:
        if interface_dest != "CLIENT-SERVER-INTERFACE":
            ignored.extend(
                name
                for name in (
                    "Runnable Symbol",
                    "Minimum Start Interval",
                    "Can Be Invoked Concurrently",
                )
                if name in provided
            )
        if not data_interface and interface_dest != "CLIENT-SERVER-INTERFACE":
            if "Runnable Name" in provided:
                ignored.append("Runnable Name")
        if not data_interface and "Access Kind" in provided:
            ignored.append("Access Kind")

    if ignored:
        LOGGER.warning(
            "Port %s: column(s) %s do not apply to %s %s and will be ignored",
            port.name,
            ", ".join(ignored),
            "P-Port" if port.direction == "P" else "R-Port",
            interface_dest,
        )


def resolve_ports(
    backend: ArxmlBackend,
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
        document = store.load(backend, component_files[component_key])
        for port in ports:
            if port.component_key != component_key:
                continue
            existing = document.read_port(components[component_key].path, port.name)
            if existing is not None:
                existing_ports[(component_key, port.name)] = existing

    interface_dests: dict[str, str] = {}
    for interface_path in sorted({port.interface_path for port in ports}):
        found = find_definition(backend, arxml_files, interface_path, "interface", store)
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
            and port.direction == "P"
            and interface_dest == "SENDER-RECEIVER-INTERFACE"
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
        if port.needs_com_specs:
            existing = existing_ports.get((port.component_key, port.name))
            elements = [] if existing is not None else interface_elements(
                backend, arxml_files, port.interface_path, store
            )
            data_paths = _com_spec_paths(port, component_path, existing, elements)
            if data_paths is None:
                continue
            if port.direction == "P":
                sender_elements = tuple(
                    (data_path, port.ack_timeout, port.uses_protection) for data_path in data_paths
                )
            elif port.enable_update:
                update_elements = data_paths
            else:
                queued_elements = tuple((data_path, port.queue_length) for data_path in data_paths)

        resolved.append(
            ResolvedPort(
                spec=port,
                component_path=component_path,
                component_file=component_files[port.component_key],
                interface_dest=interface_dest,
                update_elements=update_elements,
                queued_elements=queued_elements,
                sender_elements=sender_elements,
            )
        )
    return resolved


def expand_port_accesses(
    backend: ArxmlBackend,
    ports: Sequence[ResolvedPort],
    arxml_files: Sequence[Path],
    store: SourceStore,
) -> list[AccessSpec]:
    """把端口行上的 Y 展开为 SR 接口全部数据元素的访问点请求。

    ``Access Kind`` 留空时 P-Port 取 Send、R-Port 取 ReceiveByArgument；方向匹配已在读表时校验。
    """

    specs: list[AccessSpec] = []
    for port in ports:
        if not port.spec.create_rte_artifact or port.interface_dest != "SENDER-RECEIVER-INTERFACE":
            continue
        runnable = port.spec.access_runnable
        if not runnable:
            LOGGER.error("Skipped automatic data access for %s: Runnable is required", port.port_path)
            continue
        elements = interface_elements(backend, arxml_files, port.spec.interface_path, store)
        if not elements:
            LOGGER.error(
                "Skipped automatic data access for %s: interface has no data elements", port.port_path
            )
            continue
        kind = port.spec.access_kind or DEFAULT_ACCESS_KINDS[port.spec.direction]
        specs.extend(
            AccessSpec(
                component_key=port.spec.component_key,
                runnable_ref=runnable,
                port_name=port.spec.name,
                data_element=element,
                kind=kind,
                name="",
            )
            for element in elements
        )
    return specs


def locate_accesses(
    backend: ArxmlBackend,
    components: dict[str, ComponentSpec],
    specs: Sequence[AccessSpec],
    resolved_ports: Sequence[ResolvedPort],
    component_files: dict[str, Path],
    store: SourceStore,
) -> tuple[list[LocatedAccess], dict[tuple[str, str], dict[str, AccessInfo]]]:
    """按组件文件分组解析 runnable、端口方向和已有访问点。

    每个组件文件只加载一次；同时返回相关 runnable 中已存在的访问点，供后续
    自动生成名称时判重和复用。
    """
    # 本次将要创建的端口，按“组件简称 + 端口名”索引。
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
    existing_accesses: dict[tuple[str, str], dict[str, AccessInfo]] = defaultdict(dict)
    for component_key, component_specs in specs_by_key.items():
        component_path = components[component_key].path
        component_file = component_files[component_key]
        document = store.load(backend, component_file)

        for spec in component_specs:
            runnable_path = document.find_runnable(component_path, spec.runnable_ref)
            if runnable_path is None:
                LOGGER.error(
                    "Skipped data access for port %s: runnable %s not found below %s",
                    spec.port_name,
                    spec.runnable_ref,
                    component_path,
                )
                continue

            port = planned.get((component_key, spec.port_name))
            if port is not None:
                direction = port.spec.direction
                interface_path = port.spec.interface_path
            else:
                existing = document.read_port(component_path, spec.port_name)
                if existing is None or existing.direction not in DEFAULT_ACCESS_KINDS:
                    LOGGER.error(
                        "Skipped data access for port %s: port %s/%s is neither "
                        "planned in this run nor an existing port prototype",
                        spec.port_name,
                        component_path,
                        spec.port_name,
                    )
                    continue
                direction = existing.direction
                interface_path = existing.interface_path

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

            key = (runnable_path, kind)
            if key not in existing_accesses:
                existing_accesses[key] = {
                    access.name: access for access in document.list_accesses(runnable_path, kind)
                }
            located.append(
                LocatedAccess(
                    spec=spec,
                    component_path=component_path,
                    component_file=component_file,
                    runnable_path=runnable_path,
                    kind=kind,
                    interface_path=interface_path,
                    name_exists=bool(spec.name) and document.element_exists(f"{runnable_path}/{spec.name}"),
                )
            )
    return located, existing_accesses


def generate_access_name(
    taken: dict[str, AccessInfo],
    runnable_path: str,
    kind: str,
    port_name: str,
    data_element: str,
    port_path: str,
    data_path: str,
    used_names: set[tuple[str, str]],
) -> str | None:
    """为留空的 ``Access Name`` 生成一个在该 runnable 内不冲突的名称。

    优先使用 ``<前缀><端口名>``；被同 runnable 中的其他访问点占用时依次尝试
    追加数据元素名和序号。已存在且引用目标完全一致的同名访问点会被复用，
    因此重复执行不会生成新名称。
    """
    base = f"{ACCESS_NAME_PREFIXES[kind]}{port_name}"
    candidates = [base, f"{base}_{data_element}"]
    candidates += [f"{base}_{data_element}{index}" for index in range(2, 100)]
    for candidate in candidates:
        if (runnable_path, candidate) in used_names:
            continue
        existing = taken.get(candidate)
        if existing is None:
            return candidate
        if existing.port_path == port_path and existing.data_path == data_path:
            return candidate
    return None


def resolve_data_element(spec: AccessSpec, interface_path: str, elements: list[str]) -> str | None:
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
                elements,
            )
            return None
        return elements[0]
    if spec.data_element not in elements:
        LOGGER.error(
            "Skipped data access for port %s: data element %s not found in interface %s, available: %s",
            spec.port_name,
            spec.data_element,
            interface_path,
            elements,
        )
        return None
    return spec.data_element


def resolve_accesses(
    backend: ArxmlBackend,
    components: dict[str, ComponentSpec],
    specs: Sequence[AccessSpec],
    resolved_ports: Sequence[ResolvedPort],
    component_files: dict[str, Path],
    arxml_files: Sequence[Path],
    store: SourceStore,
) -> list[ResolvedAccess]:
    """把每条访问点请求补全为可直接生成 VARIABLE-ACCESS 的完整描述。

    先按组件文件分组定位 runnable、端口方向和已有访问点，再解析数据元素并生成
    名称：后端的模型是全局单例，加载接口所在文件会使组件文档失效，因此两个阶段
    必须分开。已有同名访问点只比较自身引用，不查询引用目标；只有新建或无法
    确定数据元素时才读取接口。任一条件不满足时只记录 error 并跳过该条请求。
    """
    located, existing_accesses = locate_accesses(
        backend, components, specs, resolved_ports, component_files, store
    )

    resolved: list[ResolvedAccess] = []
    used_names: set[tuple[str, str]] = set()
    for item in located:
        port_path = f"{item.component_path}/{item.spec.port_name}"
        taken = existing_accesses[(item.runnable_path, item.kind)]
        existing = taken.get(item.spec.name)
        if not item.spec.name and item.spec.data_element:
            candidate = generate_access_name(
                taken,
                item.runnable_path,
                item.kind,
                item.spec.port_name,
                item.spec.data_element,
                port_path,
                f"{item.interface_path}/{item.spec.data_element}",
                used_names,
            )
            existing = taken.get(candidate or "")
        if item.name_exists and existing is None:
            LOGGER.error(
                "Data access %s/%s already exists as a different kind of object; "
                "please choose a different access name",
                item.runnable_path,
                item.spec.name,
            )
            continue
        if existing is not None:
            data_element = item.spec.data_element or existing.data_path.rpartition("/")[2]
            if not data_element:
                LOGGER.error(
                    "Data access %s/%s already exists with a missing data reference; "
                    "please choose a different access name",
                    item.runnable_path,
                    existing.name,
                )
                continue
        else:
            data_element = resolve_data_element(
                item.spec,
                item.interface_path,
                interface_elements(backend, arxml_files, item.interface_path, store),
            )
        if data_element is None:
            continue

        data_path = f"{item.interface_path}/{data_element}"
        if item.spec.name:
            name = item.spec.name
            if (item.runnable_path, name) in used_names:
                LOGGER.error(
                    "Skipped data access %s/%s: the name is already used in this run; "
                    "fill in a unique Access Name",
                    item.runnable_path,
                    name,
                )
                continue
        else:
            generated = generate_access_name(
                taken,
                item.runnable_path,
                item.kind,
                item.spec.port_name,
                data_element,
                port_path,
                data_path,
                used_names,
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
        used_names.add((item.runnable_path, name))

        resolved.append(
            ResolvedAccess(
                spec=item.spec,
                component_path=item.component_path,
                component_file=item.component_file,
                runnable_path=item.runnable_path,
                kind=item.kind,
                name=name,
                port_path=port_path,
                data_path=data_path,
            )
        )
    return resolved
