"""生成新元素的 XML 片段，按 AUTOSAR 路径插入原始字节，并重新解析验证结果。"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable, Collection, Iterable, Mapping, Sequence, Union

from . import xml_patch
from ..common.messages import clean_message
from ..common.names import generated_name
from ..common.report import CREATED, EXISTING, REUSED, Source, current_rows, each, located
from ..common.report import record as record_rows
from .arxml_model import AccessInfo, ArxmlDocument, ImplementationTypeInfo, PortInfo
from .constants import C_KEYWORDS, CLIENT_SERVER_INTERFACE, DIRECTION_REQUIRED, SENDER_RECEIVER_INTERFACE
from .resolve import ResolvedAccess, ResolvedPort, interface_summary
from .store import SourceStore, load_bytes
from .workbook import (
    ArraySpec,
    ClientServerInterfaceSpec,
    EnumerationSpec,
    InterfaceSpec,
    OperationSpec,
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


def check_existing_definition(
    context: str, existing: object, expected: object, path: str = "", file: object = "", status: str = EXISTING,
) -> bool:
    """只比较调用方提供的本层定义，不解析引用对象；一致时记入当前表格行的结果。"""

    if existing != expected:
        LOGGER.error(
            "%s already exists with a different definition: existing=%s, requested=%s; "
            "please choose a different name for the new object; existing content is unchanged",
            context,
            existing,
            expected,
        )
        return False
    LOGGER.info("%s already exists with the same definition; skipped", context)
    record_rows(current_rows(), status, path, file)
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


def warn_case_variants(
    store: SourceStore, element_path: str, context: str, batch: _PackageElementBatch | None = None,
) -> None:
    """新对象与已有对象的路径仅大小写不同时只记录 WARNING，按不同对象继续生成（AUTOSAR 规则，由 EB 校验）。

    ``batch`` 中本步骤已计划、尚未写回字节的对象也参与比较。
    """

    variants = [*store.case_variants(element_path), *(batch.case_variants(element_path) if batch else ())]
    if variants:
        LOGGER.warning(
            "%s: %s differs only in case from existing %s; created as a distinct object, "
            "left to EB validation",
            context, element_path, ", ".join(f"{path} in {source}" for source, path in variants),
        )


def warn_name_case_variants(names: Iterable[str], name: str, context: str) -> None:
    """同一命名空间中有与 ``name`` 仅大小写不同的名称时记录 WARNING。"""

    similar = sorted({other for other in names if other != name and other.casefold() == name.casefold()})
    if similar:
        LOGGER.warning(
            "%s: SHORT-NAME %s differs only in case from %s; created as a distinct object, "
            "left to EB validation",
            context, name, ", ".join(similar),
        )


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
    if port.spec.element_com_specs:
        comparisons = _element_comparisons(existing, port)
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
            shown = list(actual) if isinstance(actual, tuple) else actual
            raise ValueError(
                f"{port.port_path} conflicts with workbook: {label}={shown}; "
                "please choose a different port name"
            )


def _element_comparisons(existing: PortInfo, port: ResolvedPort) -> list[tuple[str, object, object]]:
    """``ComSpecs`` 明细只比较列出的数据元素中显式填写的列。"""

    timeouts = {path: timeout for path, timeout, _ in existing.sender_elements}
    protections = {path: protection for path, _, protection in existing.sender_elements}
    queue_lengths = dict(existing.queued_elements)
    queued_paths = {path for path, _ in port.queued_elements}
    comparisons: list[tuple[str, object, object]] = []
    for entry in port.spec.element_com_specs:
        path = f"{port.spec.interface_path}/{entry.data_element}"
        if entry.enable_update is not None:
            comparisons.append((
                f"Enable Update of {entry.data_element}", path in existing.update_elements,
                bool(entry.enable_update) and path in port.update_elements,
            ))
        if entry.queue_length is not None:
            comparisons.append((
                f"Queue Length of {entry.data_element}", queue_lengths.get(path, 0),
                entry.queue_length if path in queued_paths else 0,
            ))
        if entry.ack_timeout is not None:
            comparisons.append((
                f"Transmission Acknowledge Timeout of {entry.data_element}", timeouts.get(path), entry.ack_timeout,
            ))
        if entry.uses_protection is not None:
            comparisons.append((
                f"Uses End To End Protection of {entry.data_element}", protections.get(path, False),
                entry.uses_protection,
            ))
    return comparisons


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


@dataclass(frozen=True)
class _PendingElement:
    target_file: Path
    element_path: str
    create: Callable[[ArxmlDocument], str]
    owner: str
    context: str
    sources: tuple[Source, ...] = ()

    @property
    def package_path(self) -> str:
        return self.element_path.rpartition("/")[0]


class _PackageElementBatch:
    """收集一个步骤中要插入包 ``ELEMENTS`` 的对象；步骤结束时每个目标文件只解析一次、插入并写回一次。

    逐个写回会使下一个对象重新解析整个目标文件。对象写回前不在源字节中，同一步骤内的同路径检查
    和大小写提示另查 ``planned``；同一步骤内不会重复创建同一路径，因此片段可由同一个文档连续生成。
    同一 ``owner``（如枚举的计算方法、类型和约束）的对象全部插入或全部不插入。
    """

    def __init__(self, store: SourceStore) -> None:
        self.store = store
        self.elements: list[_PendingElement] = []
        # 本步骤计划新建的对象：完整路径 -> (目标文件, 元类, 期望定义)。
        self.planned: dict[str, tuple[Path, str, object]] = {}

    def package_ready(self, target_file: Path, package_path: str, context: str) -> bool:
        """确认目标文件可修改且包已存在；按原始字节定位，不需要 armodel 解析。"""

        problem = self.store.modification_problem(target_file)
        if problem is not None:
            LOGGER.error("Skipped %s: %s", context, problem)
            return False
        try:
            tag = xml_patch.element_tag(self.store.read(target_file), package_path)
        except ValueError as error:
            LOGGER.error(
                "Skipped %s: unable to locate package %s in %s: %s",
                context, package_path, target_file, clean_message(error),
            )
            return False
        if tag != "AR-PACKAGE":
            LOGGER.error("Skipped %s: package %s not found in %s", context, package_path, target_file)
            return False
        return True

    def add(
        self,
        target_file: Path,
        element_path: str,
        label: str,
        expected: object,
        create: Callable[[ArxmlDocument], str],
        owner: str,
        context: str,
    ) -> None:
        """登记一个新对象；``create`` 在 :meth:`flush` 时由目标文件的文档生成片段。"""

        target_file = self.store.canonical(target_file)
        self.elements.append(
            _PendingElement(target_file, element_path, create, owner, context, current_rows())
        )
        self.planned[element_path] = (target_file, label, expected)

    def case_variants(self, element_path: str) -> list[tuple[Path, str]]:
        folded = element_path.casefold()
        return [
            (target_file, path) for path, (target_file, _label, _expected) in self.planned.items()
            if path != element_path and path.casefold() == folded
        ]

    def flush(self) -> set[str]:
        """生成全部片段、插入并写回缓存；返回有对象未能生成或插入的 ``owner``。"""

        by_file: dict[Path, list[_PendingElement]] = {}
        for element in self.elements:
            by_file.setdefault(element.target_file, []).append(element)
        failed: set[str] = set()
        for target_file, elements in by_file.items():
            document = self.store.load(target_file, reuse_modified=True)
            created: list[tuple[_PendingElement, str]] = []
            for element in each(elements, lambda item: item.sources):
                if element.owner in failed:
                    continue
                try:
                    created.append((element, element.create(document)))
                except ValueError as error:
                    LOGGER.error("Skipped %s: %s", element.context, clean_message(error))
                    failed.add(element.owner)
            source = self.store.read(target_file)
            # 某个包无法定位时撤销同一 owner 的全部对象，再重新计算其他包的插入位置。
            while True:
                kept = [(element, fragment) for element, fragment in created if element.owner not in failed]
                packages: dict[str, list[tuple[_PendingElement, str]]] = {}
                for element, fragment in kept:
                    packages.setdefault(element.package_path, []).append((element, fragment))
                insertions: list[xml_patch.Insertion] = []
                retry = False
                for package_path, items in packages.items():
                    try:
                        insertions.append(xml_patch.container_insertion(
                            source, package_path, "ELEMENTS", [fragment for _element, fragment in items]
                        ))
                    except ValueError as error:
                        for element, _fragment in items:
                            LOGGER.error(
                                "Skipped %s: unable to locate package %s in %s: %s",
                                element.context, package_path, target_file, clean_message(error),
                                extra=located(element.sources),
                            )
                            failed.add(element.owner)
                        retry = True
                if not retry:
                    break
            if insertions:
                self.store.write(target_file, xml_patch.apply_insertions(source, insertions))
            for element, _fragment in kept:
                record_rows(element.sources, CREATED, element.element_path, target_file)
        self.elements.clear()
        return failed


def apply_enumerations(
    enumerations: Sequence[EnumerationSpec],
    store: SourceStore,
    planned_types: Mapping[str, str] | None = None,
) -> StepResult:
    """创建尚不存在的枚举类型，并把结果字节写入缓存。

    每个枚举生成三个元素：TEXTTABLE 计算方法、引用它的实现数据类型和限定取值
    范围的数据约束。三者分别比较自身定义，不递归展开引用；相同则记录 INFO 并复用，
    不同则 error 并提示改名，已有内容不会被覆盖。上下限按数值比较（``1.0`` 等于 ``1``）。
    三者中任一项失败时整个枚举都不写入，不会留下孤立的计算方法或约束。
    ``planned_types`` 为本次工作簿新建的类型路径及其元类，用于解析尚未插入的类型引用。
    """
    planned_types = planned_types or {}
    result = StepResult()
    batch = _PackageElementBatch(store)
    prepared: list[EnumerationSpec] = []
    for spec in each(enumerations, lambda item: item.sources):
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
        pending: list[tuple[str, str, object, Callable[[ArxmlDocument], str], str]] = []
        for operation in elements:
            element_path, label = operation.path, operation.label
            existing_file = store.find_first_definition_file(element_path)
            if existing_file is not None:
                existing_document = store.load(existing_file)
                if not check_existing_definition(
                    f"{label} {element_path} in {existing_file}",
                    operation.read(existing_document, element_path),
                    operation.expected,
                    element_path, existing_file,
                ):
                    failed = True
                continue
            if element_path in batch.planned:
                # 同一步骤中其他枚举已计划创建该对象（如共用计算方法），按已有对象比较。
                planned_file, planned_label, planned_definition = batch.planned[element_path]
                if not check_existing_definition(
                    f"{label} {element_path} in {planned_file}",
                    planned_definition if planned_label == label else None,
                    operation.expected,
                    element_path, planned_file, REUSED,
                ):
                    failed = True
                continue
            context = f"enumeration {spec.path} ({label})"
            warn_case_variants(store, element_path, context, batch)
            if label == "IMPLEMENTATION-DATA-TYPE" and resolve_type_reference(
                store, spec.base_type, IMPLEMENTATION_TYPE_DESTS, planned_types, context,
                "Implementation Data Type Ref",
            ) is None:
                failed = True
                continue
            package_path, _, name = element_path.rpartition("/")
            if not batch.package_ready(spec.target_file, package_path, context):
                failed = True
                continue
            pending.append((
                element_path, label, operation.expected,
                lambda document, operation=operation, package_path=package_path, name=name: operation.create(
                    document, package_path, name,
                ),
                context,
            ))

        # 三个对象全部检查通过后才登记，避免部分对象被写入。
        if failed:
            result.skipped += 1
            continue
        for element_path, label, expected, create, context in pending:
            batch.add(spec.target_file, element_path, label, expected, create, spec.path, context)
        if pending:
            prepared.append(spec)

    failed_owners = batch.flush()
    for spec in prepared:
        if spec.path in failed_owners:
            result.skipped += 1
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
    batch = _PackageElementBatch(store)
    prepared: list[tuple[str, Union[ArraySpec, StructureSpec]]] = []
    seen_paths: set[str] = set()
    for spec in each([*arrays, *structures], lambda item: item.sources):
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
            if not check_existing_definition(
                f"{kind} {spec.path} in {existing_file}", existing, expected, spec.path, existing_file,
            ):
                result.skipped += 1
            continue
        context = f"{kind} {spec.path}"
        warn_case_variants(store, spec.path, context, batch)
        references = (
            [(spec.element_type_ref, "Element Type Ref")] if isinstance(spec, ArraySpec)
            else [(type_ref, "Member Type Ref") for _, type_ref in spec.members]
        )
        if _type_dests(store, references, IMPLEMENTATION_TYPE_DESTS, planned_types, context) is None:
            result.skipped += 1
            continue
        if not batch.package_ready(spec.target_file, spec.package_path, context):
            result.skipped += 1
            continue
        if isinstance(spec, ArraySpec):
            def create(document: ArxmlDocument, spec: ArraySpec = spec) -> str:
                return document.add_array_data_type(
                    spec.package_path,
                    spec.name,
                    spec.element_name,
                    spec.element_type_ref,
                    spec.array_size,
                    spec.effective_semantics,
                )
        else:
            def create(document: ArxmlDocument, spec: StructureSpec = spec) -> str:
                return document.add_structure_data_type(spec.package_path, spec.name, spec.members)
        batch.add(spec.target_file, spec.path, IMPLEMENTATION_DATA_TYPE, None, create, spec.path, context)
        prepared.append((kind, spec))

    failed_owners = batch.flush()
    for kind, spec in prepared:
        if spec.path in failed_owners:
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
    batch = _PackageElementBatch(store)
    prepared: list[tuple[str, Union[InterfaceSpec, ClientServerInterfaceSpec], str]] = []
    for spec in each(interfaces, lambda item: item.sources):
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
                spec.path, existing_file,
            ):
                result.skipped += 1
            continue
        if spec.path in batch.planned:
            planned_file, _planned_label, planned_definition = batch.planned[spec.path]
            if not check_existing_definition(
                f"{label} {spec.path} in {planned_file}", planned_definition, (dest, expected, spec.is_service),
                spec.path, planned_file, REUSED,
            ):
                result.skipped += 1
            continue
        context = f"{label} {spec.path}"
        warn_case_variants(store, spec.path, context, batch)
        references = (
            [(type_ref, "Data Type Ref") for _, type_ref, _ in spec.data_elements]
            if isinstance(spec, InterfaceSpec)
            else [(type_ref, "Data Type Ref") for _, arguments in spec.operations for _, type_ref, _ in arguments]
        )
        type_dests = _type_dests(store, references, AUTOSAR_DATA_TYPE_DESTS, planned_types, context)
        if type_dests is None:
            result.skipped += 1
            continue
        if not batch.package_ready(spec.target_file, spec.package_path, context):
            result.skipped += 1
            continue
        if isinstance(spec, InterfaceSpec):
            def create(document: ArxmlDocument, spec: InterfaceSpec = spec, type_dests=type_dests) -> str:
                return document.add_sr_interface(
                    spec.package_path, spec.name, spec.data_elements, spec.is_service, type_dests,
                )
        else:
            def create(
                document: ArxmlDocument, spec: ClientServerInterfaceSpec = spec, type_dests=type_dests,
            ) -> str:
                return document.add_cs_interface(
                    spec.package_path, spec.name, spec.operations, spec.is_service, type_dests,
                )
        batch.add(
            spec.target_file, spec.path, dest, (dest, expected, spec.is_service), create, spec.path, context,
        )
        prepared.append((label, spec, created_detail))

    failed_owners = batch.flush()
    for label, spec, created_detail in prepared:
        if spec.path in failed_owners:
            result.skipped += 1
            continue
        result.planned.append(spec.path)
        LOGGER.info("Prepared %s %s with %s in memory for %s", label, spec.path, created_detail, spec.target_file)
    return result


@dataclass(frozen=True)
class CsRequest:
    """一个 CS operation 的 RTE 对象请求。

    P-Port 新建或复用 ``runnable_name`` 并为 operation 建 OPERATION-INVOKED-EVENT；R-Port 在 ``runnable_name``
    中建同步服务器调用点。``auto_named`` 表示 runnable 名由工具生成，只属于该端口和 operation。
    """

    port: ResolvedPort
    operation: str
    runnable_name: str
    runnable_symbol: str = ""
    minimum_start_interval: float = 0.0
    can_be_invoked_concurrently: bool = False
    auto_named: bool = False
    sources: tuple[Source, ...] = ()

    @property
    def key(self) -> tuple[str, str]:
        return self.port.port_path, self.operation

    @property
    def definition(self) -> tuple[str, float, bool]:
        return self.runnable_symbol, self.minimum_start_interval, self.can_be_invoked_concurrently


def _port_row_requests(port: ResolvedPort, operations: Sequence[str]) -> list[CsRequest]:
    """端口行 ``Create RTE Artifact=Y``：为接口的全部 operation 生成请求。"""

    spec = port.spec
    if spec.direction == DIRECTION_REQUIRED:
        return [CsRequest(port, operation, spec.runnable_name, sources=spec.sources) for operation in operations]
    requests = []
    for operation in operations:
        name = spec.runnable_name or f"RE_{spec.name}_{operation}"
        if spec.runnable_name and len(operations) > 1:
            name = f"{spec.runnable_name}_{operation}"
        name = generated_name(name)
        symbol = spec.runnable_symbol or name
        if spec.runnable_symbol and len(operations) > 1:
            symbol = f"{spec.runnable_symbol}_{operation}"
        requests.append(CsRequest(
            port, operation, name, symbol, spec.minimum_start_interval, spec.can_be_invoked_concurrently,
            not spec.runnable_name, spec.sources,
        ))
    return requests


def _operation_request(spec: OperationSpec, port: ResolvedPort) -> CsRequest | None:
    """``CS Operations`` 页的一行；R-Port 必须填写调用方 runnable，不适用的列只警告。"""

    if port.spec.direction == DIRECTION_REQUIRED:
        if not spec.runnable_name:
            LOGGER.error(
                "Skipped CS operation %s of R-Port %s: Runnable Name of the calling runnable is required",
                spec.operation, port.port_path,
            )
            return None
        ignored = [
            name for name, value in (
                ("Runnable Symbol", spec.runnable_symbol),
                ("Minimum Start Interval", spec.minimum_start_interval),
                ("Can Be Invoked Concurrently", spec.can_be_invoked_concurrently),
            ) if value not in (None, "")
        ]
        if ignored:
            LOGGER.warning(
                "CS operation %s of R-Port %s: column(s) %s apply only to P-Ports and will be ignored",
                spec.operation, port.port_path, ", ".join(ignored),
            )
        return CsRequest(port, spec.operation, spec.runnable_name, sources=spec.sources)
    name = generated_name(spec.runnable_name or f"RE_{port.spec.name}_{spec.operation}")
    return CsRequest(
        port, spec.operation, name, spec.runnable_symbol or name,
        spec.minimum_start_interval or 0.0, bool(spec.can_be_invoked_concurrently),
        not spec.runnable_name, spec.sources,
    )


def _merge_cs_requests(requests: Sequence[CsRequest]) -> list[CsRequest]:
    """合并来自端口行和 ``CS Operations`` 页的相同请求。

    P-Port 每个 operation 只能对应一个 runnable：解析出不同 runnable 或属性时记录 ERROR 并跳过该 operation；
    R-Port 按 ``(端口, operation, runnable)`` 去重，同一 operation 可以在多个 runnable 中调用。
    """

    groups: dict[tuple, list[CsRequest]] = {}
    for request in requests:
        key = request.key if request.port.spec.direction != DIRECTION_REQUIRED else (
            *request.key, request.runnable_name)
        groups.setdefault(key, []).append(request)
    merged: list[CsRequest] = []
    for items in groups.values():
        sources = tuple(dict.fromkeys(source for item in items for source in item.sources))
        first = items[0]
        if any((item.runnable_name, item.definition) != (first.runnable_name, first.definition) for item in items):
            LOGGER.error(
                "Skipped CS operation %s of %s: the port row and CS Operations rows request different runnables "
                "%s; configure one runnable per P-Port operation",
                first.operation, first.port.port_path,
                ", ".join(dict.fromkeys(f"{item.runnable_name}{list(item.definition)}" for item in items)),
                extra=located(sources),
            )
            continue
        if len(items) > 1:
            LOGGER.info("Merged %d duplicate CS request(s) for %s operation %s", len(items) - 1, *first.key)
        merged.append(replace(first, sources=sources))
    return merged


def apply_client_server_artifacts(
    ports: Sequence[ResolvedPort],
    arxml_files: Sequence[Path],
    store: SourceStore,
    operation_ports: Sequence[tuple[OperationSpec, ResolvedPort]] = (),
) -> StepResult:
    """为 CS 端口创建 operation 对应的内部行为对象。

    ``ports`` 只应包含已成功准备或已存在的端口：``Create RTE Artifact=Y`` 的端口处理接口的全部 operation；
    ``operation_ports`` 为 ``CS Operations`` 页的行及其端口，只处理指定的 operation。每个文件先插入 P-Port 的
    runnable 和 operation 事件，重新加载后才创建 R-Port 的服务器调用点，因此调用点可以位于本次新建的
    runnable 中。单个 operation 或插入组失败只记录 error 并跳过，不中断其他对象。
    """

    port_rows = [
        port for port in ports
        if port.spec.create_rte_artifact and port.interface_dest == CLIENT_SERVER_INTERFACE
    ]
    operations_by_interface: dict[str, list[str]] = {}
    for interface_path in sorted({port.spec.interface_path for port in port_rows} | {
            port.spec.interface_path for _spec, port in operation_ports}):
        summary = interface_summary(arxml_files, interface_path, store)
        operations_by_interface[interface_path] = [] if summary is None else list(summary.operations)

    result = StepResult()
    requests: list[CsRequest] = []
    for port in each(port_rows, lambda item: item.spec.sources):
        operations = operations_by_interface.get(port.spec.interface_path)
        if not operations:
            LOGGER.error("Skipped CS artifacts for %s: interface has no operations", port.port_path)
            result.skipped += 1
            continue
        requests.extend(_port_row_requests(port, operations))
    for spec, port in each(operation_ports, lambda item: item[0].sources):
        if spec.operation not in operations_by_interface.get(port.spec.interface_path, ()):
            LOGGER.error(
                "Skipped CS operation %s of %s: operation not found in interface %s, available: %s",
                spec.operation, port.port_path, port.spec.interface_path,
                operations_by_interface.get(port.spec.interface_path, []),
            )
            result.skipped += 1
            continue
        request = _operation_request(spec, port)
        if request is None:
            result.skipped += 1
        else:
            requests.append(request)

    by_file: dict[Path, list[CsRequest]] = defaultdict(list)
    for request in _merge_cs_requests(requests):
        by_file[request.port.component_file].append(request)
    for component_file, file_requests in by_file.items():
        problem = store.modification_problem(component_file)
        if problem is not None:
            LOGGER.error(
                "Skipped CS artifacts in %s", problem,
                extra=located(source for request in file_requests for source in request.sources),
            )
            result.skipped += len(file_requests)
            continue
        providers = [request for request in file_requests if request.port.spec.direction != DIRECTION_REQUIRED]
        requirers = [request for request in file_requests if request.port.spec.direction == DIRECTION_REQUIRED]
        if providers:
            _apply_server_runnables(component_file, providers, store, result)
        if requirers:
            _apply_server_calls(component_file, requirers, store, result)
    return result


def _container_insertions(
    source: bytes,
    groups: Mapping[str, Mapping[str, Sequence[tuple[str, str, tuple[Source, ...]]]]],
    result: StepResult,
) -> tuple[list[xml_patch.Insertion], list[tuple[str, tuple[Source, ...]]]]:
    """计算 ``父路径 -> 容器 -> [(新对象路径, 片段, 来源)]`` 的插入位置。

    同一父元素下的容器（如内部行为的 RUNNABLES 和 EVENTS）全部可定位才插入，避免只插入 runnable 而缺少事件；
    返回插入位置和成功插入的对象。
    """

    insertions: list[xml_patch.Insertion] = []
    created: list[tuple[str, tuple[Source, ...]]] = []
    for parent_path, containers in groups.items():
        items = [item for entries in containers.values() for item in entries]
        try:
            parent_insertions = [
                xml_patch.container_insertion(source, parent_path, container, [fragment for _, fragment, _ in entries])
                for container, entries in containers.items() if entries
            ]
        except ValueError as error:
            LOGGER.error(
                "Skipped CS artifacts in %s: %s", parent_path, clean_message(error),
                extra=located(source_row for _, _, sources in items for source_row in sources),
            )
            result.skipped += len(items)
            for object_path, _fragment, _sources in items:
                if object_path in result.planned:
                    result.planned.remove(object_path)
            continue
        insertions.extend(parent_insertions)
        created.extend((object_path, sources) for object_path, _fragment, sources in items)
    return insertions, created


def _validate_cs_source(component_file: Path, source: bytes, store: SourceStore) -> None:
    if source == store.read(component_file):
        return
    LOGGER.info("Re-parsing ARXML for validation: %s", component_file)
    document, diagnostics = load_bytes(source)
    for diagnostic in diagnostics:
        LOGGER.warning("armodel validation: %s", diagnostic, extra=located(()))
    store.write(component_file, source, document)


@dataclass(frozen=True)
class _PlannedRunnable:
    definition: tuple[str, float, bool]
    # 第一个计划该 runnable 的请求：(端口路径, operation)。
    request: tuple[str, str]
    auto_named: bool


def _new_behavior_groups() -> dict[str, list[tuple[str, str, tuple[Source, ...]]]]:
    return {"RUNNABLES": [], "EVENTS": []}


@dataclass
class _ServerPlan:
    """一个文件中计划插入的 runnable 和 operation 事件。"""

    runnables: dict[str, _PlannedRunnable] = field(default_factory=dict)
    events: dict[str, tuple[str, str]] = field(default_factory=dict)
    # 内部行为路径 -> 容器 -> [(对象路径, 片段, 来源)]
    groups: dict[str, dict[str, list[tuple[str, str, tuple[Source, ...]]]]] = field(
        default_factory=lambda: defaultdict(_new_behavior_groups))
    symbols: dict[str, dict[str, str]] = field(default_factory=dict)


def _apply_server_runnables(
    component_file: Path,
    requests: Sequence[CsRequest],
    store: SourceStore,
    result: StepResult,
) -> None:
    """为 CS P-Port 的 operation 创建（或复用）runnable 和 operation 事件。"""

    document = store.load(component_file)
    source = store.read(component_file)
    plan = _ServerPlan()
    for request in each(requests, lambda item: item.sources):
        port = request.port
        try:
            behavior_path = document.internal_behavior_path(port.component_path)
            if behavior_path is not None and behavior_path not in plan.symbols:
                plan.symbols[behavior_path] = document.runnable_symbols(behavior_path)
        except ValueError as error:
            LOGGER.error("Skipped CS artifacts for %s: %s", port.port_path, clean_message(error))
            result.skipped += 1
            continue
        if behavior_path is None:
            LOGGER.error("Skipped CS artifacts for %s: component has no internal behavior", port.port_path)
            result.skipped += 1
            continue
        try:
            if not _plan_server_runnable(document, source, request, behavior_path, plan, result):
                result.skipped += 1
        except ValueError as error:
            LOGGER.error(
                "Skipped CS artifacts for %s operation %s: %s",
                port.port_path, request.operation, clean_message(error),
            )
            result.skipped += 1

    if not plan.groups:
        return
    insertions, created = _container_insertions(source, plan.groups, result)
    _validate_cs_source(component_file, xml_patch.apply_insertions(source, insertions), store)
    for object_path, sources in created:
        record_rows(sources, CREATED, object_path, component_file)


def _plan_server_runnable(
    document: ArxmlDocument,
    source: bytes,
    request: CsRequest,
    behavior_path: str,
    plan: _ServerPlan,
    result: StepResult,
) -> bool:
    """规划一个 operation 的 runnable 和事件；失败时记录 error 并返回 ``False``。

    先完成 runnable 和事件的全部检查，再生成片段，两者要么都计划插入，要么都不插入。
    自动生成的名称（``RE_``/``oie_`` + 端口名 + operation）可能在不同端口间重合，如端口 ``p_A`` 的
    operation ``B_C`` 与端口 ``p_A_B`` 的 operation ``C``。本次计划的自动命名对象只供同一端口和 operation 使用，
    重合时报错，不静默共用。
    """

    port = request.port
    operation = request.operation
    operation_path = f"{port.spec.interface_path}/{operation}"
    key = request.key
    runnable_name = request.runnable_name
    runnable_symbol = request.runnable_symbol
    runnable_path = f"{behavior_path}/{runnable_name}"
    event_name = generated_name(f"oie_{port.spec.name}_{operation}")
    event_path = f"{behavior_path}/{event_name}"
    definition = request.definition
    behavior_names = xml_patch.child_names(source, behavior_path)
    symbols = plan.symbols[behavior_path]

    planned = plan.runnables.get(runnable_path)
    if planned is not None and (request.auto_named or planned.auto_named) and planned.request != key:
        LOGGER.error(
            "Runnable %s for %s operation %s collides with the runnable planned in this run for %s operation %s; "
            "generated CS names must be unique: fill in or change Runnable Name, or rename a port or operation",
            runnable_path, *key, *planned.request,
        )
        return False
    if event_path in plan.events and plan.events[event_path] != key:
        LOGGER.error(
            "Operation event %s for %s operation %s collides with the event planned in this run for %s "
            "operation %s; generated CS names must be unique: rename a port or operation",
            event_path, *key, *plan.events[event_path],
        )
        return False

    create_runnable = False
    if planned is not None:
        if planned.definition != definition:
            LOGGER.error(
                "Runnable %s is already planned in this run with a different definition: planned=%s, "
                "requested=%s; please choose a different Runnable Name",
                runnable_path, planned.definition, definition,
            )
            return False
        LOGGER.info("Runnable %s is already planned in this run with the same definition; reused", runnable_path)
        record_rows(request.sources, REUSED, runnable_path)
    elif document.element_exists(runnable_path):
        if not check_existing_definition(
            f"Runnable {runnable_path}", document.runnable_definition(runnable_path), definition, runnable_path,
        ):
            return False
    else:
        create_runnable = True

    expected_event = (port.port_path, operation_path, runnable_path)
    create_event = not document.element_exists(event_path)
    if not create_event:
        existing_event = document.read_operation_event(behavior_path, event_name)
        definition_event = None if existing_event is None else (
            existing_event.port_path, existing_event.operation_path, existing_event.runnable_path,
        )
        if not check_existing_definition(
            f"Operation event {event_path}", definition_event, expected_event, event_path,
        ):
            return False

    groups = plan.groups[behavior_path]
    planned_names = [path.rpartition("/")[2] for entries in groups.values() for path, _, _ in entries]
    if create_runnable:
        warn_name_case_variants(
            [*behavior_names, *planned_names], runnable_name,
            f"CS artifacts for {port.port_path}: runnable in {behavior_path}",
        )
        duplicate_symbol = next((name for name, symbol in symbols.items() if symbol == runnable_symbol), None)
        if duplicate_symbol is not None:
            LOGGER.warning(
                "Runnable %s uses SYMBOL %s, which is already used by runnable %s/%s",
                runnable_path, runnable_symbol, behavior_path, duplicate_symbol,
            )
        if runnable_symbol in C_KEYWORDS:
            LOGGER.warning(
                "Runnable %s uses SYMBOL %s, which is a C keyword; RTE code generation may fail, "
                "left to EB validation",
                runnable_path, runnable_symbol,
            )
        groups["RUNNABLES"].append((runnable_path, document.add_runnable(
            behavior_path, runnable_name, runnable_symbol,
            request.minimum_start_interval, request.can_be_invoked_concurrently,
        ), request.sources))
        plan.runnables[runnable_path] = _PlannedRunnable(definition, key, request.auto_named)
        symbols[runnable_name] = runnable_symbol
        result.planned.append(runnable_path)
        planned_names.append(runnable_name)
    if create_event:
        warn_name_case_variants(
            [*behavior_names, *planned_names], event_name,
            f"CS artifacts for {port.port_path}: operation event in {behavior_path}",
        )
        groups["EVENTS"].append((event_path, document.add_operation_event(
            behavior_path, event_name, runnable_path, port.port_path, operation_path
        ), request.sources))
        plan.events[event_path] = key
        result.planned.append(event_path)
    return True


def _apply_server_calls(
    component_file: Path,
    requests: Sequence[CsRequest],
    store: SourceStore,
    result: StepResult,
) -> None:
    """为 CS R-Port 的 operation 在指定 runnable 中创建同步服务器调用点。"""

    document = store.load(component_file)
    source = store.read(component_file)
    call_groups: dict[str, dict[str, list[tuple[str, str, tuple[Source, ...]]]]] = {}
    # 本次计划的调用点：完整路径 -> (端口路径, operation)；自动名称可能在不同端口间重合。
    planned_calls: dict[str, tuple[str, str]] = {}
    for request in each(requests, lambda item: item.sources):
        port = request.port
        operation = request.operation
        operation_path = f"{port.spec.interface_path}/{operation}"
        try:
            runnable_path = document.find_runnable(port.component_path, request.runnable_name)
            if runnable_path is None:
                LOGGER.error(
                    "Skipped CS call for %s: runnable %s not found",
                    port.port_path,
                    request.runnable_name,
                )
                result.skipped += 1
                continue
            name = generated_name(f"scp_{port.spec.name}_{operation}")
            call_path = f"{runnable_path}/{name}"
            if call_path in planned_calls:
                LOGGER.error(
                    "Server call %s for %s operation %s collides with the server call planned in this run "
                    "for %s operation %s; generated CS names must be unique: rename a port or operation",
                    call_path, port.port_path, operation, *planned_calls[call_path],
                )
                result.skipped += 1
                continue
            if document.element_exists(call_path):
                existing = document.read_server_call(runnable_path, name)
                definition = None if existing is None else (
                    existing.port_path, existing.operation_path, existing.timeout
                )
                if not check_existing_definition(
                    f"Server call {call_path}", definition, (port.port_path, operation_path, 0.0), call_path,
                ):
                    result.skipped += 1
                continue
            calls = call_groups.setdefault(runnable_path, {"SERVER-CALL-POINTS": []})["SERVER-CALL-POINTS"]
            warn_name_case_variants(
                [*xml_patch.child_names(source, runnable_path), *(path.rpartition("/")[2] for path, _, _ in calls)],
                name, f"CS call for {port.port_path}: server call point in {runnable_path}",
            )
            calls.append(
                (call_path, document.add_server_call(runnable_path, name, port.port_path, operation_path),
                 request.sources)
            )
            planned_calls[call_path] = (port.port_path, operation)
            result.planned.append(call_path)
        except ValueError as error:
            LOGGER.error(
                "Skipped CS call for %s operation %s: %s", port.port_path, operation, clean_message(error)
            )
            result.skipped += 1

    if call_groups:
        insertions, created = _container_insertions(source, call_groups, result)
        _validate_cs_source(component_file, xml_patch.apply_insertions(source, insertions), store)
        for object_path, sources in created:
            record_rows(sources, CREATED, object_path, component_file)


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
) -> ArxmlDocument:
    """把修改后的字节重新解析，确认后端能完整读回已处理的内容；返回解析结果供后续步骤复用。"""

    check_document, diagnostics = load_bytes(document)
    for diagnostic in diagnostics:
        LOGGER.warning("armodel validation: %s", diagnostic, extra=located(()))
    for port in expected_ports:
        check_port(check_document.read_port(port.component_path, port.spec.name), port)
    for access in expected_accesses:
        check_access(check_document.read_access(access.runnable_path, access.name, access.kind), access)
    return check_document


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
        problem = store.modification_problem(source_file)
        if problem is not None:
            LOGGER.error(
                "Skipped ports and data accesses in %s", problem,
                extra=located(source for item in [
                    *(port.spec.sources for port in ports_by_file.get(source_file, [])),
                    *(access.sources for access in accesses_by_file.get(source_file, [])),
                ] for source in item),
            )
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
        for port in each(ports_by_file.get(source_file, []), lambda item: item.spec.sources):
            try:
                existing = document.read_port(port.component_path, port.spec.name)
                if existing is not None:
                    check_port(existing, port)
                    LOGGER.info(
                        "Port %s already exists with the same definition for explicitly provided "
                        "fields; skipped",
                        port.port_path,
                    )
                    record_rows(port.spec.sources, EXISTING, port.port_path, source_file)
                    existing_ports.append(port)
                    continue
                existing_tag = xml_patch.element_tag(source, port.port_path)
                if existing_tag is not None:
                    raise ValueError(
                        f"{port.port_path} exists as {existing_tag}, expected {port.spec.element_name}; "
                        "please choose a different port name"
                    )
                warn_case_variants(store, port.port_path, f"port {port.port_path}")
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

        for access in each(accesses_by_file.get(source_file, []), lambda item: item.sources):
            container = access.container
            try:
                existing_access = document.read_access(access.runnable_path, access.name, access.kind)
                if existing_access is not None:
                    check_access(existing_access, access)
                    if not access.reused:
                        LOGGER.info(
                            "Data access %s already exists with the same definition; skipped",
                            access.access_path,
                        )
                    record_rows(access.sources, EXISTING, access.access_path, source_file)
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

        original = source
        insertions = [
            xml_patch.container_insertion(source, component_path, "PORTS", fragments)
            for component_path, fragments in fragments_by_component.items()
        ]
        for (runnable_path, container), fragments in access_fragments_by_container.items():
            group_accesses = accesses_by_container[(runnable_path, container)]
            try:
                insertions.append(xml_patch.container_insertion(source, runnable_path, container, fragments))
            except ValueError as error:
                LOGGER.error(
                    "Skipped data accesses in %s: %s",
                    runnable_path,
                    clean_message(error),
                    extra=located(source_row for access in group_accesses for source_row in access.sources),
                )
                result.skipped_accesses += len(fragments)
                continue
            accesses_to_validate.extend(group_accesses)
            planned_accesses.extend(access.access_path for access in group_accesses)
            for access in group_accesses:
                record_rows(access.sources, CREATED, access.access_path, source_file)
        source = xml_patch.apply_insertions(source, insertions)
        # 没有插入任何内容时，已有端口和访问点已按加载的文档检查过，不再重新解析和写回。
        if source != original:
            LOGGER.info("Re-parsing ARXML for validation: %s", source_file)
            check_document = validate_document(source, ports_to_validate, accesses_to_validate)
            store.write(source_file, source, check_document)
        for port in planned_ports:
            record_rows(port.spec.sources, CREATED, port.port_path, source_file)
        for access_path in planned_accesses:
            LOGGER.info("Prepared data access %s in memory for %s", access_path, source_file)
        changes.append(FileChange(source_file, source, planned_ports, existing_ports, planned_accesses))
    return changes, result
