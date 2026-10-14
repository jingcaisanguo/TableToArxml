"""生成新元素的 XML 片段，按 AUTOSAR 路径插入原始字节，并重新解析验证结果。"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Sequence, Union

from . import xml_patch
from .lib.arxml_backend import (
    AccessInfo,
    ArxmlBackend,
    ImplementationTypeInfo,
    PortInfo,
    clean_message,
)
from .resolve import ResolvedAccess, ResolvedPort, interface_summary
from .store import SourceStore, load_bytes
from .workbook import (
    ArraySpec,
    ClientServerInterfaceSpec,
    EnumerationSpec,
    InterfaceSpec,
    StructureSpec,
)

LOGGER = logging.getLogger(__name__)
SR_INTERFACE_DEST = "SENDER-RECEIVER-INTERFACE"
CS_INTERFACE_DEST = "CLIENT-SERVER-INTERFACE"


@dataclass
class FileChange:
    """一个 ARXML 的修改结果，``document`` 为待写出的完整字节。"""

    source_file: Path
    document: bytes
    created_ports: list[ResolvedPort]
    existing_ports: list[ResolvedPort]
    created_accesses: list[str]


def check_existing_definition(context: str, existing: object, expected: object) -> bool:
    """只比较调用方提供的本层定义，不解析引用对象。"""

    if existing != expected:
        LOGGER.error(
            "%s already exists with a different definition: existing=%s, requested=%s; "
            "please choose a different name for the new object; existing content is unchanged",
            context,
            existing,
            expected,
        )
        return False
    LOGGER.warning("%s already exists with the same definition; skipped", context)
    return True


def check_port(existing: PortInfo | None, port: ResolvedPort) -> None:
    """校验端口方向、接口引用和本身的 ComSpec，不解析引用对象。

    同名端口已存在但内容不同时报错，使脚本可重复执行而不会静默覆盖已有配置。
    """
    if existing is None or existing.direction != port.spec.direction:
        actual_type = "missing" if existing is None else existing.type_name
        raise ValueError(
            f"{port.port_path} exists as {actual_type}, "
            f"expected {port.spec.element_name}; please choose a different port name"
        )
    if (
        existing.interface_dest != port.interface_dest
        or existing.interface_path != port.spec.interface_path
    ):
        raise ValueError(
            f"{port.port_path} conflicts with workbook: "
            f"DEST={existing.interface_dest!r}, interface={existing.interface_path!r}; "
            "please choose a different port name"
        )
    for label, actual, expected in (
        ("Enable Update elements", existing.update_elements, port.update_elements),
        ("QueueLength elements", existing.queued_elements, port.queued_elements),
        ("sender ComSpec elements", existing.sender_elements, port.sender_elements),
    ):
        if actual != expected:
            raise ValueError(
                f"{port.port_path} conflicts with workbook: {label}={list(actual)}; "
                "please choose a different port name"
            )


def check_access(existing: AccessInfo | None, access: ResolvedAccess) -> None:
    """校验访问点指向工作簿中记录的端口和数据元素。"""

    if existing is None:
        raise ValueError(f"{access.access_path} missing after creation")
    if existing.port_path != access.port_path or existing.data_path != access.data_path:
        raise ValueError(
            f"{access.access_path} conflicts with workbook: "
            f"port={existing.port_path!r}, data={existing.data_path!r}; "
            "please choose a different access name"
        )


def insert_package_element(
    store: SourceStore,
    target_file: Path,
    package_path: str,
    fragment: str,
    context: str,
) -> bool:
    """把元素插入包的 ``ELEMENTS`` 容器；包内还没有该容器时在子包之前一并创建。"""

    source = store.read(target_file)
    try:
        updated = xml_patch.insert_or_create_child_container(
            source, package_path, "ELEMENTS", [fragment], ("AR-PACKAGES",)
        )
    except ValueError as error:
        LOGGER.error(
            "Skipped %s: unable to locate package %s in %s: %s",
            context,
            package_path,
            target_file,
            clean_message(error),
        )
        return False
    store.write(target_file, updated)
    return True


def apply_enumerations(
    backend: ArxmlBackend,
    enumerations: Sequence[EnumerationSpec],
    store: SourceStore,
) -> tuple[list[str], int]:
    """创建尚不存在的枚举类型，并把结果字节写入缓存。

    每个枚举生成三个元素：TEXTTABLE 计算方法、引用它的实现数据类型和限定取值
    范围的数据约束。三者分别比较自身定义，不递归展开引用；相同则 warning，
    不同则 error 并提示改名，已有内容不会被覆盖。
    """
    created: list[str] = []
    skipped = 0
    for spec in enumerations:
        document = store.load(backend, spec.target_file)

        lower, upper = spec.limits
        elements = (
            (
                spec.compu_method_path,
                partial(document.add_compu_method, values=spec.values),
                "COMPU-METHOD",
                document.compu_method_definition,
                ("TEXTTABLE", tuple((value, value, label) for value, label in spec.values)),
            ),
            (
                spec.path,
                partial(
                    document.add_enum_data_type,
                    base_type_path=spec.base_type,
                    compu_method_path=spec.compu_method_path,
                    data_constr_path=spec.data_constr_path,
                ),
                "IMPLEMENTATION-DATA-TYPE",
                document.implementation_type_definition,
                ImplementationTypeInfo(
                    "TYPE_REFERENCE", spec.base_type, spec.compu_method_path,
                    spec.data_constr_path,
                ),
            ),
            (
                spec.data_constr_path,
                partial(document.add_data_constr, lower_limit=lower, upper_limit=upper),
                "DATA-CONSTR",
                document.data_constr_definition,
                ((lower, upper),),
            ),
        )

        failed = False
        inserted = 0
        for element_path, build_fragment, label, read_definition, expected in elements:
            if document.element_exists(element_path):
                if not check_existing_definition(
                    f"{label} {element_path} in {spec.target_file}",
                    read_definition(element_path),
                    expected,
                ):
                    failed = True
                continue
            package_path, _, name = element_path.rpartition("/")
            context = f"enumeration {spec.path} ({label})"
            if not document.package_exists(package_path):
                LOGGER.error(
                    "Skipped %s: package %s not found in %s", context, package_path, spec.target_file
                )
                failed = True
                break
            try:
                fragment = build_fragment(package_path, name)
            except ValueError as error:
                LOGGER.error("Skipped %s: %s", context, clean_message(error))
                failed = True
                break
            if not insert_package_element(store, spec.target_file, package_path, fragment, context):
                failed = True
                break
            inserted += 1

        if failed:
            skipped += 1
            continue
        if not inserted:
            continue

        created.append(spec.path)
        LOGGER.info(
            "create enumeration %s with %d value(s) in %s",
            spec.path,
            len(spec.values),
            spec.target_file,
        )
    return created, skipped


def apply_composite_types(
    backend: ArxmlBackend,
    arrays: Sequence[ArraySpec],
    structures: Sequence[StructureSpec],
    store: SourceStore,
) -> tuple[list[str], int]:
    """创建数组和结构体实现数据类型，并将 XML 片段写入缓存。"""

    created: list[str] = []
    skipped = 0
    seen_paths: set[str] = set()
    for spec in [*arrays, *structures]:
        kind = "array" if isinstance(spec, ArraySpec) else "structure"
        if spec.path in seen_paths:
            LOGGER.error("Skipped duplicate composite Type Path: %s", spec.path)
            skipped += 1
            continue
        seen_paths.add(spec.path)
        document = store.load(backend, spec.target_file)
        if document.element_exists(spec.path):
            if isinstance(spec, ArraySpec):
                expected = ImplementationTypeInfo("ARRAY", members=((
                    spec.element_name, "TYPE_REFERENCE", spec.element_type_ref,
                    str(spec.array_size), spec.array_size_semantics,
                ),))
            else:
                expected = ImplementationTypeInfo("STRUCTURE", members=tuple(
                    (name, "TYPE_REFERENCE", type_ref, "", "")
                    for name, type_ref in spec.members
                ))
            if not check_existing_definition(
                f"{kind} {spec.path} in {spec.target_file}",
                document.implementation_type_definition(spec.path), expected,
            ):
                skipped += 1
            continue
        if not document.package_exists(spec.package_path):
            LOGGER.error(
                "Skipped %s %s: package %s not found in %s",
                kind,
                spec.path,
                spec.package_path,
                spec.target_file,
            )
            skipped += 1
            continue
        try:
            if isinstance(spec, ArraySpec):
                fragment = document.add_array_data_type(
                    spec.package_path,
                    spec.name,
                    spec.element_name,
                    spec.element_type_ref,
                    spec.array_size,
                    spec.array_size_semantics,
                )
            else:
                fragment = document.add_structure_data_type(spec.package_path, spec.name, spec.members)
        except ValueError as error:
            LOGGER.error("Skipped %s %s: %s", kind, spec.path, clean_message(error))
            skipped += 1
            continue
        if not insert_package_element(
            store, spec.target_file, spec.package_path, fragment, f"{kind} {spec.path}"
        ):
            skipped += 1
            continue
        created.append(spec.path)
        LOGGER.info("create %s %s in %s", kind, spec.path, spec.target_file)
    return created, skipped


def apply_interfaces(
    backend: ArxmlBackend,
    interfaces: Sequence[Union[InterfaceSpec, ClientServerInterfaceSpec]],
    store: SourceStore,
) -> tuple[list[str], int]:
    """创建尚不存在的 SR/CS 接口，并把结果字节写入缓存。

    已存在时比较接口类型、数据元素（或 operation 与参数）和服务标志，不修改已有定义；
    任何不一致都记录 error 并跳过该接口，由主流程阻止全部 ARXML 写盘。
    返回新建接口路径列表和被跳过的数量。
    """
    created: list[str] = []
    skipped = 0
    for spec in interfaces:
        document = store.load(backend, spec.target_file)
        if isinstance(spec, InterfaceSpec):
            label, dest, members = "SR interface", SR_INTERFACE_DEST, spec.data_elements
            read_definition, add_interface = document.sender_receiver_definition, document.add_sr_interface
            expected: list = list(spec.data_elements)
            created_detail = f"{len(spec.data_elements)} data element(s)"
        else:
            label, dest, members = "CS interface", CS_INTERFACE_DEST, spec.operations
            read_definition, add_interface = document.client_server_definition, document.add_cs_interface
            expected = [(operation, list(arguments)) for operation, arguments in spec.operations]
            created_detail = f"{len(spec.operations)} operation(s)"

        if document.element_exists(spec.path):
            if not check_existing_definition(
                f"{label} {spec.path} in {spec.target_file}",
                (
                    document.interface_dest(spec.path),
                    read_definition(spec.path),
                    document.interface_is_service(spec.path),
                ),
                (dest, expected, spec.is_service),
            ):
                skipped += 1
            continue
        if not document.package_exists(spec.package_path):
            LOGGER.error(
                "Skipped %s %s: package %s not found in %s",
                label,
                spec.path,
                spec.package_path,
                spec.target_file,
            )
            skipped += 1
            continue
        try:
            fragment = add_interface(spec.package_path, spec.name, members, spec.is_service)
        except ValueError as error:
            LOGGER.error("Skipped %s %s: %s", label, spec.path, clean_message(error))
            skipped += 1
            continue
        if not insert_package_element(
            store, spec.target_file, spec.package_path, fragment, f"{label} {spec.path}"
        ):
            skipped += 1
            continue
        created.append(spec.path)
        LOGGER.info("create %s %s with %s in %s", label, spec.path, created_detail, spec.target_file)
    return created, skipped


def apply_client_server_artifacts(
    backend: ArxmlBackend,
    ports: Sequence[ResolvedPort],
    arxml_files: Sequence[Path],
    store: SourceStore,
) -> tuple[int, int]:
    """为标记为 Y 的 CS 端口创建全部 operation 对应的内部行为对象。"""

    requested = [
        port
        for port in ports
        if port.spec.create_rte_artifact and port.interface_dest == CS_INTERFACE_DEST
    ]
    operations_by_interface: dict[str, list[str]] = {}
    for interface_path in sorted({port.spec.interface_path for port in requested}):
        summary = interface_summary(backend, arxml_files, interface_path, store)
        operations_by_interface[interface_path] = [] if summary is None else list(summary.operations)

    by_file: dict[Path, list[ResolvedPort]] = defaultdict(list)
    for port in requested:
        by_file[port.component_file].append(port)

    created = 0
    skipped = 0
    for component_file, component_ports in by_file.items():
        document = store.load(backend, component_file)
        source = store.read(component_file)
        call_fragments: dict[str, list[str]] = defaultdict(list)
        event_fragments: dict[str, list[str]] = defaultdict(list)
        runnable_fragments: dict[str, list[str]] = defaultdict(list)

        for port in component_ports:
            operations = operations_by_interface.get(port.spec.interface_path, [])
            if not operations:
                LOGGER.error("Skipped CS artifacts for %s: interface has no operations", port.port_path)
                skipped += 1
                continue
            behavior_path = document.internal_behavior_path(port.component_path)
            if behavior_path is None:
                LOGGER.error(
                    "Skipped CS artifacts for %s: component has no internal behavior", port.port_path
                )
                skipped += 1
                continue

            for operation in operations:
                operation_path = f"{port.spec.interface_path}/{operation}"
                if port.spec.direction == "R":
                    runnable_path = document.find_runnable(port.component_path, port.spec.runnable_ref)
                    if runnable_path is None:
                        LOGGER.error(
                            "Skipped CS call for %s: runnable %s not found",
                            port.port_path,
                            port.spec.runnable_ref,
                        )
                        skipped += 1
                        continue
                    name = f"scp_{port.spec.name}_{operation}"
                    existing = document.read_server_call(runnable_path, name)
                    if document.element_exists(f"{runnable_path}/{name}"):
                        definition = None if existing is None else (
                            existing.port_path, existing.operation_path, existing.timeout
                        )
                        if not check_existing_definition(
                            f"Server call {runnable_path}/{name}", definition,
                            (port.port_path, operation_path, 0.0),
                        ):
                            skipped += 1
                        continue
                    call_fragments[runnable_path].append(
                        document.add_server_call(runnable_path, name, port.port_path, operation_path)
                    )
                    created += 1
                    continue

                runnable_name = port.spec.runnable_name or f"RE_{port.spec.name}_{operation}"
                if port.spec.runnable_name and len(operations) > 1:
                    runnable_name = f"{port.spec.runnable_name}_{operation}"
                runnable_symbol = port.spec.runnable_symbol or runnable_name
                if port.spec.runnable_symbol and len(operations) > 1:
                    runnable_symbol = f"{port.spec.runnable_symbol}_{operation}"
                runnable_path = f"{behavior_path}/{runnable_name}"
                if document.element_exists(runnable_path):
                    if not check_existing_definition(
                        f"Runnable {runnable_path}",
                        document.runnable_definition(runnable_path),
                        (runnable_symbol, port.spec.minimum_start_interval,
                         port.spec.can_be_invoked_concurrently),
                    ):
                        skipped += 1
                        continue
                else:
                    runnable_fragments[behavior_path].append(
                        document.add_runnable(
                            behavior_path,
                            runnable_name,
                            runnable_symbol,
                            port.spec.minimum_start_interval,
                            port.spec.can_be_invoked_concurrently,
                        )
                    )
                    created += 1

                event_name = f"oie_{port.spec.name}_{operation}"
                existing_event = document.read_operation_event(behavior_path, event_name)
                if document.element_exists(f"{behavior_path}/{event_name}"):
                    definition = None if existing_event is None else (
                        existing_event.port_path, existing_event.operation_path,
                        existing_event.runnable_path,
                    )
                    if not check_existing_definition(
                        f"Operation event {behavior_path}/{event_name}", definition,
                        (port.port_path, operation_path, runnable_path),
                    ):
                        skipped += 1
                    continue
                event_fragments[behavior_path].append(
                    document.add_operation_event(
                        behavior_path, event_name, runnable_path, port.port_path, operation_path
                    )
                )
                created += 1

        for runnable_path, fragments in call_fragments.items():
            source = xml_patch.insert_or_create_child_container(
                source, runnable_path, "SERVER-CALL-POINTS", fragments, ("SYMBOL",)
            )
        for behavior_path, fragments in event_fragments.items():
            source = xml_patch.insert_or_create_child_container(
                source, behavior_path, "EVENTS", fragments, ("EXCLUSIVE-AREAS", "RUNNABLES")
            )
        for behavior_path, fragments in runnable_fragments.items():
            source = xml_patch.insert_or_create_child_container(
                source, behavior_path, "RUNNABLES", fragments, ("SERVICE-DEPENDENCIES",)
            )
        if call_fragments or event_fragments or runnable_fragments:
            LOGGER.info("Re-parsing ARXML for validation: %s", component_file)
            load_bytes(backend, source)
            store.write(component_file, source)
    return created, skipped


def container_ready(source: bytes, element_path: str, container_tag: str) -> bool:
    """确认元素及其直接子容器均可按路径定位；否则记录 error。"""

    problem = xml_patch.container_problem(source, element_path, container_tag)
    if problem is not None:
        LOGGER.error("%s", problem)
        return False
    return True


def validate_document(
    backend: ArxmlBackend,
    document: bytes,
    expected_ports: Sequence[ResolvedPort],
    expected_accesses: Sequence[ResolvedAccess],
) -> None:
    """把修改后的字节重新解析，确认后端能完整读回已处理的内容。"""

    check_document, diagnostics = load_bytes(backend, document)
    for diagnostic in diagnostics:
        LOGGER.warning("%s validation: %s", backend.name, diagnostic)
    for port in expected_ports:
        check_port(check_document.read_port(port.component_path, port.spec.name), port)
    for access in expected_accesses:
        check_access(check_document.read_access(access.runnable_path, access.name, access.kind), access)


def build_changes(
    backend: ArxmlBackend,
    resolved_ports: Sequence[ResolvedPort],
    resolved_accesses: Sequence[ResolvedAccess],
    store: SourceStore,
) -> tuple[list[FileChange], int]:
    """按目标文件分组应用端口和访问点请求，并验证生成结果。

    已存在且内容一致的端口不会重复创建；单条端口或访问点失败只记录 error 并跳过，
    不影响同一文件中的其他请求。返回变更列表和被跳过的端口数量。
    """
    ports_by_file: dict[Path, list[ResolvedPort]] = defaultdict(list)
    for port in resolved_ports:
        ports_by_file[port.component_file].append(port)
    accesses_by_file: dict[Path, list[ResolvedAccess]] = defaultdict(list)
    for access in resolved_accesses:
        accesses_by_file[access.component_file].append(access)

    changes: list[FileChange] = []
    skipped = 0
    for source_file in list(ports_by_file) + [path for path in accesses_by_file if path not in ports_by_file]:
        document = store.load(backend, source_file)
        source = store.read(source_file)
        fragments_by_component: dict[str, list[str]] = defaultdict(list)
        fragments_by_runnable: dict[tuple[str, str], list[str]] = defaultdict(list)
        created_ports: list[ResolvedPort] = []
        existing_ports: list[ResolvedPort] = []
        created_accesses: list[str] = []
        checked_ports: list[ResolvedPort] = []
        checked_accesses: list[ResolvedAccess] = []
        for port in ports_by_file.get(source_file, []):
            try:
                existing = document.read_port(port.component_path, port.spec.name)
                if existing is not None:
                    check_port(existing, port)
                    LOGGER.warning(
                        "Port %s already exists with the same definition (including ComSpec); skipped",
                        port.port_path,
                    )
                    existing_ports.append(port)
                    continue
                if not container_ready(source, port.component_path, "PORTS"):
                    skipped += 1
                    continue
                fragment = document.add_port(
                    port.component_path,
                    port.spec.name,
                    port.spec.direction,
                    port.spec.interface_path,
                    port.interface_dest,
                    port.update_elements,
                    port.queued_elements,
                    port.sender_elements,
                )
                check_port(document.read_port(port.component_path, port.spec.name), port)
            except ValueError as error:
                LOGGER.error("Skipped port %s: %s", port.port_path, clean_message(error))
                skipped += 1
                continue
            fragments_by_component[port.component_path].append(fragment)
            created_ports.append(port)
            checked_ports.append(port)

        for access in accesses_by_file.get(source_file, []):
            container = access.container
            try:
                existing_access = document.read_access(access.runnable_path, access.name, access.kind)
                if existing_access is not None:
                    check_access(existing_access, access)
                    LOGGER.warning(
                        "Data access %s already exists with the same definition; skipped",
                        access.access_path,
                    )
                    checked_accesses.append(access)
                    continue
                if document.element_exists(access.access_path):
                    LOGGER.error(
                        "Data access %s already exists as a different kind of object; "
                        "please choose a different access name",
                        access.access_path,
                    )
                    continue
                if not container_ready(source, access.runnable_path, container):
                    continue
                access_fragment = document.add_access(
                    access.runnable_path, access.name, access.kind, access.port_path, access.data_path
                )
                check_access(document.read_access(access.runnable_path, access.name, access.kind), access)
            except ValueError as error:
                LOGGER.error("Skipped data access %s: %s", access.access_path, clean_message(error))
                continue
            fragments_by_runnable[(access.runnable_path, container)].append(access_fragment)
            created_accesses.append(access.access_path)
            checked_accesses.append(access)
            LOGGER.info("create %s %s -> %s", container, access.access_path, access.data_path)

        source = xml_patch.insert_many(
            source,
            [
                (xml_patch.container_end(source, component_path, "PORTS"), fragments)
                for component_path, fragments in fragments_by_component.items()
            ]
            + [
                (xml_patch.container_end(source, runnable_path, container), fragments)
                for (runnable_path, container), fragments in fragments_by_runnable.items()
            ],
        )
        LOGGER.info("Re-parsing ARXML for validation: %s", source_file)
        validate_document(backend, source, checked_ports, checked_accesses)
        store.write(source_file, source)
        changes.append(FileChange(source_file, source, created_ports, existing_ports, created_accesses))
    return changes, skipped
