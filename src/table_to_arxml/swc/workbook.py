"""工作簿读取：一次加载全部工作表，逐表校验并转换成待创建对象的描述（Spec）。

工作表按列名读取，列顺序任意；合并单元格的值会展开到区域内每个单元格。
不合法的记录只记录 ERROR 并跳过，由主流程在最后统一阻止写盘。
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence, cast

from openpyxl import load_workbook

from ..common.messages import clean_message
from ..common.names import autosar_path, c_identifier, generated_name, short_name
from ..common.paths import filesystem_path
from .constants import (
    ACCESS_DIRECTIONS,
    DIRECTION_PROVIDED,
    DIRECTION_REQUIRED,
    PORT_ELEMENTS,
)

from .schema import (
    COMPONENTS_SHEET,
    P_PORTS_SHEET,
    R_PORTS_SHEET,
    DATA_ACCESS_SHEET,
    SR_INTERFACE_SHEET,
    CS_INTERFACE_SHEET,
    ENUMERATION_SHEET,
    ARRAY_SHEET,
    STRUCTURE_SHEET,
    ACCESS_KIND_LABELS,
    BOOLEAN_LABELS,
    BOOLEAN_CHOICES,
    ARRAY_SIZE_SEMANTICS,
    CS_ARGUMENT_DIRECTIONS,
    ACCESS_KIND_CHOICES,
    SHEET_HEADERS,
)

LOGGER = logging.getLogger(__name__)

Sheets = dict[str, list[tuple[object, ...]]]

# ``Enumerations`` 表留空时，计算方法和数据约束放在数据类型包的同级包里。
ENUM_COMPU_METHOD_PACKAGE = "CompuMethods"
ENUM_DATA_CONSTR_PACKAGE = "DataConstrs"
ENUM_DATA_CONSTR_PREFIX = "dc_"


class _AutosarPath:
    """``path`` 为完整 AUTOSAR 路径的 Spec 共用：拆出所在包和简称。"""

    path: str

    @property
    def package_path(self) -> str:
        return self.path.rsplit("/", maxsplit=1)[0]

    @property
    def name(self) -> str:
        return self.path.rsplit("/", maxsplit=1)[-1]


@dataclass(frozen=True)
class EnumerationSpec(_AutosarPath):
    """``Enumerations`` 表中一个待创建的枚举类型；``Type Path`` 相同的多行按行顺序合并取值。"""

    path: str
    target_file: Path
    base_type: str
    compu_method_path: str
    data_constr_path: str
    values: tuple[tuple[str, str], ...]

    @property
    def limits(self) -> tuple[str, str]:
        numbers = [int(value) for value, _ in self.values]
        return str(min(numbers)), str(max(numbers))


@dataclass(frozen=True)
class ArraySpec(_AutosarPath):
    """``Arrays`` 表中的固定长度实现数据类型。"""

    path: str
    target_file: Path
    element_name: str
    element_type_ref: str
    array_size: int
    array_size_semantics: str = "FIXED-SIZE"


@dataclass(frozen=True)
class StructureSpec(_AutosarPath):
    """``Structures`` 表中按行顺序定义成员的结构体实现数据类型。"""

    path: str
    target_file: Path
    members: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class InterfaceSpec(_AutosarPath):
    """``SR-Interfaces`` 表中一个待创建的 Sender-Receiver 接口；同一 ``Interface Path`` 的多行合并。"""

    path: str
    target_file: Path
    data_elements: tuple[tuple[str, str, bool], ...]
    is_service: bool = False


@dataclass(frozen=True)
class ClientServerInterfaceSpec(_AutosarPath):
    """``CS-Interfaces`` 表中按 operation 聚合参数的接口定义。"""

    path: str
    target_file: Path
    operations: tuple[tuple[str, tuple[tuple[str, str, str], ...]], ...]
    is_service: bool = False


@dataclass(frozen=True)
class ComponentSpec:
    """``Components`` 表中的一条记录：组件简称及其 AUTOSAR 路径。"""

    key: str
    path: str


@dataclass(frozen=True)
class PortSpec:
    """``P-Ports`` 或 ``R-Ports`` 表中的一条端口请求。

    ComSpec 列按 AUTOSAR 规则分方向取值：``enable_update`` 和 ``queue_length`` 只存在于
    R-Port（且互斥），``ack_timeout`` 和 ``uses_protection`` 只存在于 P-Port。
    ``runnable_name`` 在 P-Ports 中表示 SR 接口的已有 runnable 或 CS 接口的新建 runnable 名称；
    在 R-Ports 中表示已有 runnable 的简称。``provided_fields`` 记录用户显式填写的可选列，
    供后续在某些设置不适用时给出警告。
    """

    component_key: str
    name: str
    interface_path: str
    direction: str
    enable_update: bool = False
    queue_length: int = 0
    ack_timeout: float | None = None
    uses_protection: bool = False
    runnable_name: str = ""
    create_rte_artifact: bool = False
    access_kind: str = ""
    runnable_symbol: str = ""
    minimum_start_interval: float = 0.0
    can_be_invoked_concurrently: bool = False
    provided_fields: frozenset[str] = frozenset()

    @property
    def element_name(self) -> str:
        return PORT_ELEMENTS[self.direction]

    @property
    def needs_com_specs(self) -> bool:
        return (
            self.enable_update
            or self.queue_length > 0
            or self.ack_timeout is not None
            or self.uses_protection
        )


@dataclass(frozen=True)
class AccessSpec:
    """``Data Accesses`` 表中的一条数据访问点请求；``data_element``、``kind``、``name`` 为空表示交由脚本推导。"""

    component_key: str
    runnable_name: str
    port_name: str
    data_element: str
    kind: str
    name: str


def normalized_cell(value: object, context: str) -> str:
    """把单元格转为去除首尾空白的文本；非文本内容视为配置错误。

    数字按整数或原样格式化，方便枚举取值这类列直接填数值。
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value)
    if not isinstance(value, str):
        raise ValueError(f"{context} must be text, got {type(value).__name__}")
    return value.strip()


def sheet_values(sheet) -> list[tuple[object, ...]]:
    """按行读取工作表，并把合并区域的值展开到区域内每个单元格。

    openpyxl 只在合并区域左上角保存值，其余单元格为 ``None``；展开后各行都能
    独立解析，工作簿是否合并单元格不影响结果。
    """
    values = [[cell.value for cell in row] for row in sheet.iter_rows()]
    for merged in sheet.merged_cells.ranges:
        anchor = values[merged.min_row - 1][merged.min_col - 1]
        for row_index in range(merged.min_row - 1, merged.max_row):
            for column_index in range(merged.min_col - 1, merged.max_col):
                values[row_index][column_index] = anchor
    return [tuple(row) for row in values]


def load_workbook_sheets(workbook_path: Path) -> Sheets:
    """一次读出工作簿全部工作表的值；需要合并区域信息，因此不能用 read_only 模式。"""

    workbook = load_workbook(workbook_path, data_only=False)
    try:
        supported = {name.casefold() for name in SHEET_HEADERS}
        sheets: Sheets = {}
        for sheet in workbook.worksheets:
            if sheet.title.casefold() == "readme":
                continue
            if sheet.title.casefold() not in supported:
                LOGGER.error(
                    "Unknown worksheet %s; supported: %s, README",
                    sheet.title, ", ".join(SHEET_HEADERS),
                )
                continue
            sheets[sheet.title] = sheet_values(sheet)
        return sheets
    finally:
        workbook.close()


def parse_flag(label: str, column: str, context: str) -> bool | None:
    """解析布尔列；空值为否，非法取值记录 error 并返回 ``None``。"""

    if not label:
        return False
    value = BOOLEAN_LABELS.get(label.casefold())
    if value is None:
        LOGGER.error(
            "Skipped %s: unknown %s %r, expected one of %s", context, column, label, BOOLEAN_CHOICES
        )
    return value


def parse_non_negative(
    text: str, column: str, context: str, integer: bool = False
) -> float | None:
    """解析非负数值；非法取值记录 error 并返回 ``None``。"""

    try:
        value = float(int(text)) if integer else float(text)
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or value < 0:
        LOGGER.error(
            "Skipped %s: %s must be a non-negative %s: %s",
            context,
            column,
            "integer" if integer else "number",
            text,
        )
        return None
    return value


def read_sheet_rows(
    sheets: Sheets,
    sheet_name: str,
) -> list[dict[str, str]]:
    """校验一张工作表的表头并返回非空数据行。

    未使用的工作表可省略；只有表头而无数据行时也返回空列表。
    schema 中的可选列允许为空，且允许整列缺失。列顺序任意；未知列、
    重复列或缺少必填列时记录 error 并跳过整张表。
    """
    matches = [name for name in sheets if name.casefold() == sheet_name.casefold()]
    if not matches:
        LOGGER.debug("Optional worksheet not present: %s", sheet_name)
        return []
    if len(matches) > 1:
        LOGGER.error("Multiple worksheets match %s: %s", sheet_name, ", ".join(matches))
        return []
    rows = sheets[matches[0]]

    if not rows or not any(value is not None for value in rows[0]):
        if any(any(value is not None for value in row) for row in rows[1:]):
            LOGGER.error("Skipped worksheet %s: missing header row", sheet_name)
        return []

    required_headers, optional_headers = SHEET_HEADERS[sheet_name]
    required = list(required_headers)
    optional = list(optional_headers)
    try:
        header_values = list(rows[0])
        while header_values and header_values[-1] is None:
            header_values.pop()
        raw_headers = [
            normalized_cell(value, f"{sheet_name} header")
            for value in header_values
        ]
    except ValueError as error:
        LOGGER.error("%s", clean_message(error))
        return []

    canonical = {name.casefold(): name for name in required + optional}
    headers = [canonical.get(header.casefold(), header) for header in raw_headers]
    folded = [header.casefold() for header in headers]
    duplicates = sorted(
        {header for header in headers if folded.count(header.casefold()) > 1}
    )
    unknown = [header for header in headers if header.casefold() not in canonical]
    missing_headers = [name for name in required if name.casefold() not in folded]
    if duplicates or unknown or missing_headers:
        LOGGER.error(
            "Skipped worksheet %s: missing=%s, unknown=%s, duplicate=%s; got %s",
            sheet_name,
            missing_headers,
            unknown,
            duplicates,
            raw_headers,
        )
        return []

    records: list[dict[str, str]] = []
    for row_number, values in enumerate(rows[1:], start=2):
        if any(value is not None and str(value).strip() for value in values[len(headers):]):
            LOGGER.error(
                "Skipped %s row %d: data under a blank header", sheet_name, row_number
            )
            continue
        # openpyxl 对尾部空列可能返回更短的元组，补齐后再按列取值。
        padded = tuple(values) + (None,) * (len(headers) - len(values))
        try:
            cells = [
                normalized_cell(padded[index], f"{sheet_name}!{header}{row_number}")
                for index, header in enumerate(headers)
            ]
        except ValueError as error:
            LOGGER.error("Skipped %s row %d: %s", sheet_name, row_number, clean_message(error))
            continue
        if not any(cells):
            continue
        record = dict(zip(headers, cells))
        missing = [name for name in required if not record.get(name)]
        if missing:
            LOGGER.error(
                "Skipped %s row %d: empty required column(s) %s",
                sheet_name,
                row_number,
                ", ".join(missing),
            )
            continue
        for name in optional:
            record.setdefault(name, "")
        try:
            for name, value in record.items():
                if not value:
                    continue
                if name.endswith((" Path", " Ref")):
                    autosar_path(value, name, 2 if name in ("Type Path", "Interface Path") else 1)
                elif name.endswith(" Name"):
                    short_name(value, name)
                elif name == "Runnable Symbol":
                    c_identifier(value)
        except ValueError as error:
            LOGGER.error("Skipped %s row %d: %s", sheet_name, row_number, error)
            continue
        records.append(record)
    return records


def resolve_target_file(listed_path: str, project_root: Path, context: str) -> Path | None:
    """把 ``Target ARXML`` 列转为绝对路径；路径非法或文件不存在时记录 error 并返回 ``None``。"""

    try:
        listed = filesystem_path(listed_path)
    except ValueError as error:
        LOGGER.error("Skipped %s: %s", context, error)
        return None
    if listed.is_absolute():
        LOGGER.error(
            "Skipped %s: Target ARXML must be relative to projectRoot: %s", context, listed_path
        )
        return None
    target = (project_root / listed).resolve()
    if not target.is_file():
        LOGGER.error("Skipped %s: Target ARXML not found: %s", context, listed_path)
        return None
    return target


def _check_type_path(path: str, kind: str) -> bool:
    """新建对象的路径必须是绝对路径且位于某个包下。"""

    if path.startswith("/") and path.count("/") >= 2:
        return True
    LOGGER.error("Skipped %s: path must be absolute and below a package: %s", kind, path)
    return False


def _check_reference(value: str, column: str, context: str) -> bool:
    if value.startswith("/"):
        return True
    LOGGER.error("Skipped %s: %s must be absolute: %s", context, column, value)
    return False


def _group_members(
    groups: dict, path: str, common: dict[str, object], context: str, empty: Callable = list,
):
    """把一行并入同一路径的分组并返回成员容器；公共字段与先前行冲突时记录 error 并返回 ``None``。"""

    known, members = groups.setdefault(path, (common, empty()))
    conflicting = sorted(name for name, value in common.items() if known[name] != value)
    if conflicting:
        LOGGER.error("Skipped %s row: conflicting %s", context, ", ".join(conflicting))
        return None
    return members


def _is_duplicate(members: Sequence[tuple], name: str, label: str, context: str) -> bool:
    if any(member[0] == name for member in members):
        LOGGER.error("Skipped duplicate %s %s in %s", label, name, context)
        return True
    return False


def default_enum_path(type_path: str, package: str, prefix: str = "") -> str:
    """按数据类型路径推导计算方法或数据约束的默认路径。

    例如 ``/BcmSystem/ImplementationDataTypes/X`` 会得到
    ``/BcmSystem/CompuMethods/X`` 和 ``/BcmSystem/DataConstrs/dc_X``。
    """
    name = type_path.rsplit("/", maxsplit=1)[-1]
    parent = type_path.rsplit("/", maxsplit=1)[0].rsplit("/", maxsplit=1)[0]
    return f"{parent}/{package}/{generated_name(prefix + name)}"


def read_enumerations(sheets: Sheets, project_root: Path) -> list[EnumerationSpec]:
    """读取 ``Enumerations`` 表，合并同一枚举类型的多个取值。

    同一 ``Type Path`` 的各行必须填写相同的目标文件、基础类型和可选路径；
    取值必须是整数且不重复，文本标签也不能重复。
    """
    rows = read_sheet_rows(sheets, ENUMERATION_SHEET)
    groups: dict[str, tuple[dict[str, object], list[tuple[str, str]]]] = {}
    for row in rows:
        path = row["Type Path"]
        context = f"enumeration {path}"
        base_type = row["Implementation Data Type Ref"]
        if not _check_type_path(path, "enumeration") or not _check_reference(
            base_type, "Implementation Data Type Ref", context
        ):
            continue
        try:
            value = str(int(row["Value"]))
        except ValueError:
            LOGGER.error("Skipped %s: Value must be an integer: %s", context, row["Value"])
            continue
        target = resolve_target_file(row["Target ARXML"], project_root, context)
        if target is None:
            continue
        values = _group_members(groups, path, {
            "Target ARXML": target,
            "Implementation Data Type Ref": base_type,
            "Compu Method Ref": row.get("Compu Method Ref", "")
            or default_enum_path(path, ENUM_COMPU_METHOD_PACKAGE),
            "Data Constr Ref": row.get("Data Constr Ref", "")
            or default_enum_path(path, ENUM_DATA_CONSTR_PACKAGE, ENUM_DATA_CONSTR_PREFIX),
        }, context)
        if values is None or _is_duplicate(values, value, "value", context):
            continue
        if any(label == row["Label"] for _, label in values):
            LOGGER.error("Skipped duplicate label %s in %s", row["Label"], context)
            continue
        values.append((value, row["Label"]))

    return [
        EnumerationSpec(
            path=path,
            target_file=cast(Path, common["Target ARXML"]),
            base_type=cast(str, common["Implementation Data Type Ref"]),
            compu_method_path=cast(str, common["Compu Method Ref"]),
            data_constr_path=cast(str, common["Data Constr Ref"]),
            values=tuple(values),
        )
        for path, (common, values) in groups.items()
        if values
    ]


def read_arrays(sheets: Sheets, project_root: Path) -> list[ArraySpec]:
    """读取固定长度数组；每行定义一个 ``IMPLEMENTATION-DATA-TYPE``。"""

    rows = read_sheet_rows(sheets, ARRAY_SHEET)
    specs: list[ArraySpec] = []
    seen: set[str] = set()
    for row in rows:
        path = row["Type Path"]
        context = f"array {path}"
        if not _check_type_path(path, "array"):
            continue
        if path in seen:
            LOGGER.error("Skipped duplicate array Type Path: %s", path)
            continue
        if not _check_reference(row["Element Type Ref"], "Element Type Ref", context):
            continue
        try:
            array_size = int(row["Array Size"])
        except ValueError:
            array_size = 0
        if array_size <= 0:
            LOGGER.error(
                "Skipped %s: Array Size must be a positive integer: %s", context, row["Array Size"]
            )
            continue
        semantics = (row.get("Array Size Semantics", "") or "FIXED-SIZE").upper()
        if semantics not in ARRAY_SIZE_SEMANTICS:
            LOGGER.error(
                "Skipped %s: Array Size Semantics must be FIXED-SIZE or VARIABLE-SIZE: %s",
                context,
                semantics,
            )
            continue
        target = resolve_target_file(row["Target ARXML"], project_root, context)
        if target is None:
            continue
        seen.add(path)
        specs.append(
            ArraySpec(path, target, row["Element Name"], row["Element Type Ref"], array_size, semantics)
        )
    return specs


def read_structures(sheets: Sheets, project_root: Path) -> list[StructureSpec]:
    """读取结构体，并按 Type Path 合并多行成员。"""

    rows = read_sheet_rows(sheets, STRUCTURE_SHEET)
    groups: dict[str, tuple[dict[str, object], list[tuple[str, str]]]] = {}
    for row in rows:
        path = row["Type Path"]
        context = f"structure {path}"
        if not _check_type_path(path, "structure") or not _check_reference(
            row["Member Type Ref"], "Member Type Ref", context
        ):
            continue
        target = resolve_target_file(row["Target ARXML"], project_root, context)
        if target is None:
            continue
        members = _group_members(groups, path, {"Target ARXML": target}, context)
        if members is None or _is_duplicate(members, row["Member Name"], "member", context):
            continue
        members.append((row["Member Name"], row["Member Type Ref"]))
    return [
        StructureSpec(path, cast(Path, common["Target ARXML"]), tuple(members))
        for path, (common, members) in groups.items()
        if members
    ]


def read_sr_interfaces(sheets: Sheets, project_root: Path) -> list[InterfaceSpec]:
    """读取 ``SR-Interfaces`` 表，合并同一接口的多个数据元素。

    同一 ``Interface Path`` 的各行必须写同一个 ``Target ARXML`` 和 ``Is Service``，数据元素不能重名。
    """
    rows = read_sheet_rows(sheets, SR_INTERFACE_SHEET)
    groups: dict[str, tuple[dict[str, object], list[tuple[str, str, bool]]]] = {}
    for row in rows:
        path = row["Interface Path"]
        context = f"SR interface {path}"
        if not _check_type_path(path, "SR interface") or not _check_reference(
            row["Data Type Ref"], "Data Type Ref", context
        ):
            continue
        element_context = f"{context} data element {row['Data Element Name']}"
        target = resolve_target_file(row["Target ARXML"], project_root, element_context)
        if target is None:
            continue
        is_queued = parse_flag(row.get("Is Queued", ""), "Is Queued", element_context)
        is_service = parse_flag(row.get("Is Service", ""), "Is Service", element_context)
        if is_queued is None or is_service is None:
            continue
        elements = _group_members(
            groups, path, {"Target ARXML": target, "Is Service": is_service}, context
        )
        if elements is None or _is_duplicate(elements, row["Data Element Name"], "data element", context):
            continue
        elements.append((row["Data Element Name"], row["Data Type Ref"], is_queued))
    return [
        InterfaceSpec(path, cast(Path, common["Target ARXML"]), tuple(elements), cast(bool, common["Is Service"]))
        for path, (common, elements) in groups.items()
        if elements
    ]


def read_cs_interfaces(sheets: Sheets, project_root: Path) -> list[ClientServerInterfaceSpec]:
    """读取 CS 接口；参数三列全空表示无参数 operation，不能与有参数的同名定义混用。"""

    rows = read_sheet_rows(sheets, CS_INTERFACE_SHEET)
    groups: dict[str, tuple[dict[str, object], dict[str, list[tuple[str, str, str]]]]] = {}
    for row in rows:
        path = row["Interface Path"]
        context = f"CS interface {path}"
        if not _check_type_path(path, "CS interface"):
            continue
        argument_columns = ("Argument Name", "Direction", "Data Type Ref")
        has_argument = any(row[name] for name in argument_columns)
        if has_argument and not all(row[name] for name in argument_columns):
            LOGGER.error(
                "Skipped %s operation %s: Argument Name, Direction and Data Type Ref "
                "must be all filled or all empty",
                context, row["Operation Name"],
            )
            continue
        if has_argument and not _check_reference(row["Data Type Ref"], "Data Type Ref", context):
            continue
        direction = row["Direction"].upper()
        if has_argument and direction not in CS_ARGUMENT_DIRECTIONS:
            LOGGER.error(
                "Skipped %s argument %s: Direction must be IN, OUT or INOUT", context, row["Argument Name"]
            )
            continue
        target = resolve_target_file(row["Target ARXML"], project_root, context)
        if target is None:
            continue
        is_service = parse_flag(row.get("Is Service", ""), "Is Service", context)
        if is_service is None:
            continue
        operations = _group_members(
            groups, path, {"Target ARXML": target, "Is Service": is_service}, context, dict
        )
        if operations is None:
            continue
        operation = row["Operation Name"]
        if operation in operations and (not has_argument or not operations[operation]):
            LOGGER.error(
                "Skipped %s operation %s: duplicate or conflicting zero-argument declaration",
                context, operation,
            )
            continue
        arguments = operations.setdefault(operation, [])
        if not has_argument:
            continue
        if _is_duplicate(arguments, row["Argument Name"], "argument", f"CS operation {path}/{row['Operation Name']}"):
            continue
        arguments.append((row["Argument Name"], row["Data Type Ref"], direction))
    return [
        ClientServerInterfaceSpec(
            path,
            cast(Path, common["Target ARXML"]),
            tuple((operation, tuple(arguments)) for operation, arguments in operations.items()),
            cast(bool, common["Is Service"]),
        )
        for path, (common, operations) in groups.items()
        if operations
    ]


def read_com_spec_flags(
    row: dict[str, str], sheet_name: str, direction: str
) -> tuple[bool, int, float | None, bool] | None:
    """解析端口行的 ComSpec 列；取值非法时记录 error 并返回 ``None``。

    P-Ports 只有发送确认超时和 E2E 保护；R-Ports 的 ``Enable Update`` 与 ``Queue Length`` 互斥。
    """

    context = f"port {row['Port Name']} in {sheet_name}"
    if direction == DIRECTION_PROVIDED:
        ack_text = row.get("Transmission Acknowledge Timeout", "")
        ack_timeout = None
        if ack_text:
            ack_timeout = parse_non_negative(ack_text, "Transmission Acknowledge Timeout", context)
            if ack_timeout is None:
                return None
        uses_protection = parse_flag(
            row.get("Uses End To End Protection", ""), "Uses End To End Protection", context
        )
        if uses_protection is None:
            return None
        return False, 0, ack_timeout, uses_protection

    enable_update = parse_flag(row.get("Enable Update", ""), "Enable Update", context)
    if enable_update is None:
        return None
    queue_text = row.get("Queue Length", "")
    queue_length = 0.0
    if queue_text:
        parsed = parse_non_negative(queue_text, "Queue Length", context, integer=True)
        if parsed is None:
            return None
        queue_length = parsed
    if enable_update and queue_length:
        LOGGER.error("Skipped %s: Enable Update and Queue Length are mutually exclusive", context)
        return None
    return enable_update, int(queue_length), None, False


def _read_port(
    row: dict[str, str], sheet_name: str, direction: str, components: dict[str, ComponentSpec]
) -> PortSpec | None:
    """把端口页的一行转为 :class:`PortSpec`；不合法时记录 error 并返回 ``None``。"""

    port_name = row["Port Name"]
    if row["SW-C"] not in components:
        LOGGER.error(
            "Skipped port %s in %s: SW-C %s is not defined in the Components sheet",
            port_name,
            sheet_name,
            row["SW-C"],
        )
        return None
    if not row["Port Interface Ref"].startswith("/"):
        LOGGER.error(
            "Skipped port %s in %s: interface path must be absolute: %s",
            port_name,
            sheet_name,
            row["Port Interface Ref"],
        )
        return None
    flags = read_com_spec_flags(row, sheet_name, direction)
    if flags is None:
        return None
    enable_update, queue_length, ack_timeout, uses_protection = flags
    context = f"port {port_name} in {sheet_name}"
    create_rte_artifact = parse_flag(
        row.get("Create RTE Artifact", ""), "Create RTE Artifact", context
    )
    if create_rte_artifact is None:
        return None
    access_kind_label = row.get("Access Kind", "")
    access_kind = ACCESS_KIND_LABELS.get(access_kind_label.casefold(), "")
    if access_kind_label and not access_kind:
        LOGGER.error(
            "Skipped port %s in %s: Access Kind must be one of %s: %s",
            port_name,
            sheet_name,
            ", ".join(ACCESS_KIND_CHOICES),
            access_kind_label,
        )
        return None
    if access_kind and ACCESS_DIRECTIONS[access_kind] != direction:
        LOGGER.error(
            "Skipped port %s in %s: Access Kind %s requires a %s-Port",
            port_name,
            sheet_name,
            access_kind_label,
            ACCESS_DIRECTIONS[access_kind],
        )
        return None
    if create_rte_artifact and direction == DIRECTION_REQUIRED and not row.get("Runnable Name", ""):
        LOGGER.error(
            "Skipped port %s in %s: Runnable Name is required when Create RTE Artifact is Y",
            port_name,
            sheet_name,
        )
        return None
    minimum_start_interval = 0.0
    concurrent = False
    if direction == DIRECTION_PROVIDED:
        interval_text = row.get("Minimum Start Interval", "")
        if interval_text:
            interval = parse_non_negative(interval_text, "Minimum Start Interval", context)
            if interval is None:
                return None
            minimum_start_interval = interval
        concurrent_value = parse_flag(
            row.get("Can Be Invoked Concurrently", ""), "Can Be Invoked Concurrently", context
        )
        if concurrent_value is None:
            return None
        concurrent = concurrent_value
    return PortSpec(
        component_key=row["SW-C"],
        name=port_name,
        interface_path=row["Port Interface Ref"],
        direction=direction,
        enable_update=enable_update,
        queue_length=queue_length,
        ack_timeout=ack_timeout,
        uses_protection=uses_protection,
        runnable_name=row.get("Runnable Name", ""),
        create_rte_artifact=create_rte_artifact,
        access_kind=access_kind,
        runnable_symbol=row.get("Runnable Symbol", ""),
        minimum_start_interval=minimum_start_interval,
        can_be_invoked_concurrently=concurrent,
        provided_fields=frozenset(
            name
            for name in (
                "Runnable Name",
                "Runnable Symbol",
                "Minimum Start Interval",
                "Can Be Invoked Concurrently",
                "Access Kind",
            )
            if row.get(name, "")
        ),
    )


def read_workbook(sheets: Sheets) -> tuple[dict[str, ComponentSpec], list[PortSpec]]:
    """读取组件和端口两页，并校验两者之间的一致性。

    组件简称和路径均不允许重复；同一组件下的端口名在 P/R 两张表中合并去重，
    因为它们最终位于同一个 ``PORTS`` 容器。
    """
    components: dict[str, ComponentSpec] = {}
    paths: set[str] = set()
    for row in read_sheet_rows(sheets, COMPONENTS_SHEET):
        key = row["SW-C"]
        path = row["Component Path"]
        if not path.startswith("/"):
            LOGGER.error("Skipped component %s: path must be absolute: %s", key, path)
            continue
        if key in components:
            LOGGER.error("Skipped duplicate component key in workbook: %s", key)
            continue
        if path in paths:
            LOGGER.error("Skipped duplicate component path in workbook: %s", path)
            continue
        components[key] = ComponentSpec(key, path)
        paths.add(path)

    ports: list[PortSpec] = []
    seen_ports: set[tuple[str, str]] = set()
    for sheet_name, direction in (
        (P_PORTS_SHEET, DIRECTION_PROVIDED),
        (R_PORTS_SHEET, DIRECTION_REQUIRED),
    ):
        rows = read_sheet_rows(sheets, sheet_name)
        for row in rows:
            port = _read_port(row, sheet_name, direction, components)
            if port is None:
                continue
            key = (port.component_key, port.name)
            if key in seen_ports:
                LOGGER.error("Skipped duplicate port for component %s: %s", *key)
                continue
            seen_ports.add(key)
            ports.append(port)

    if not components:
        LOGGER.info("No component records found in workbook")
    if not ports:
        LOGGER.info("No P-Port or R-Port records found in workbook")
    return components, ports


def read_data_accesses(sheets: Sheets, components: dict[str, ComponentSpec]) -> list[AccessSpec]:
    """读取 ``Data Accesses`` 表，返回逐条访问点请求。

    只做与 ARXML 无关的静态校验：组件必须在 ``Components`` 表中出现、
    ``Access Kind`` 必须是已知取值、同一端口的同一数据元素不能重复。
    """
    rows = read_sheet_rows(sheets, DATA_ACCESS_SHEET)

    specs: list[AccessSpec] = []
    seen: set[tuple[str, str, str, str]] = set()
    for row in rows:
        component_key = row["SW-C"]
        if component_key not in components:
            LOGGER.error(
                "Skipped data access for port %s: SW-C %s is not defined in the Components sheet",
                row["Port Name"],
                component_key,
            )
            continue

        label = row.get("Access Kind", "")
        kind = ACCESS_KIND_LABELS.get(label.casefold(), "") if label else ""
        if label and not kind:
            LOGGER.error(
                "Skipped data access %s/%s: unknown Access Kind %r, expected one of %s",
                row["Runnable Name"],
                row["Port Name"],
                label,
                ", ".join(sorted(ACCESS_KIND_LABELS)),
            )
            continue

        unique = (
            component_key,
            row["Runnable Name"],
            row["Port Name"],
            row.get("Data Element Name", ""),
        )
        if unique in seen:
            LOGGER.error("Skipped duplicate data access row: %s / %s / %s / %s", *unique)
            continue
        seen.add(unique)
        specs.append(
            AccessSpec(
                component_key=component_key,
                runnable_name=row["Runnable Name"],
                port_name=row["Port Name"],
                data_element=row.get("Data Element Name", ""),
                kind=kind,
                name=row.get("Access Name", ""),
            )
        )
    return specs
