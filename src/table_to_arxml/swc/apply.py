"""生成新元素的 XML 片段，按 AUTOSAR 路径插入原始字节，并重新解析验证结果。"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable, Collection, Iterable, Mapping, Sequence, Union

from . import xml_patch
from ..common.messages import clean_message
from ..common.names import generated_name
from .arxml_model import AccessInfo, ArxmlDocument, ImplementationTypeInfo, PortInfo
from .constants import CLIENT_SERVER_INTERFACE, DIRECTION_REQUIRED, SENDER_RECEIVER_INTERFACE
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

IMPLEMENTATION_DATA_TYPE = "IMPLEMENTATION-DATA-TYPE"
# ``IMPLEMENTATION-DATA-TYPE-REF`` 只能引用实现数据类型；数据原型的 ``TYPE-TREF`` 可引用
# AUTOSAR-DATA-TYPE 的全部具体子类（AUTOSAR_00044.xsd 的 AUTOSAR-DATA-TYPE--SUBTYPES-ENUM）。
IMPLEMENTATION_TYPE_DESTS = (IMPLEMENTATION_DATA_TYPE,)
AUTOSAR_DATA_TYPE_DESTS = (
    "APPLICATION-ARRAY-DATA-TYPE",
    "APPLICATION-ASSOC-MAP-DATA-TYPE",
    "APPLICATION-PRIMITIVE-DATA-TYPE",
    "APPLICATION-RECORD-DATA-TYPE",
    IMPLEMENTATION_DATA_TYPE,
)


@dataclass
class StepResult:
    """一个处理步骤的结果：已在内存中准备的新增对象路径和失败的请求数。"""

    planned: list[str] = field(default_factory=list)
    skipped: int = 0
    skipped_accesses: int = 0


@dataclass
class FileChange:
    """一个 ARXML 的修改结果，``document`` 为待写出的完整字节。"""

    source_file: Path
    document: bytes
    planned_ports: list[ResolvedPort]
    existing_ports: list[ResolvedPort]
    planned_accesses: list[str]


@dataclass(frozen=True)
class _DefinitionOperation:
    path: str
    label: str
    read: Callable[[ArxmlDocument, str], object]
    create: Callable[[ArxmlDocument, str, str], str]
    expected: object


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


def number_text(text: str) -> str:
    """把数值文本规范化以便比较（``1.0`` 与 ``1`` 相同）；``INF`` 等非有限值和非数值保持原文。"""

    try:
        number = Decimal(text.strip())
    except InvalidOperation:
        return text
    if not number.is_finite():
        return text
    return "0" if number.is_zero() else format(number.normalize(), "f")


def _normalized_scales(definition: object) -> object:
    if not isinstance(definition, tuple) or len(definition) != 2:
        return definition
    category, scales = definition
    return category, tuple((number_text(lower), number_text(upper), label) for lower, upper, label in scales)


def _normalized_limits(definition: object) -> object:
    if not isinstance(definition, tuple):
        return definition
    return tuple((number_text(lower), number_text(upper)) for lower, upper in definition)


def case_conflict(store: SourceStore, element_path: str, context: str) -> bool:
    """新对象与已有对象的 SHORT-NAME 仅大小写不同时记录 ERROR（constr_2508）；返回是否冲突。"""

    variants = store.case_variants(element_path)
    if variants:
        LOGGER.error(
            "Skipped %s: %s differs only in case from existing %s; SHORT-NAMEs must be unique "
            "case-insensitively",
            context, element_path, ", ".join(f"{path} in {source}" for source, path in variants),
        )
        return True
    return False


def name_case_conflict(names: Iterable[str], name: str) -> str | None:
    """返回同一命名空间中与 ``name`` 仅大小写不同的名称。"""

    return next((other for other in names if other != name and other.casefold() == name.casefold()), None)


def resolve_type_reference(
    store: SourceStore,
    type_path: str,
    allowed: Collection[str],
    planned_types: Mapping[str, str],
    context: str,
    column: str,
) -> str | None:
    """确定类型引用的 ``DEST``：按清单和目标文件查找被引用对象的元类（含本次已插入的对象）。

    元类不在 ``allowed`` 中时记录 ERROR 并返回 ``None``；找不到时记录 WARNING，按实现数据类型写出。
    """
    found = store.element_tag(type_path)
    if found is not None:
        source, tag = found
        if tag not in allowed:
            LOGGER.error(
                "Skipped %s: %s %s refers to %s in %s, expected %s",
                context, column, type_path, tag, source, " or ".join(allowed),
            )
            return None
        return tag
    if type_path in planned_types:
        return planned_types[type_path]
    variants = store.case_variants(type_path)
    hint = f" (case variants: {', '.join(path for _, path in variants)})" if variants else ""
    LOGGER.warning(
        "%s: %s %s not found in loaded ARXML%s; reference written unresolved with DEST=%s",
        context, column, type_path, hint, IMPLEMENTATION_DATA_TYPE,
    )
    return IMPLEMENTATION_DATA_TYPE


def _type_dests(
    store: SourceStore,
    references: Iterable[tuple[str, str]],
    allowed: Collection[str],
    planned_types: Mapping[str, str],
    context: str,
) -> dict[str, str] | None:
    """逐个解析 ``(类型路径, 列名)``；任一引用元类不符时返回 ``None``。"""

    dests: dict[str, str] = {}
    failed = False
    for type_path, column in references:
        if type_path in dests:
            continue
        dest = resolve_type_reference(store, type_path, allowed, planned_types, context, column)
        if dest is None:
            failed = True
        else:
            dests[type_path] = dest
    return None if failed else dests


def check_port(existing: PortInfo | None, port: ResolvedPort) -> None:
    """校验端口方向、接口引用和工作簿显式填写的 ComSpec 字段。

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
    provided = port.spec.provided_fields
    comparisons: list[tuple[str, object, object]] = []
    if "Enable Update" in provided:
        expected = port.update_elements if port.spec.enable_update else ()
        comparisons.append(("Enable Update elements", existing.update_elements, expected))
    if "Queue Length" in provided:
        expected_queue = port.queued_elements if port.spec.queue_length > 0 else ()
        comparisons.append(("QueueLength elements", existing.queued_elements, expected_queue))
    if "Transmission Acknowledge Timeout" in provided:
        comparisons.append((
            "Transmission Acknowledge Timeout",
            tuple((path, timeout) for path, timeout, _ in existing.sender_elements),
            tuple((path, timeout) for path, timeout, _ in port.sender_elements),
        ))
    if "Uses End To End Protection" in provided:
        actual_protection = tuple(
            (path, protection) for path, _, protection in existing.sender_elements
        )
        if port.spec.uses_protection:
            expected_protection = tuple(
                (path, protection) for path, _, protection in port.sender_elements
            )
            comparisons.append((
                "Uses End To End Protection",
                actual_protection,
                expected_protection,
            ))
        elif any(protection for _, protection in actual_protection):
            comparisons.append((
                "Uses End To End Protection",
                actual_protection,
                tuple((path, False) for path, _ in actual_protection),
            ))
    for label, actual, expected in comparisons:
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

    problem = store.encoding_problem(target_file)
    if problem is not None:
        LOGGER.error("Skipped %s: %s", context, problem)
        return False
    source = store.read(target_file)
    try:
        updated = xml_patch.insert_or_create_child_container(
            source, package_path, "ELEMENTS", [fragment]
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
    enumerations: Sequence[EnumerationSpec],
    store: SourceStore,
    planned_types: Mapping[str, str] | None = None,
) -> StepResult:
    """创建尚不存在的枚举类型，并把结果字节写入缓存。

    每个枚举生成三个元素：TEXTTABLE 计算方法、引用它的实现数据类型和限定取值
    范围的数据约束。三者分别比较自身定义，不递归展开引用；相同则 warning，
    不同则 error 并提示改名，已有内容不会被覆盖。上下限按数值比较（``1.0`` 等于 ``1``）。
    ``planned_types`` 为本次工作簿新建的类型路径及其元类，用于解析尚未插入的类型引用。
    """
    planned_types = planned_types or {}
    result = StepResult()
    for spec in enumerations:
        lower, upper = spec.limits
        elements = (
            _DefinitionOperation(
                spec.compu_method_path,
                "COMPU-METHOD",
                lambda document, path: _normalized_scales(document.compu_method_definition(path)),
                lambda document, package, name, spec=spec: document.add_compu_method(package, name, spec.values),
                _normalized_scales(("TEXTTABLE", tuple((value, value, label) for value, label in spec.values))),
            ),
            _DefinitionOperation(
                spec.path,
                "IMPLEMENTATION-DATA-TYPE",
                lambda document, path: document.implementation_type_definition(path),
                lambda document, package, name, spec=spec: document.add_enum_data_type(
                    package, name, spec.base_type, spec.compu_method_path, spec.data_constr_path,
                ),
                ImplementationTypeInfo(
                    "TYPE_REFERENCE", spec.base_type, spec.compu_method_path,
                    spec.data_constr_path,
                ),
            ),
            _DefinitionOperation(
                spec.data_constr_path,
                "DATA-CONSTR",
                lambda document, path: _normalized_limits(document.data_constr_definition(path)),
                lambda document, package, name, lower=lower, upper=upper: document.add_data_constr(
                    package, name, lower, upper,
                ),
                _normalized_limits(((lower, upper),)),
            ),
        )

        failed = False
        inserted = 0
        for operation in elements:
            element_path, label = operation.path, operation.label
            existing_file = store.find_first_definition_file(element_path)
            if existing_file is not None:
                existing_document = store.load(existing_file)
                if not check_existing_definition(
                    f"{label} {element_path} in {existing_file}",
                    operation.read(existing_document, element_path),
                    operation.expected,
                ):
                    failed = True
                continue
            context = f"enumeration {spec.path} ({label})"
            if case_conflict(store, element_path, context):
                failed = True
                break
            if label == "IMPLEMENTATION-DATA-TYPE" and resolve_type_reference(
                store, spec.base_type, IMPLEMENTATION_TYPE_DESTS, planned_types, context,
                "Implementation Data Type Ref",
            ) is None:
                failed = True
                break
            document = store.load(spec.target_file)
            package_path, _, name = element_path.rpartition("/")
            if not document.package_exists(package_path):
                LOGGER.error(
                    "Skipped %s: package %s not found in %s", context, package_path, spec.target_file
                )
                failed = True
                break
            try:
                fragment = operation.create(document, package_path, name)
            except ValueError as error:
                LOGGER.error("Skipped %s: %s", context, clean_message(error))
                failed = True
                break
            if not insert_package_element(store, spec.target_file, package_path, fragment, context):
                failed = True
                break
            inserted += 1

        if failed:
            result.skipped += 1
            continue
        if not inserted:
            continue

        result.planned.append(spec.path)
        LOGGER.info(
            "Prepared enumeration %s with %d value(s) in memory for %s",
            spec.path,
            len(spec.values),
            spec.target_file,
        )
    return result


def apply_composite_types(
    arrays: Sequence[ArraySpec],
    structures: Sequence[StructureSpec],
    store: SourceStore,
    planned_types: Mapping[str, str] | None = None,
) -> StepResult:
    """创建数组和结构体实现数据类型，并将 XML 片段写入缓存。

    已有数组只在工作簿填写了 ``Array Size Semantics`` 时比较该属性。
    """

    planned_types = planned_types or {}
    result = StepResult()
    seen_paths: set[str] = set()
    for spec in [*arrays, *structures]:
        kind = "array" if isinstance(spec, ArraySpec) else "structure"
        if spec.path in seen_paths:
            LOGGER.error("Skipped duplicate composite Type Path: %s", spec.path)
            result.skipped += 1
            continue
        seen_paths.add(spec.path)
        existing_file = store.find_first_definition_file(spec.path)
        if existing_file is not None:
            document = store.load(existing_file)
            existing = document.implementation_type_definition(spec.path)
            if isinstance(spec, ArraySpec):
                semantics = spec.array_size_semantics
                if not semantics:
                    semantics = (
                        existing.members[0][4] if existing is not None and len(existing.members) == 1
                        else spec.effective_semantics
                    )
                expected = ImplementationTypeInfo("ARRAY", members=((
                    spec.element_name, "TYPE_REFERENCE", spec.element_type_ref,
                    str(spec.array_size), semantics,
                ),))
            else:
                expected = ImplementationTypeInfo("STRUCTURE", members=tuple(
                    (name, "TYPE_REFERENCE", type_ref, "", "")
                    for name, type_ref in spec.members
                ))
            if not check_existing_definition(f"{kind} {spec.path} in {existing_file}", existing, expected):
                result.skipped += 1
            continue
        context = f"{kind} {spec.path}"
        if case_conflict(store, spec.path, context):
            result.skipped += 1
            continue
        references = (
            [(spec.element_type_ref, "Element Type Ref")] if isinstance(spec, ArraySpec)
            else [(type_ref, "Member Type Ref") for _, type_ref in spec.members]
        )
        if _type_dests(store, references, IMPLEMENTATION_TYPE_DESTS, planned_types, context) is None:
            result.skipped += 1
            continue
        document = store.load(spec.target_file)
        if not document.package_exists(spec.package_path):
            LOGGER.error(
                "Skipped %s %s: package %s not found in %s",
                kind,
                spec.path,
                spec.package_path,
                spec.target_file,
            )
            result.skipped += 1
            continue
        try:
            if isinstance(spec, ArraySpec):
                fragment = document.add_array_data_type(
                    spec.package_path,
                    spec.name,
                    spec.element_name,
                    spec.element_type_ref,
                    spec.array_size,
                    spec.effective_semantics,
                )
            else:
                fragment = document.add_structure_data_type(spec.package_path, spec.name, spec.members)
        except ValueError as error:
            LOGGER.error("Skipped %s %s: %s", kind, spec.path, clean_message(error))
            result.skipped += 1
            continue
        if not insert_package_element(
            store, spec.target_file, spec.package_path, fragment, f"{kind} {spec.path}"
        ):
            result.skipped += 1
            continue
        result.planned.append(spec.path)
        LOGGER.info("Prepared %s %s in memory for %s", kind, spec.path, spec.target_file)
    return result


def apply_interfaces(
    interfaces: Sequence[Union[InterfaceSpec, ClientServerInterfaceSpec]],
    store: SourceStore,
    planned_types: Mapping[str, str] | None = None,
) -> StepResult:
    """创建尚不存在的 SR/CS 接口，并把结果字节写入缓存。

    已存在时比较接口类型、数据元素（或 operation 与参数）和服务标志，不修改已有定义；
    任何不一致都记录 error 并跳过该接口，由主流程阻止全部 ARXML 写盘。
    新建时 ``TYPE-TREF`` 的 ``DEST`` 取被引用类型的实际元类。返回新建接口路径列表和被跳过的数量。
    """
    planned_types = planned_types or {}
    result = StepResult()
    for spec in interfaces:
        if isinstance(spec, InterfaceSpec):
            label, dest = "SR interface", SENDER_RECEIVER_INTERFACE
            expected: object = list(spec.data_elements)
            created_detail = f"{len(spec.data_elements)} data element(s)"
        else:
            label, dest = "CS interface", CLIENT_SERVER_INTERFACE
            expected = [(operation, list(arguments)) for operation, arguments in spec.operations]
            created_detail = f"{len(spec.operations)} operation(s)"

        existing_file = store.find_first_definition_file(spec.path)
        if existing_file is not None:
            document = store.load(existing_file)
            if not check_existing_definition(
                f"{label} {spec.path} in {existing_file}",
                (
                    document.interface_dest(spec.path),
                    document.sender_receiver_definition(spec.path) if isinstance(spec, InterfaceSpec)
                    else document.client_server_definition(spec.path),
                    document.interface_is_service(spec.path),
                ),
                (dest, expected, spec.is_service),
            ):
                result.skipped += 1
            continue
        context = f"{label} {spec.path}"
        if case_conflict(store, spec.path, context):
            result.skipped += 1
            continue
        references = (
            [(type_ref, "Data Type Ref") for _, type_ref, _ in spec.data_elements]
            if isinstance(spec, InterfaceSpec)
            else [(type_ref, "Data Type Ref") for _, arguments in spec.operations for _, type_ref, _ in arguments]
        )
        type_dests = _type_dests(store, references, AUTOSAR_DATA_TYPE_DESTS, planned_types, context)
        if type_dests is None:
            result.skipped += 1
            continue
        document = store.load(spec.target_file)
        if not document.package_exists(spec.package_path):
            LOGGER.error(
                "Skipped %s %s: package %s not found in %s",
                label,
                spec.path,
                spec.package_path,
                spec.target_file,
            )
            result.skipped += 1
            continue
        try:
            if isinstance(spec, InterfaceSpec):
                fragment = document.add_sr_interface(
                    spec.package_path, spec.name, spec.data_elements, spec.is_service, type_dests,
                )
            else:
                fragment = document.add_cs_interface(
                    spec.package_path, spec.name, spec.operations, spec.is_service, type_dests,
                )
        except ValueError as error:
            LOGGER.error("Skipped %s %s: %s", label, spec.path, clean_message(error))
            result.skipped += 1
            continue
        if not insert_package_element(
            store, spec.target_file, spec.package_path, fragment, f"{label} {spec.path}"
        ):
            result.skipped += 1
            continue
        result.planned.append(spec.path)
        LOGGER.info("Prepared %s %s with %s in memory for %s", label, spec.path, created_detail, spec.target_file)
    return result


def apply_client_server_artifacts(
    ports: Sequence[ResolvedPort],
    arxml_files: Sequence[Path],
    store: SourceStore,
) -> StepResult:
    """为标记为 Y 的 CS 端口创建全部 operation 对应的内部行为对象。

    ``ports`` 只应包含已成功准备或已存在的端口。每个文件先插入 P-Port 的 runnable，再插入
    operation 事件，重新加载后才创建 R-Port 的服务器调用点，因此调用点可以位于本次新建的 runnable 中。
    单个 operation 或插入组失败只记录 error 并跳过，不中断其他对象。
    """

    requested = [
        port
        for port in ports
        if port.spec.create_rte_artifact and port.interface_dest == CLIENT_SERVER_INTERFACE
    ]
    operations_by_interface: dict[str, list[str]] = {}
    for interface_path in sorted({port.spec.interface_path for port in requested}):
        summary = interface_summary(arxml_files, interface_path, store)
        operations_by_interface[interface_path] = [] if summary is None else list(summary.operations)

    by_file: dict[Path, list[ResolvedPort]] = defaultdict(list)
    for port in requested:
        by_file[port.component_file].append(port)

    result = StepResult()
    for component_file, component_ports in by_file.items():
        problem = store.encoding_problem(component_file)
        if problem is not None:
            LOGGER.error("Skipped CS artifacts in %s", problem)
            result.skipped += len(component_ports)
            continue
        valid_ports = []
        for port in component_ports:
            if not operations_by_interface.get(port.spec.interface_path):
                LOGGER.error("Skipped CS artifacts for %s: interface has no operations", port.port_path)
                result.skipped += 1
            else:
                valid_ports.append(port)
        providers = [port for port in valid_ports if port.spec.direction != DIRECTION_REQUIRED]
        requirers = [port for port in valid_ports if port.spec.direction == DIRECTION_REQUIRED]
        if providers:
            _apply_server_runnables(component_file, providers, operations_by_interface, store, result)
        if requirers:
            _apply_server_calls(component_file, requirers, operations_by_interface, store, result)
    return result


def _insert_groups(
    source: bytes,
    groups: Mapping[str, Sequence[tuple[str, str]]],
    container_tag: str,
    result: StepResult,
) -> bytes:
    """把 ``父路径 -> [(新对象路径, 片段)]`` 插入各父元素的容器；失败的组记录 error 并撤销其计划。"""

    for parent_path, items in groups.items():
        try:
            source = xml_patch.insert_or_create_child_container(
                source, parent_path, container_tag, [fragment for _, fragment in items]
            )
        except ValueError as error:
            LOGGER.error("Skipped %s in %s: %s", container_tag, parent_path, clean_message(error))
            result.skipped += len(items)
            for object_path, _ in items:
                if object_path in result.planned:
                    result.planned.remove(object_path)
    return source


def _validate_cs_source(component_file: Path, source: bytes, store: SourceStore) -> None:
    LOGGER.info("Re-parsing ARXML for validation: %s", component_file)
    _document, diagnostics = load_bytes(source)
    for diagnostic in diagnostics:
        LOGGER.warning("armodel validation: %s", diagnostic)
    store.write(component_file, source)


def _apply_server_runnables(
    component_file: Path,
    ports: Sequence[ResolvedPort],
    operations_by_interface: Mapping[str, list[str]],
    store: SourceStore,
    result: StepResult,
) -> None:
    """为 CS P-Port 的每个 operation 创建（或复用）runnable 和 operation 事件。"""

    document = store.load(component_file)
    source = store.read(component_file)
    runnable_groups: dict[str, list[tuple[str, str]]] = defaultdict(list)
    event_groups: dict[str, list[tuple[str, str]]] = defaultdict(list)
    # 本次计划的 runnable：casefold 路径 -> (路径, SYMBOL/最小启动间隔/并发定义)。
    planned_runnables: dict[str, tuple[str, tuple[str, float, bool]]] = {}
    symbols: dict[str, dict[str, str]] = {}

    for port in ports:
        try:
            behavior_path = document.internal_behavior_path(port.component_path)
            if behavior_path is not None and behavior_path not in symbols:
                symbols[behavior_path] = document.runnable_symbols(behavior_path)
        except ValueError as error:
            LOGGER.error("Skipped CS artifacts for %s: %s", port.port_path, clean_message(error))
            result.skipped += 1
            continue
        if behavior_path is None:
            LOGGER.error("Skipped CS artifacts for %s: component has no internal behavior", port.port_path)
            result.skipped += 1
            continue
        operations = operations_by_interface[port.spec.interface_path]
        for operation in operations:
            try:
                if not _plan_server_runnable(
                    document, source, port, operation, len(operations), behavior_path,
                    planned_runnables, symbols[behavior_path], runnable_groups, event_groups, result,
                ):
                    result.skipped += 1
            except ValueError as error:
                LOGGER.error(
                    "Skipped CS artifacts for %s operation %s: %s", port.port_path, operation, clean_message(error)
                )
                result.skipped += 1

    if not runnable_groups and not event_groups:
        return
    source = _insert_groups(source, runnable_groups, "RUNNABLES", result)
    source = _insert_groups(source, event_groups, "EVENTS", result)
    _validate_cs_source(component_file, source, store)


def _plan_server_runnable(
    document: ArxmlDocument,
    source: bytes,
    port: ResolvedPort,
    operation: str,
    operation_count: int,
    behavior_path: str,
    planned_runnables: dict[str, tuple[str, tuple[str, float, bool]]],
    symbols: dict[str, str],
    runnable_groups: dict[str, list[tuple[str, str]]],
    event_groups: dict[str, list[tuple[str, str]]],
    result: StepResult,
) -> bool:
    """规划一个 operation 的 runnable 和事件；失败时记录 error 并返回 ``False``。"""

    operation_path = f"{port.spec.interface_path}/{operation}"
    runnable_name = port.spec.runnable_name or f"RE_{port.spec.name}_{operation}"
    if port.spec.runnable_name and operation_count > 1:
        runnable_name = f"{port.spec.runnable_name}_{operation}"
    runnable_name = generated_name(runnable_name)
    runnable_symbol = port.spec.runnable_symbol or runnable_name
    if port.spec.runnable_symbol and operation_count > 1:
        runnable_symbol = f"{port.spec.runnable_symbol}_{operation}"
    runnable_path = f"{behavior_path}/{runnable_name}"
    definition = (runnable_symbol, port.spec.minimum_start_interval, port.spec.can_be_invoked_concurrently)
    behavior_names = xml_patch.child_names(source, behavior_path)

    planned = planned_runnables.get(runnable_path.casefold())
    if planned is not None:
        planned_path, planned_definition = planned
        if planned_path != runnable_path:
            LOGGER.error(
                "Skipped CS artifacts for %s: runnable %s differs only in case from %s planned in this run",
                port.port_path, runnable_path, planned_path,
            )
            return False
        if planned_definition != definition:
            LOGGER.error(
                "Runnable %s is already planned in this run with a different definition: planned=%s, "
                "requested=%s; please choose a different Runnable Name",
                runnable_path, planned_definition, definition,
            )
            return False
        LOGGER.info("Runnable %s is already planned in this run with the same definition; reused", runnable_path)
    elif document.element_exists(runnable_path):
        if not check_existing_definition(
            f"Runnable {runnable_path}", document.runnable_definition(runnable_path), definition,
        ):
            return False
    else:
        conflict = name_case_conflict(behavior_names, runnable_name)
        if conflict is not None:
            LOGGER.error(
                "Skipped CS artifacts for %s: runnable %s differs only in case from existing %s/%s",
                port.port_path, runnable_path, behavior_path, conflict,
            )
            return False
        duplicate_symbol = next((name for name, symbol in symbols.items() if symbol == runnable_symbol), None)
        if duplicate_symbol is not None:
            LOGGER.warning(
                "Runnable %s uses SYMBOL %s, which is already used by runnable %s/%s",
                runnable_path, runnable_symbol, behavior_path, duplicate_symbol,
            )
        runnable_groups[behavior_path].append((runnable_path, document.add_runnable(
            behavior_path,
            runnable_name,
            runnable_symbol,
            port.spec.minimum_start_interval,
            port.spec.can_be_invoked_concurrently,
        )))
        planned_runnables[runnable_path.casefold()] = (runnable_path, definition)
        symbols[runnable_name] = runnable_symbol
        result.planned.append(runnable_path)

    event_name = generated_name(f"oie_{port.spec.name}_{operation}")
    event_path = f"{behavior_path}/{event_name}"
    expected_event = (port.port_path, operation_path, runnable_path)
    if document.element_exists(event_path):
        existing_event = document.read_operation_event(behavior_path, event_name)
        definition_event = None if existing_event is None else (
            existing_event.port_path, existing_event.operation_path, existing_event.runnable_path,
        )
        return check_existing_definition(f"Operation event {event_path}", definition_event, expected_event)
    planned_names = [path.rpartition("/")[2] for path, _ in (*runnable_groups[behavior_path], *event_groups[behavior_path])]
    conflict = name_case_conflict([*behavior_names, *planned_names], event_name)
    if conflict is not None:
        LOGGER.error(
            "Skipped CS artifacts for %s: operation event %s differs only in case from %s",
            port.port_path, event_path, conflict,
        )
        return False
    event_groups[behavior_path].append((event_path, document.add_operation_event(
        behavior_path, event_name, runnable_path, port.port_path, operation_path
    )))
    result.planned.append(event_path)
    return True


def _apply_server_calls(
    component_file: Path,
    ports: Sequence[ResolvedPort],
    operations_by_interface: Mapping[str, list[str]],
    store: SourceStore,
    result: StepResult,
) -> None:
    """为 CS R-Port 的每个 operation 在指定 runnable 中创建同步服务器调用点。"""

    document = store.load(component_file)
    source = store.read(component_file)
    call_groups: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for port in ports:
        for operation in operations_by_interface[port.spec.interface_path]:
            operation_path = f"{port.spec.interface_path}/{operation}"
            try:
                runnable_path = document.find_runnable(port.component_path, port.spec.runnable_name)
                if runnable_path is None:
                    LOGGER.error(
                        "Skipped CS call for %s: runnable %s not found",
                        port.port_path,
                        port.spec.runnable_name,
                    )
                    result.skipped += 1
                    continue
                name = generated_name(f"scp_{port.spec.name}_{operation}")
                call_path = f"{runnable_path}/{name}"
                if document.element_exists(call_path):
                    existing = document.read_server_call(runnable_path, name)
                    definition = None if existing is None else (
                        existing.port_path, existing.operation_path, existing.timeout
                    )
                    if not check_existing_definition(
                        f"Server call {call_path}", definition, (port.port_path, operation_path, 0.0),
                    ):
                        result.skipped += 1
                    continue
                conflict = name_case_conflict(
                    [*xml_patch.child_names(source, runnable_path), *(
                        path.rpartition("/")[2] for path, _ in call_groups.get(runnable_path, ())
                    )],
                    name,
                )
                if conflict is not None:
                    LOGGER.error(
                        "Skipped CS call for %s: server call point %s differs only in case from %s/%s",
                        port.port_path, call_path, runnable_path, conflict,
                    )
                    result.skipped += 1
                    continue
                call_groups[runnable_path].append(
                    (call_path, document.add_server_call(runnable_path, name, port.port_path, operation_path))
                )
                result.planned.append(call_path)
            except ValueError as error:
                LOGGER.error(
                    "Skipped CS call for %s operation %s: %s", port.port_path, operation, clean_message(error)
                )
                result.skipped += 1

    if call_groups:
        _validate_cs_source(
            component_file, _insert_groups(source, call_groups, "SERVER-CALL-POINTS", result), store
        )


def container_ready(source: bytes, element_path: str, container_tag: str) -> bool:
    """确认元素及其直接子容器均可按路径定位；否则记录 error。"""

    problem = xml_patch.container_problem(source, element_path, container_tag)
    if problem is not None:
        LOGGER.error("%s", problem)
        return False
    return True


def validate_document(
    document: bytes,
    expected_ports: Sequence[ResolvedPort],
    expected_accesses: Sequence[ResolvedAccess],
) -> None:
    """把修改后的字节重新解析，确认后端能完整读回已处理的内容。"""

    check_document, diagnostics = load_bytes(document)
    for diagnostic in diagnostics:
        LOGGER.warning("armodel validation: %s", diagnostic)
    for port in expected_ports:
        check_port(check_document.read_port(port.component_path, port.spec.name), port)
    for access in expected_accesses:
        check_access(check_document.read_access(access.runnable_path, access.name, access.kind), access)


def build_changes(
    resolved_ports: Sequence[ResolvedPort],
    resolved_accesses: Sequence[ResolvedAccess],
    store: SourceStore,
) -> tuple[list[FileChange], StepResult]:
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
    result = StepResult()
    for source_file in list(ports_by_file) + [path for path in accesses_by_file if path not in ports_by_file]:
        problem = store.encoding_problem(source_file)
        if problem is not None:
            LOGGER.error("Skipped ports and data accesses in %s", problem)
            result.skipped += len(ports_by_file.get(source_file, []))
            result.skipped_accesses += len(accesses_by_file.get(source_file, []))
            continue
        document = store.load(source_file)
        source = store.read(source_file)
        fragments_by_component: dict[str, list[str]] = defaultdict(list)
        access_fragments_by_container: dict[tuple[str, str], list[str]] = defaultdict(list)
        accesses_by_container: dict[tuple[str, str], list[ResolvedAccess]] = defaultdict(list)
        planned_ports: list[ResolvedPort] = []
        existing_ports: list[ResolvedPort] = []
        planned_accesses: list[str] = []
        ports_to_validate: list[ResolvedPort] = []
        accesses_to_validate: list[ResolvedAccess] = []
        for port in ports_by_file.get(source_file, []):
            try:
                existing = document.read_port(port.component_path, port.spec.name)
                if existing is not None:
                    check_port(existing, port)
                    LOGGER.warning(
                        "Port %s already exists with the same definition for explicitly provided "
                        "fields; skipped",
                        port.port_path,
                    )
                    existing_ports.append(port)
                    continue
                existing_tag = xml_patch.element_tag(source, port.port_path)
                if existing_tag is not None:
                    raise ValueError(
                        f"{port.port_path} exists as {existing_tag}, expected {port.spec.element_name}; "
                        "please choose a different port name"
                    )
                if case_conflict(store, port.port_path, f"port {port.port_path}"):
                    result.skipped += 1
                    continue
                if not container_ready(source, port.component_path, "PORTS"):
                    result.skipped += 1
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
                    port.queued_sender_elements,
                )
                check_port(document.read_port(port.component_path, port.spec.name), port)
            except ValueError as error:
                LOGGER.error("Skipped port %s: %s", port.port_path, clean_message(error))
                result.skipped += 1
                continue
            fragments_by_component[port.component_path].append(fragment)
            planned_ports.append(port)
            result.planned.append(port.port_path)
            ports_to_validate.append(port)

        for access in accesses_by_file.get(source_file, []):
            container = access.container
            try:
                existing_access = document.read_access(access.runnable_path, access.name, access.kind)
                if existing_access is not None:
                    check_access(existing_access, access)
                    if not access.reused:
                        LOGGER.warning(
                            "Data access %s already exists with the same definition; skipped",
                            access.access_path,
                        )
                    accesses_to_validate.append(access)
                    continue
                if document.element_exists(access.access_path):
                    LOGGER.error(
                        "Data access %s already exists as a different kind of object; "
                        "please choose a different access name",
                        access.access_path,
                    )
                    result.skipped_accesses += 1
                    continue
                access_fragment = document.add_access(
                    access.runnable_path, access.name, access.kind, access.port_path, access.data_path
                )
                check_access(document.read_access(access.runnable_path, access.name, access.kind), access)
            except ValueError as error:
                LOGGER.error("Skipped data access %s: %s", access.access_path, clean_message(error))
                result.skipped_accesses += 1
                continue
            access_fragments_by_container[(access.runnable_path, container)].append(access_fragment)
            accesses_by_container[(access.runnable_path, container)].append(access)

        source = xml_patch.insert_many(
            source,
            [
                (xml_patch.container_end(source, component_path, "PORTS"), fragments)
                for component_path, fragments in fragments_by_component.items()
            ],
        )
        for (runnable_path, container), fragments in access_fragments_by_container.items():
            try:
                source = xml_patch.insert_or_create_child_container(
                    source, runnable_path, container, fragments
                )
            except ValueError as error:
                LOGGER.error(
                    "Skipped data accesses in %s: %s",
                    runnable_path,
                    clean_message(error),
                )
                result.skipped_accesses += len(fragments)
                continue
            group_accesses = accesses_by_container[(runnable_path, container)]
            accesses_to_validate.extend(group_accesses)
            planned_accesses.extend(access.access_path for access in group_accesses)
        LOGGER.info("Re-parsing ARXML for validation: %s", source_file)
        validate_document(source, ports_to_validate, accesses_to_validate)
        for access_path in planned_accesses:
            LOGGER.info("Prepared data access %s in memory for %s", access_path, source_file)
        store.write(source_file, source)
        changes.append(FileChange(source_file, source, planned_ports, existing_ports, planned_accesses))
    return changes, result
