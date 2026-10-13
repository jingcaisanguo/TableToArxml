"""表格转 SWC ARXML 工具：根据 Excel 配置向指定组件或 ARXML 批量添加端口、接口和数据类型。

工作簿格式：

* ``Components``：``SWC``、``Component Path``，定义组件简称与 AUTOSAR 路径；
* ``P-Ports``：端口、发送端 ComSpec，以及 ``Runnable Name``、``Create RTE Artifact``；
* ``R-Ports``：端口、接收端 ComSpec，以及 ``Runnable Ref``、``Create RTE Artifact``、
    ``Receive Mode``；
* ``Data-Accesses``：可选的高级逐元素 ``VARIABLE-ACCESS`` 配置；通常只需在端口页
    把 ``Create RTE Artifact`` 填为 ``Y``，脚本会覆盖接口全部 element/operation；
* ``Enumerations``：创建枚举及其计算方法和数据约束；
* ``Arrays`` / ``Structures``：创建数组和结构体实现数据类型；
* ``SR-Interfaces``：``Interface Path``、``Target ARXML``、``Data Element``、
    ``Data Type Ref``、``Is Queued``。

为了阅读方便，工作簿里重复的单元格可以合并；脚本会把合并区域的值
展开到每一行，因此合并与否不影响解析结果。
工作表按列名读取，列顺序可以自由调整；未知、重复或缺失列会记录错误。

``projectRoot``、``arxmlFileList``、``workbook`` 和 ``logDir`` 须由命令行或 ``--config``
JSON 提供，工具不使用自身安装目录作为默认位置；
JSON 中的相对路径以配置文件所在目录为基准，支持 ``*`` 唯一匹配以及 ``${variant}``
替换（``--variant`` 优先于 JSON 的 ``defaultVariant``）。
命令行文件和输出目录路径支持绝对路径或相对 ``projectRoot`` 的路径；相对
``--config`` 需要显式指定 ``--project-root``，否则使用绝对配置路径。命令行
``--project-root`` 自身相对工作目录解析，用于建立其余参数的路径基准。
脚本只在 ``--arxml-file-list`` 列出的 ARXML 中查找组件和接口。接口的 AUTOSAR 元类
由解析结果确定，不需要在 Excel 中重复填写 ``DEST``。默认仅构建并验证修改；使用
``--in-place`` 才会覆盖目标 ARXML，使用 ``--output-dir`` 可将修改后的文件
按仓库相对路径写入其他目录。

ARXML 的解析和序列化全部通过 :mod:`.lib.arxml_backend` 完成，本模块不直接依赖
具体解析库；更换解析库只需在该模块中新增一个后端。

运行日志写入 ``logDir`` 目录下的 ``table_to_swc_arxml_<时间>_<进程号>.log``，同时输出到控制台。
"""

from __future__ import annotations

import argparse
from datetime import datetime
import glob
import json
import logging
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence, cast

from openpyxl import load_workbook

from .lib.arxml_backend import (
    ACCESS_CONTAINERS,
    ACCESS_DIRECTIONS,
    ACCESS_READ,
    ACCESS_RECEIVE,
    ACCESS_RECEIVE_VALUE,
    ACCESS_SEND,
    ACCESS_WRITE,
    BACKENDS,
    AccessInfo,
    ArxmlBackend,
    ImplementationTypeInfo,
    PortInfo,
    clean_message,
    get_backend,
)


TOOL_NAME = "table_to_swc_arxml"
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
LOGGER = logging.getLogger(TOOL_NAME)
SHORT_NAME_PATTERN = re.compile(r"<SHORT-NAME>\s*([^<]*?)\s*</SHORT-NAME>")

# JSON 键 -> 内部属性名；命令行参数为 JSON 键的 kebab-case，与 table-to-system-arxml 保持一致。
CONFIG_OPTIONS = {
    "defaultVariant": "variant",
    "projectRoot": "project_dir",
    "arxmlFileList": "list_file",
    "workbook": "workbook",
    "strictList": "strict_list",
    "excludeArxml": "exclude_arxml",
    "logDir": "log_dir",
    "logLevel": "log_level",
    "backend": "backend",
    "inPlace": "in_place",
    "outputDir": "output_dir",
    "dryRun": "dry_run",
}
OPTION_KEYS = {attribute: key for key, attribute in CONFIG_OPTIONS.items()}
PATH_OPTIONS = ("project_dir", "workbook", "list_file", "log_dir", "output_dir")
DIRECTORY_OPTIONS = {"project_dir", "log_dir", "output_dir"}
REQUIRED_OPTIONS = ("project_dir", "list_file", "workbook", "log_dir")


def option_flag(attribute: str) -> str:
    return "--" + re.sub(r"(?<!^)(?=[A-Z])", "-", OPTION_KEYS[attribute]).lower()

# 在原始 ARXML 字节中定位组件块时使用的 AUTOSAR 元类标签。
SWC_ELEMENT_NAMES = {
    "APPLICATION-SW-COMPONENT-TYPE",
    "COMPLEX-DEVICE-DRIVER-SW-COMPONENT-TYPE",
    "COMPOSITION-SW-COMPONENT-TYPE",
    "ECU-ABSTRACTION-SW-COMPONENT-TYPE",
    "SENSOR-ACTUATOR-SW-COMPONENT-TYPE",
    "SERVICE-SW-COMPONENT-TYPE",
}

# ``Data-Accesses`` 表 ``Access Kind`` 列的取值（比较时忽略大小写）。
ACCESS_KIND_LABELS = {
    "send": ACCESS_SEND,
    "receivebyargument": ACCESS_RECEIVE,
    "receivebyvalue": ACCESS_RECEIVE_VALUE,
    "read": ACCESS_READ,
    "write": ACCESS_WRITE,
}

# ``Access Name`` 留空时按端口名生成的默认前缀。
ACCESS_NAME_PREFIXES = {
    ACCESS_SEND: "dsp_",
    ACCESS_RECEIVE: "drparg_",
    ACCESS_RECEIVE_VALUE: "drpval_",
    ACCESS_READ: "dra_",
    ACCESS_WRITE: "dwa_",
}

# ``Access Kind`` 留空时按端口方向推导的默认类别。
DEFAULT_ACCESS_KINDS = {"P": ACCESS_SEND, "R": ACCESS_RECEIVE}

# 布尔标志列的取值（比较时忽略大小写）；留空视为否。
BOOLEAN_LABELS = {
    "true": True,
    "yes": True,
    "y": True,
    "1": True,
    "false": False,
    "no": False,
    "n": False,
    "0": False,
}

# 旧工作簿列名映射到当前规范名称；解析按名称进行，不依赖列顺序。
HEADER_ALIASES = {
    "create access": "Create RTE Artifact",
    "runnable": "Runnable Name",
    "transmission acknowledge": "Transmission Acknowledge Timeout",
    "queuelength": "Queue Length",
    "data element": "Data Element Name",
    "operation": "Operation Name",
    "argument": "Argument Name",
}

DATA_ACCESS_SHEET = "Data-Accesses"
ENUMERATION_SHEET = "Enumerations"
ARRAY_SHEET = "Arrays"
STRUCTURE_SHEET = "Structures"

# ``Enumerations`` 表留空时，计算方法和数据约束放在数据类型包的同级包里。
ENUM_COMPU_METHOD_PACKAGE = "CompuMethods"
ENUM_DATA_CONSTR_PACKAGE = "DataConstrs"
ENUM_DATA_CONSTR_PREFIX = "dc_"


@dataclass(frozen=True)
class EnumerationSpec:
    """``Enumerations`` 表中一个待创建的枚举类型。

    ``Type Path`` 相同的多行合并为同一个枚举，按行顺序生成取值。
    """

    path: str
    target_file: Path
    base_type: str
    compu_method_path: str
    data_constr_path: str
    values: tuple[tuple[str, str], ...]

    @property
    def package_path(self) -> str:
        return self.path.rsplit("/", maxsplit=1)[0]

    @property
    def name(self) -> str:
        return self.path.rsplit("/", maxsplit=1)[-1]

    @property
    def limits(self) -> tuple[str, str]:
        numbers = [int(value) for value, _ in self.values]
        return str(min(numbers)), str(max(numbers))


@dataclass(frozen=True)
class ArraySpec:
    """``Arrays`` 表中的固定长度实现数据类型。"""

    path: str
    target_file: Path
    element_name: str
    element_type_ref: str
    array_size: int
    array_size_semantics: str = "FIXED-SIZE"

    @property
    def package_path(self) -> str:
        return self.path.rsplit("/", maxsplit=1)[0]

    @property
    def name(self) -> str:
        return self.path.rsplit("/", maxsplit=1)[-1]


@dataclass(frozen=True)
class StructureSpec:
    """``Structures`` 表中按行顺序定义成员的结构体实现数据类型。"""

    path: str
    target_file: Path
    members: tuple[tuple[str, str], ...]

    @property
    def package_path(self) -> str:
        return self.path.rsplit("/", maxsplit=1)[0]

    @property
    def name(self) -> str:
        return self.path.rsplit("/", maxsplit=1)[-1]


@dataclass(frozen=True)
class InterfaceSpec:
    """``SR-Interfaces`` 表中一个待创建的 Sender-Receiver 接口。

    ``Interface Path`` 和 ``Target ARXML`` 相同的多行合并为同一个接口。
    """

    path: str
    target_file: Path
    data_elements: tuple[tuple[str, str, bool], ...]
    is_service: bool = False

    @property
    def package_path(self) -> str:
        return self.path.rsplit("/", maxsplit=1)[0]

    @property
    def name(self) -> str:
        return self.path.rsplit("/", maxsplit=1)[-1]


@dataclass(frozen=True)
class ClientServerInterfaceSpec:
    """``CS-Interfaces`` 表中按 operation 聚合参数的接口定义。"""

    path: str
    target_file: Path
    operations: tuple[tuple[str, tuple[tuple[str, str, str], ...]], ...]
    is_service: bool = False

    @property
    def package_path(self) -> str:
        return self.path.rsplit("/", maxsplit=1)[0]

    @property
    def name(self) -> str:
        return self.path.rsplit("/", maxsplit=1)[-1]


@dataclass(frozen=True)
class ComponentSpec:
    """``component`` 表中的一条记录：组件简称及其 AUTOSAR 路径。"""

    key: str
    path: str


@dataclass(frozen=True)
class PortSpec:
    """``P-Ports`` 或 ``R-Ports`` 表中的一条端口请求。

    ComSpec 列按 AUTOSAR 规则分方向取值：``is_update`` 和 ``queue_length`` 只存在于
    R-Port（且互斥），``ack_timeout`` 和 ``uses_protection`` 只存在于 P-Port。
    """

    component_key: str
    name: str
    interface_path: str
    direction: str
    is_update: bool = False
    queue_length: int = 0
    ack_timeout: float | None = None
    uses_protection: bool = False
    runnable: str = ""
    create_access: bool = False
    receive_by_value: bool = False
    runnable_symbol: str = ""
    minimum_start_interval: float = 0.0
    can_be_invoked_concurrently: bool = False

    @property
    def element_name(self) -> str:
        return "P-PORT-PROTOTYPE" if self.direction == "P" else "R-PORT-PROTOTYPE"

    @property
    def needs_com_specs(self) -> bool:
        return (
            self.is_update
            or self.queue_length > 0
            or self.ack_timeout is not None
            or self.uses_protection
        )


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
class AccessSpec:
    """``Data-Accesses`` 表中的一条数据访问点请求。

    ``data_element``、``kind``、``name`` 为空表示交由脚本推导。
    """

    component_key: str
    runnable: str
    port_name: str
    data_element: str
    kind: str
    name: str


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


@dataclass
class FileChange:
    """一个 ARXML 的修改结果，``document`` 为待写出的完整字节。"""

    source_file: Path
    document: bytes
    created_ports: list[ResolvedPort]
    existing_ports: list[ResolvedPort]
    created_accesses: list[str]

    @property
    def has_changes(self) -> bool:
        return bool(self.created_ports or self.created_accesses)


def resolve_config_path(
    value: object, name: str, variant: str | None, config_dir: Path
) -> Path:
    """按配置文件目录解析路径，先替换 variant，再匹配唯一文件或目录。"""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Config option {OPTION_KEYS[name]} must be a non-empty path string")
    if "${variant}" in value:
        if variant is None:
            raise ValueError(
                f"Config option {OPTION_KEYS[name]} uses ${{variant}}; provide --variant or config defaultVariant"
            )
        value = value.replace("${variant}", variant)
    path = Path(value.replace("\\", "/"))
    if not path.is_absolute():
        path = config_dir / path
    if glob.has_magic(str(path)):
        directory = name in DIRECTORY_OPTIONS
        matches = sorted({
            candidate.resolve()
            for match in glob.glob(str(path))
            for candidate in (Path(match),)
            if (candidate.is_dir() if directory else candidate.is_file())
        })
        if not matches:
            raise ValueError(f"Config option {OPTION_KEYS[name]}: no paths match {path}")
        if len(matches) != 1:
            raise ValueError(
                f"Config option {OPTION_KEYS[name]}: pattern {path} matches multiple paths: "
                + ", ".join(str(match) for match in matches)
            )
        return matches[0]
    return path.resolve()


def resolve_cli_path(path: Path, project_dir: Path | None, option: str) -> Path:
    """命令行路径仅接受绝对路径或相对项目根目录的普通路径。"""

    if any(token in str(path) for token in ("*", "?", "${")):
        raise ValueError(f"{option} must be a literal path; templates and wildcards are only supported in JSON")
    if not path.is_absolute():
        if path.drive or path.root:
            raise ValueError(f"{option} must be absolute or relative to projectRoot: {path}")
        if project_dir is None:
            raise ValueError(
                f"Relative {option} requires --project-root; otherwise provide an absolute path"
            )
        path = project_dir / path
    return path.resolve()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """解析工作簿、ARXML 清单和输出方式。

    三个输出选项互斥，默认（均未指定）等同于只验证不写文件。
    """
    parser = argparse.ArgumentParser(
        description="Add component ports from an Excel workbook.",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="JSON file: absolute or relative to explicit --project-root; paths inside JSON use the config file directory",
    )
    parser.add_argument(
        "--variant",
        help="Replace ${variant} in JSON paths (overrides config defaultVariant)",
    )
    parser.add_argument(
        "--project-root",
        dest="project_dir",
        type=Path,
        help="Project root for relative CLI paths; this option itself is relative to the working directory",
    )
    parser.add_argument(
        "--workbook",
        type=Path,
        help="ARXML workbook: absolute or relative to projectRoot; required via CLI or config",
    )
    parser.add_argument(
        "--arxml-file-list",
        dest="list_file",
        type=Path,
        help="ARXML list: absolute or relative to projectRoot; required via CLI or config",
    )
    parser.add_argument(
        "--strict-list",
        action="store_true",
        default=None,
        help=(
            "Fail immediately on any invalid or missing entry in --arxml-file-list "
            "instead of logging a warning and skipping it"
        ),
    )
    parser.add_argument(
        "--exclude-arxml",
        action="append",
        help="Exclude a filename or projectRoot-relative ARXML path; repeat for multiple entries; overrides config.excludeArxml",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        help="Log directory: absolute or relative to projectRoot; required via CLI or config; no default",
    )
    parser.add_argument(
        "--log-level",
        type=str.upper,
        choices=LOG_LEVELS,
        default=None,
        help="Log level, case-insensitive (default: INFO)",
    )
    parser.add_argument(
        "--backend",
        choices=sorted(BACKENDS),
        default=None,
        help="ARXML parsing backend (defaults to armodel)",
    )
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument(
        "--in-place",
        action="store_true",
        default=None,
        help="Explicitly overwrite affected ARXML files",
    )
    output_group.add_argument(
        "--output-dir",
        type=Path,
        help="Output directory: absolute or relative to projectRoot; preserve repository-relative file layout",
    )
    output_group.add_argument(
        "--dry-run",
        action="store_true",
        default=None,
        help="Build and validate changes without writing files",
    )
    args = parser.parse_args(argv)
    cli_path_names = {
        name for name in ("workbook", "list_file", "log_dir", "output_dir")
        if getattr(args, name) is not None
    }
    try:
        if args.project_dir is not None:
            args.project_dir = resolve_cli_path(args.project_dir, Path.cwd(), "--project-root")
        if args.config is not None:
            args.config = resolve_cli_path(args.config, args.project_dir, "--config")
    except (OSError, ValueError) as error:
        parser.error(clean_message(error))

    config: dict[str, object] = {}
    config_dir: Path | None = None
    if args.config is not None:
        config_path = args.config.resolve()
        config_dir = config_path.parent
        try:
            config_data = json.loads(config_path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            parser.error(f"Cannot read config file {config_path}: {clean_message(error)}")
        if not isinstance(config_data, dict):
            parser.error(f"Config file must contain a JSON object: {config_path}")
        unknown = sorted(set(config_data) - set(CONFIG_OPTIONS))
        if unknown:
            parser.error(
                f"Unknown config option(s) in {config_path}: {', '.join(unknown)}; "
                f"supported: {', '.join(CONFIG_OPTIONS)}"
            )
        config = {CONFIG_OPTIONS[key]: value for key, value in config_data.items()}

    if args.variant is None:
        args.variant = config.get("variant")
    if args.variant is not None and (
        not isinstance(args.variant, str) or not args.variant.strip()
    ):
        parser.error("--variant or config defaultVariant must be a non-empty string")
    for name in OPTION_KEYS:
        if name == "variant":
            continue
        if getattr(args, name) is None and name in config:
            value = config[name]
            if name in PATH_OPTIONS and value is not None:
                assert config_dir is not None
                try:
                    value = resolve_config_path(value, name, args.variant, config_dir)
                except (OSError, ValueError) as error:
                    parser.error(clean_message(error))
            setattr(args, name, value)

    missing = [option_flag(name) for name in REQUIRED_OPTIONS if getattr(args, name) is None]
    if missing:
        parser.error(", ".join(missing) + " required unless supplied by --config")

    for name in REQUIRED_OPTIONS:
        if not isinstance(getattr(args, name), (str, Path)):
            parser.error(f"{OPTION_KEYS[name]} must be a path string")
    assert args.project_dir is not None
    args.project_dir = Path(args.project_dir).resolve()
    try:
        for name in cli_path_names:
            setattr(args, name, resolve_cli_path(
                getattr(args, name), args.project_dir, option_flag(name)
            ))
    except (OSError, ValueError) as error:
        parser.error(clean_message(error))
    args.list_file = Path(args.list_file)
    args.workbook = Path(args.workbook)
    args.log_dir = Path(args.log_dir)
    if args.log_level is None:
        args.log_level = "INFO"
    if args.backend is None:
        args.backend = "armodel"
    if not isinstance(args.log_level, str) or args.log_level.upper() not in LOG_LEVELS:
        parser.error(f"logLevel must be one of: {', '.join(LOG_LEVELS)}")
    args.log_level = args.log_level.upper()
    if args.backend not in BACKENDS:
        parser.error(f"backend must be one of: {', '.join(sorted(BACKENDS))}")
    if args.strict_list is None:
        args.strict_list = False
    if args.exclude_arxml is None:
        args.exclude_arxml = []
    if not isinstance(args.exclude_arxml, list) or any(
        not isinstance(value, str) or not value.strip() for value in args.exclude_arxml
    ):
        parser.error("excludeArxml must be a list of non-empty strings")
    if args.in_place is None:
        args.in_place = False
    if args.dry_run is None:
        args.dry_run = False
    for name in ("strict_list", "in_place", "dry_run"):
        if not isinstance(getattr(args, name), bool):
            parser.error(f"{OPTION_KEYS[name]} must be true or false")
    if args.output_dir is not None:
        args.output_dir = Path(args.output_dir)
    selected_outputs = sum(
        value is not None and value is not False
        for value in (args.in_place, args.output_dir, args.dry_run)
    )
    if selected_outputs > 1:
        parser.error("Only one of inPlace, outputDir, and dryRun may be selected")
    return args


class ErrorCounter(logging.Handler):
    """统计本次运行记录的 ERROR，供最终退出码和写入保护使用。"""

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.count = 0

    def emit(self, record: logging.LogRecord) -> None:
        self.count += 1


def setup_logging(log_dir: Path, level_name: str) -> ErrorCounter:
    """同时向日志文件和控制台输出；普通信息走 stdout，警告和错误走 stderr。"""

    level = getattr(logging, level_name.upper())
    LOGGER.setLevel(level)
    LOGGER.propagate = False
    for handler in list(LOGGER.handlers):
        LOGGER.removeHandler(handler)
        handler.close()

    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{TOOL_NAME}_{datetime.now():%Y%m%d_%H%M%S_%f}_{os.getpid()}.log"
    file_handler = logging.FileHandler(log_file, mode="w", encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    )
    LOGGER.addHandler(file_handler)

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.addFilter(lambda record: record.levelno < logging.WARNING)
    LOGGER.addHandler(stdout_handler)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.WARNING)
    LOGGER.addHandler(stderr_handler)

    error_counter = ErrorCounter()
    LOGGER.addHandler(error_counter)
    LOGGER.info("Log file: %s", log_file)
    return error_counter


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


def read_sheet_rows(
    workbook_path: Path,
    sheet_name: str,
    expected_headers: Sequence[str],
    optional_headers: Sequence[str] = (),
) -> list[dict[str, str]]:
    """读取一张工作表，校验表头并返回非空数据行。

    只有表头而无数据行时返回空列表，因此工作簿可以只填 P-Port 或只填 R-Port。
    ``optional_headers`` 中的列允许为空，且允许整列缺失。列顺序任意；未知列、
    重复列或缺少必填列时记录 error 并跳过整张表。
    """
    # 需要读取合并区域信息，因此不能用 read_only 模式。
    workbook = load_workbook(workbook_path, data_only=False)
    try:
        if sheet_name not in workbook.sheetnames:
            LOGGER.error("Worksheet not found: %s", sheet_name)
            return []
        rows = sheet_values(workbook[sheet_name])
    finally:
        workbook.close()

    if not rows or not any(value is not None for value in rows[0]):
        return []

    required = list(expected_headers)
    optional = list(optional_headers)
    try:
        raw_headers = [
            normalized_cell(value, f"{sheet_name} header")
            for value in rows[0]
            if value is not None
        ]
    except ValueError as error:
        LOGGER.error("%s", clean_message(error))
        return []

    canonical = {name.casefold(): name for name in required + optional}
    headers = []
    for header in raw_headers:
        alias = HEADER_ALIASES.get(header.casefold(), header)
        if header.casefold() == "runnable" and alias.casefold() not in canonical:
            alias = next(
                (name for name in required + optional if name.startswith("Runnable ")),
                alias,
            )
        headers.append(canonical.get(alias.casefold(), alias))
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
        records.append(record)
    return records


def resolve_target_file(listed_path: str, project_dir: Path) -> Path | None:
    """把 ``Target ARXML`` 列转为绝对路径；文件不存在时返回 ``None``。"""

    listed = Path(listed_path.replace("\\", "/"))
    if listed.is_absolute():
        return None
    target = (project_dir / listed).resolve()
    return target if target.is_file() else None


def read_enumerations(
    workbook_path: Path, project_dir: Path
) -> list[EnumerationSpec]:
    """读取 ``Enumerations`` 表，合并同一枚举类型的多个取值。

    同一 ``Type Path`` 的各行必须填写相同的目标文件、基础类型和可选路径；
    取值必须是整数且不重复，文本标签也不能重复。任何冲突只记录 error 并跳过该行。
    """
    rows = read_sheet_rows(
        workbook_path,
        ENUMERATION_SHEET,
        (
            "Type Path",
            "Target ARXML",
            "Implementation Data Type Ref",
            "Value",
            "Label",
        ),
        ("Compu Method Ref", "Data Constr Ref"),
    )

    grouped: dict[str, tuple[dict[str, object], list[tuple[str, str]]]] = {}
    for row in rows:
        type_path = row["Type Path"]
        if not type_path.startswith("/") or type_path.count("/") < 2:
            LOGGER.error(
                "Skipped enumeration row: type path must be absolute and below a "
                "package: %s",
                type_path,
            )
            continue
        if not row["Implementation Data Type Ref"].startswith("/"):
            LOGGER.error(
                "Skipped enumeration %s: base type must be absolute: %s",
                type_path,
                row["Implementation Data Type Ref"],
            )
            continue
        try:
            value = str(int(row["Value"]))
        except ValueError:
            LOGGER.error(
                "Skipped enumeration %s: value must be an integer: %s",
                type_path,
                row["Value"],
            )
            continue

        target = resolve_target_file(row["Target ARXML"], project_dir)
        if target is None:
            LOGGER.error(
                "Skipped enumeration %s: target ARXML not found: %s",
                type_path,
                row["Target ARXML"],
            )
            continue

        header = {
            "target": target,
            "base_type": row["Implementation Data Type Ref"],
            "compu_method_path": row.get("Compu Method Ref", "")
            or default_enum_path(type_path, ENUM_COMPU_METHOD_PACKAGE),
            "data_constr_path": row.get("Data Constr Ref", "")
            or default_enum_path(
                type_path, ENUM_DATA_CONSTR_PACKAGE, ENUM_DATA_CONSTR_PREFIX
            ),
        }
        if type_path not in grouped:
            grouped[type_path] = (header, [])
        known, values = grouped[type_path]
        conflicting = [name for name, item in header.items() if known[name] != item]
        if conflicting:
            LOGGER.error(
                "Skipped enumeration row for %s: conflicting %s",
                type_path,
                ", ".join(sorted(conflicting)),
            )
            continue
        if any(value == known_value for known_value, _ in values):
            LOGGER.error(
                "Skipped duplicate value %s in enumeration %s", value, type_path
            )
            continue
        if any(row["Label"] == label for _, label in values):
            LOGGER.error(
                "Skipped duplicate label %s in enumeration %s",
                row["Label"],
                type_path,
            )
            continue
        values.append((value, row["Label"]))

    return [
        EnumerationSpec(
            path=path,
            target_file=cast(Path, header["target"]),
            base_type=cast(str, header["base_type"]),
            compu_method_path=cast(str, header["compu_method_path"]),
            data_constr_path=cast(str, header["data_constr_path"]),
            values=tuple(values),
        )
        for path, (header, values) in grouped.items()
        if values
    ]


def default_enum_path(type_path: str, package: str, prefix: str = "") -> str:
    """按数据类型路径推导计算方法或数据约束的默认路径。

    例如 ``/BcmSystem/ImplementationDataTypes/X`` 会得到
    ``/BcmSystem/CompuMethods/X`` 和 ``/BcmSystem/DataConstrs/dc_X``。
    """
    name = type_path.rsplit("/", maxsplit=1)[-1]
    parent = type_path.rsplit("/", maxsplit=1)[0].rsplit("/", maxsplit=1)[0]
    return f"{parent}/{package}/{prefix}{name}"


def read_arrays(
    workbook_path: Path, project_dir: Path
) -> list[ArraySpec]:
    """读取固定长度数组；每行定义一个 ``IMPLEMENTATION-DATA-TYPE``。"""

    rows = read_sheet_rows(
        workbook_path,
        ARRAY_SHEET,
        (
            "Type Path",
            "Target ARXML",
            "Element Name",
            "Element Type Ref",
            "Array Size",
            "Target ARXML",
        ),
        optional_headers=("Array Size Semantics",),
    )
    specs: list[ArraySpec] = []
    seen: set[str] = set()
    for row in rows:
        path = row["Type Path"]
        if not path.startswith("/") or path.count("/") < 2:
            LOGGER.error("Skipped array: Type Path must be below a package: %s", path)
            continue
        if path in seen:
            LOGGER.error("Skipped duplicate array Type Path: %s", path)
            continue
        if not row["Element Type Ref"].startswith("/"):
            LOGGER.error(
                "Skipped array %s: Element Type Ref must be absolute: %s",
                path,
                row["Element Type Ref"],
            )
            continue
        try:
            array_size = int(row["Array Size"])
        except ValueError:
            array_size = 0
        if array_size <= 0:
            LOGGER.error(
                "Skipped array %s: Array Size must be a positive integer: %s",
                path,
                row["Array Size"],
            )
            continue
        semantics = row.get("Array Size Semantics", "") or "FIXED-SIZE"
        semantics = semantics.upper()
        if semantics not in {"FIXED-SIZE", "VARIABLE-SIZE"}:
            LOGGER.error(
                "Skipped array %s: Array Size Semantics must be FIXED-SIZE or VARIABLE-SIZE: %s",
                path,
                semantics,
            )
            continue
        target = resolve_target_file(row["Target ARXML"], project_dir)
        if target is None:
            LOGGER.error(
                "Skipped array %s: Target ARXML not found: %s",
                path,
                row["Target ARXML"],
            )
            continue
        seen.add(path)
        specs.append(
            ArraySpec(
                path,
                target,
                row["Element Name"],
                row["Element Type Ref"],
                array_size,
                semantics,
            )
        )
    return specs


def read_structures(
    workbook_path: Path, project_dir: Path
) -> list[StructureSpec]:
    """读取结构体，并按 Type Path 合并多行成员。"""

    rows = read_sheet_rows(
        workbook_path,
        STRUCTURE_SHEET,
        ("Type Path", "Target ARXML", "Member Name", "Member Type Ref"),
    )
    grouped: dict[str, tuple[Path, list[tuple[str, str]]]] = {}
    for row in rows:
        path = row["Type Path"]
        if not path.startswith("/") or path.count("/") < 2:
            LOGGER.error("Skipped structure: Type Path must be below a package: %s", path)
            continue
        if not row["Member Type Ref"].startswith("/"):
            LOGGER.error(
                "Skipped structure %s: Member Type Ref must be absolute: %s",
                path,
                row["Member Type Ref"],
            )
            continue
        target = resolve_target_file(row["Target ARXML"], project_dir)
        if target is None:
            LOGGER.error(
                "Skipped structure %s: Target ARXML not found: %s",
                path,
                row["Target ARXML"],
            )
            continue
        if path not in grouped:
            grouped[path] = (target, [])
        known_target, members = grouped[path]
        if known_target != target:
            LOGGER.error("Skipped structure member for %s: conflicting Target ARXML", path)
            continue
        if any(name == row["Member Name"] for name, _ in members):
            LOGGER.error(
                "Skipped duplicate member %s in structure %s",
                row["Member Name"],
                path,
            )
            continue
        members.append((row["Member Name"], row["Member Type Ref"]))
    return [
        StructureSpec(path, target, tuple(members))
        for path, (target, members) in grouped.items()
        if members
    ]


def read_sr_interfaces(
    workbook_path: Path, project_dir: Path
) -> list[InterfaceSpec]:
    """读取 ``SR-Interfaces`` 表，合并同一接口的多个数据元素。

    同一 ``Interface Path`` 出现在不同 ``Target ARXML``、或同一接口内数据元素
    重名都视为配置错误，只记录 error 并跳过该行。
    """
    rows = read_sheet_rows(
        workbook_path,
        "SR-Interfaces",
        ("Interface Path", "Data Element Name", "Data Type Ref", "Target ARXML"),
        optional_headers=("Is Queued", "Is Service"),
    )

    grouped: dict[str, tuple[Path, list[tuple[str, str, bool]], bool]] = {}
    for row in rows:
        interface_path = row["Interface Path"]
        if not interface_path.startswith("/") or interface_path.count("/") < 2:
            LOGGER.error(
                "Skipped SR interface row: interface path must be absolute and "
                "below a package: %s",
                interface_path,
            )
            continue
        if not row["Data Type Ref"].startswith("/"):
            LOGGER.error(
                "Skipped SR interface %s: data type must be absolute: %s",
                interface_path,
                row["Data Type Ref"],
            )
            continue

        listed = Path(row["Target ARXML"].replace("\\", "/"))
        target = resolve_target_file(row["Target ARXML"], project_dir)
        if target is None:
            LOGGER.error(
                "Skipped SR interface %s: target ARXML not found: %s",
                interface_path,
                listed,
            )
            continue

        label = row.get("Is Queued", "")
        is_queued = BOOLEAN_LABELS.get(label.casefold(), None) if label else False
        if is_queued is None:
            LOGGER.error(
                "Skipped SR interface %s data element %s: unknown Is Queued %r, "
                "expected one of %s",
                interface_path,
                row["Data Element Name"],
                label,
                ", ".join(sorted(BOOLEAN_LABELS)),
            )
            continue
        service_label = row.get("Is Service", "")
        is_service = (
            BOOLEAN_LABELS.get(service_label.casefold(), None)
            if service_label
            else False
        )
        if is_service is None:
            LOGGER.error(
                "Skipped SR interface %s: unknown Is Service %r, expected Y or N",
                interface_path,
                service_label,
            )
            continue
        if interface_path not in grouped:
            grouped[interface_path] = (target, [], is_service)
        known_target, elements, known_is_service = grouped[interface_path]
        if known_target != target:
            LOGGER.error(
                "Skipped SR interface row for %s: conflicting target ARXML %s and %s",
                interface_path,
                known_target,
                target,
            )
            continue
        if known_is_service != is_service:
            LOGGER.error(
                "Skipped SR interface row for %s: conflicting Is Service values",
                interface_path,
            )
            continue
        if any(name == row["Data Element Name"] for name, _, _ in elements):
            LOGGER.error(
                "Skipped duplicate data element %s in SR interface %s",
                row["Data Element Name"],
                interface_path,
            )
            continue
        elements.append((row["Data Element Name"], row["Data Type Ref"], is_queued))

    return [
        InterfaceSpec(path, target, tuple(elements), is_service)
        for path, (target, elements, is_service) in grouped.items()
        if elements
    ]


def read_cs_interfaces(
    workbook_path: Path, project_dir: Path
) -> list[ClientServerInterfaceSpec]:
    """读取 CS 接口，并按接口和 operation 聚合参数。"""

    rows = read_sheet_rows(
        workbook_path,
        "CS-Interfaces",
        (
            "Interface Path",
            "Operation Name",
            "Argument Name",
            "Direction",
            "Data Type Ref",
            "Target ARXML",
        ),
        optional_headers=("Is Service",),
    )
    grouped: dict[
        str, tuple[Path, dict[str, list[tuple[str, str, str]]], bool]
    ] = {}
    for row in rows:
        interface_path = row["Interface Path"]
        if not interface_path.startswith("/") or interface_path.count("/") < 2:
            LOGGER.error(
                "Skipped CS interface row: Interface Path must be below a package: %s",
                interface_path,
            )
            continue
        if not row["Data Type Ref"].startswith("/"):
            LOGGER.error(
                "Skipped CS interface %s: Data Type Ref must be absolute: %s",
                interface_path,
                row["Data Type Ref"],
            )
            continue
        direction = row["Direction"].upper()
        if direction not in {"IN", "OUT", "INOUT"}:
            LOGGER.error(
                "Skipped CS interface %s argument %s: Direction must be IN, OUT or INOUT",
                interface_path,
                row["Argument Name"],
            )
            continue
        target = resolve_target_file(row["Target ARXML"], project_dir)
        if target is None:
            LOGGER.error(
                "Skipped CS interface %s: Target ARXML not found: %s",
                interface_path,
                row["Target ARXML"],
            )
            continue
        service_label = row.get("Is Service", "")
        is_service = (
            BOOLEAN_LABELS.get(service_label.casefold(), None)
            if service_label
            else False
        )
        if is_service is None:
            LOGGER.error(
                "Skipped CS interface %s: unknown Is Service %r, expected Y or N",
                interface_path,
                service_label,
            )
            continue
        if interface_path not in grouped:
            grouped[interface_path] = (target, {}, is_service)
        known_target, operations, known_is_service = grouped[interface_path]
        if known_target != target:
            LOGGER.error(
                "Skipped CS interface row for %s: conflicting Target ARXML",
                interface_path,
            )
            continue
        if known_is_service != is_service:
            LOGGER.error(
                "Skipped CS interface row for %s: conflicting Is Service values",
                interface_path,
            )
            continue
        arguments = operations.setdefault(row["Operation Name"], [])
        if any(name == row["Argument Name"] for name, _, _ in arguments):
            LOGGER.error(
                "Skipped duplicate argument %s in CS operation %s/%s",
                row["Argument Name"],
                interface_path,
                row["Operation Name"],
            )
            continue
        arguments.append((row["Argument Name"], row["Data Type Ref"], direction))

    return [
        ClientServerInterfaceSpec(
            path,
            target,
            tuple(
                (operation, tuple(arguments))
                for operation, arguments in operations.items()
            ),
            is_service,
        )
        for path, (target, operations, is_service) in grouped.items()
        if operations
    ]


def read_workbook(workbook_path: Path) -> tuple[dict[str, ComponentSpec], list[PortSpec]]:
    """读取三张表，并校验组件、端口定义之间的一致性。

    组件简称和路径均不允许重复；同一组件下的端口名在 P/R 两张表中合并去重，
    因为它们最终位于同一个 ``PORTS`` 容器。任何不合法的记录只记录 error 并跳过。
    """
    component_rows = read_sheet_rows(
        workbook_path, "Components", ("SWC", "Component Path")
    )
    components: dict[str, ComponentSpec] = {}
    paths: set[str] = set()
    for row in component_rows:
        key = row["SWC"]
        path = row["Component Path"]
        if not path.startswith("/"):
            LOGGER.error(
                "Skipped component %s: path must be absolute: %s", key, path
            )
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
    for sheet_name, direction, com_spec_headers in (
        (
            "P-Ports",
            "P",
            (
                "Create RTE Artifact",
                "Runnable Name",
                "Runnable Symbol",
                "Minimum Start Interval",
                "Can Be Invoked Concurrently",
                "Transmission Acknowledge Timeout",
                "Uses End To End Protection",
            ),
        ),
        (
            "R-Ports",
            "R",
            (
                "Create RTE Artifact",
                "Runnable Ref",
                "Receive Mode",
                "Enable Update",
                "Queue Length",
            ),
        ),
    ):
        rows = read_sheet_rows(
            workbook_path,
            sheet_name,
            ("SWC", "Port Name", "Port Interface Ref"),
            com_spec_headers,
        )
        for row in rows:
            component_key = row["SWC"]
            if component_key not in components:
                LOGGER.error(
                    "Skipped port %s in %s: SWC %s is not defined in the Components sheet",
                    row["Port Name"],
                    sheet_name,
                    component_key,
                )
                continue
            if not row["Port Interface Ref"].startswith("/"):
                LOGGER.error(
                    "Skipped port %s in %s: interface path must be absolute: %s",
                    row["Port Name"],
                    sheet_name,
                    row["Port Interface Ref"],
                )
                continue
            flags = read_com_spec_flags(row, sheet_name, direction)
            if flags is None:
                continue
            is_update, queue_length, ack_timeout, uses_protection = flags
            create_access = read_boolean_flag(
                row.get("Create RTE Artifact", ""),
                "Create RTE Artifact",
                row["Port Name"],
                sheet_name,
            )
            if create_access is None:
                continue
            receive_mode = row.get("Receive Mode", "")
            if receive_mode and receive_mode.casefold() not in {"argument", "value"}:
                LOGGER.error(
                    "Skipped port %s in %s: Receive Mode must be Argument or Value: %s",
                    row["Port Name"],
                    sheet_name,
                    receive_mode,
                )
                continue
            runnable_column = "Runnable Name" if direction == "P" else "Runnable Ref"
            if create_access and direction == "R" and not row.get(runnable_column, ""):
                LOGGER.error(
                    "Skipped port %s in %s: Runnable Ref is required when Create RTE Artifact is Y",
                    row["Port Name"],
                    sheet_name,
                )
                continue
            runnable_symbol = row.get("Runnable Symbol", "")
            minimum_start_interval = 0.0
            concurrent = False
            if direction == "P":
                interval_text = row.get("Minimum Start Interval", "")
                if interval_text:
                    try:
                        minimum_start_interval = float(interval_text)
                    except ValueError:
                        minimum_start_interval = -1.0
                    if minimum_start_interval < 0:
                        LOGGER.error(
                            "Skipped port %s in %s: Minimum Start Interval must be a non-negative number: %s",
                            row["Port Name"],
                            sheet_name,
                            interval_text,
                        )
                        continue
                concurrent_value = read_boolean_flag(
                    row.get("Can Be Invoked Concurrently", ""),
                    "Can Be Invoked Concurrently",
                    row["Port Name"],
                    sheet_name,
                )
                if concurrent_value is None:
                    continue
                concurrent = concurrent_value
            key = (component_key, row["Port Name"])
            if key in seen_ports:
                LOGGER.error(
                    "Skipped duplicate port for component %s: %s",
                    component_key,
                    row["Port Name"],
                )
                continue
            seen_ports.add(key)
            ports.append(
                PortSpec(
                    component_key=component_key,
                    name=row["Port Name"],
                    interface_path=row["Port Interface Ref"],
                    direction=direction,
                    is_update=is_update,
                    queue_length=queue_length,
                    ack_timeout=ack_timeout,
                    uses_protection=uses_protection,
                    runnable=row.get(runnable_column, ""),
                    create_access=create_access,
                    receive_by_value=receive_mode.casefold() == "value",
                    runnable_symbol=runnable_symbol,
                    minimum_start_interval=minimum_start_interval,
                    can_be_invoked_concurrently=concurrent,
                )
            )

    if not components:
        LOGGER.error("No usable components found in workbook")
    if not ports:
        LOGGER.error("No usable P-Port or R-Port records found in workbook")
    return components, ports


def read_boolean_flag(
    label: str, column: str, port_name: str, sheet_name: str
) -> bool | None:
    """解析简洁的 Y/N 标志；空值按 N 处理。"""

    value = BOOLEAN_LABELS.get(label.casefold()) if label else False
    if value is None:
        LOGGER.error(
            "Skipped port %s in %s: unknown %s %r, expected Y or N",
            port_name,
            sheet_name,
            column,
            label,
        )
    return value


def read_com_spec_flags(
    row: dict[str, str], sheet_name: str, direction: str
) -> tuple[bool, int, float | None, bool] | None:
    """解析端口行的 ComSpec 列；取值非法时记录 error 并返回 ``None``。

    P-Ports 和 R-Ports 两张表的 ComSpec 列不同，按 ``direction`` 分别解析。
    """

    if direction == "P":
        flags = read_sender_flags(row, sheet_name)
        return None if flags is None else (False, 0, flags[0], flags[1])
    flags = read_receiver_flags(row, sheet_name)
    return None if flags is None else (flags[0], flags[1], None, False)


def read_sender_flags(
    row: dict[str, str], sheet_name: str
) -> tuple[float | None, bool] | None:
    """解析 P-Port 的发送端 ComSpec 列：发送确认超时和 E2E 保护标志。"""

    port_name = row["Port Name"]
    text = row.get("Transmission Acknowledge Timeout", "")
    ack_timeout: float | None = None
    if text:
        try:
            ack_timeout = float(text)
        except ValueError:
            LOGGER.error(
                "Skipped port %s in %s: Transmission Acknowledge Timeout must be a "
                "non-negative number of seconds: %s",
                port_name,
                sheet_name,
                text,
            )
            return None
        if ack_timeout < 0:
            LOGGER.error(
                "Skipped port %s in %s: Transmission Acknowledge Timeout must be a "
                "non-negative number of seconds: %s",
                port_name,
                sheet_name,
                text,
            )
            return None

    label = row.get("Uses End To End Protection", "")
    uses_protection = BOOLEAN_LABELS.get(label.casefold(), None) if label else False
    if uses_protection is None:
        LOGGER.error(
            "Skipped port %s in %s: unknown Uses End To End Protection %r, "
            "expected one of %s",
            port_name,
            sheet_name,
            label,
            ", ".join(sorted(BOOLEAN_LABELS)),
        )
        return None
    return ack_timeout, uses_protection


def read_receiver_flags(
    row: dict[str, str], sheet_name: str
) -> tuple[bool, int] | None:
    """解析 R-Port 的接收端 ComSpec 列；两个列互斥。"""

    port_name = row["Port Name"]
    label = row.get("Enable Update", "")
    is_update = BOOLEAN_LABELS.get(label.casefold(), None) if label else False
    if is_update is None:
        LOGGER.error(
            "Skipped port %s in %s: unknown Enable Update %r, expected one of %s",
            port_name,
            sheet_name,
            label,
            ", ".join(sorted(BOOLEAN_LABELS)),
        )
        return None

    text = row.get("Queue Length", "")
    try:
        queue_length = int(text) if text else 0
    except ValueError:
        LOGGER.error(
            "Skipped port %s in %s: Queue Length must be a positive integer: %s",
            port_name,
            sheet_name,
            text,
        )
        return None
    if queue_length < 0:
        LOGGER.error(
            "Skipped port %s in %s: Queue Length must be a positive integer: %s",
            port_name,
            sheet_name,
            text,
        )
        return None

    if is_update and queue_length:
        LOGGER.error(
            "Skipped port %s in %s: Enable Update and Queue Length are mutually exclusive",
            port_name,
            sheet_name,
        )
        return None
    return is_update, queue_length


def read_data_accesses(
    workbook_path: Path, components: dict[str, ComponentSpec]
) -> list[AccessSpec]:
    """读取 ``Data-Accesses`` 表，返回逐条访问点请求。

    只做与 ARXML 无关的静态校验：组件必须在 ``Components`` 表中出现、
    ``Access Kind`` 必须是已知取值、同一端口的同一数据元素不能重复。
    数据元素、类别和名称留空表示交由后续解析阶段推导。
    """
    rows = read_sheet_rows(
        workbook_path,
        DATA_ACCESS_SHEET,
        ("SWC", "Runnable Ref", "Port Name"),
        ("Data Element Name", "Access Kind", "Access Name"),
    )

    specs: list[AccessSpec] = []
    seen: set[tuple[str, str, str, str]] = set()
    for row in rows:
        component_key = row["SWC"]
        if component_key not in components:
            LOGGER.error(
                "Skipped data access for port %s: SWC %s is not defined in the "
                "Components sheet",
                row["Port Name"],
                component_key,
            )
            continue

        label = row.get("Access Kind", "")
        kind = ACCESS_KIND_LABELS.get(label.casefold(), "") if label else ""
        if label and not kind:
            LOGGER.error(
                "Skipped data access %s/%s: unknown Access Kind %r, expected one of %s",
                row["Runnable Ref"],
                row["Port Name"],
                label,
                ", ".join(sorted(ACCESS_KIND_LABELS)),
            )
            continue

        unique = (
            component_key,
            row["Runnable Ref"],
            row["Port Name"],
            row.get("Data Element Name", ""),
        )
        if unique in seen:
            LOGGER.error(
                "Skipped duplicate data access row: %s / %s / %s / %s",
                *unique,
            )
            continue
        seen.add(unique)
        specs.append(
            AccessSpec(
                component_key=component_key,
                runnable=row["Runnable Ref"],
                port_name=row["Port Name"],
                data_element=row.get("Data Element Name", ""),
                kind=kind,
                name=row.get("Access Name", ""),
            )
        )
    return specs


def read_arxml_list(
    list_file: Path, project_dir: Path, strict: bool = False,
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
            excluded_paths.add((project_dir / excluded).resolve())
    files: list[Path] = []
    for line_number, line in enumerate(
        list_file.read_text(encoding="utf-8-sig").splitlines(), start=1
    ):
        listed_path = line.strip().strip('"')
        if not listed_path or listed_path.startswith("#"):
            continue
        path = Path(listed_path.replace("\\", "/"))
        absolute_path = (
            path if path.is_absolute() else project_dir / path
        ).resolve()
        if absolute_path.name.casefold() in excluded_names or absolute_path in excluded_paths:
            LOGGER.info("Exclude ARXML file: %s", absolute_path)
            continue
        if absolute_path.suffix.casefold() != ".arxml":
            message = f"Non-ARXML record at {list_file}:{line_number}"
            if strict:
                raise ValueError(message)
            LOGGER.warning("%s; skipped", message)
            continue
        if not absolute_path.is_file():
            message = (
                f"Listed ARXML not found at {list_file}:{line_number}: {absolute_path}"
            )
            if strict:
                raise FileNotFoundError(message)
            LOGGER.warning("%s; skipped", message)
            continue
        files.append(absolute_path)
    if not files:
        raise ValueError(f"No usable ARXML files found in {list_file}")
    return files


def candidate_files(
    arxml_files: Sequence[Path], short_name: str, store: SourceStore
) -> list[Path]:
    """按 ``SHORT-NAME`` 反向索引预筛候选文件，避免每次调用都重新扫描整份清单。"""

    return store.short_name_index(arxml_files).get(short_name, [])


def find_definition(
    backend: ArxmlBackend,
    arxml_files: Sequence[Path],
    autosar_path: str,
    kind: str,
    store: SourceStore,
) -> tuple[Path, str] | None:
    """在清单范围内定位一个 AUTOSAR 路径的唯一定义。

    ``kind`` 为 ``component`` 或 ``interface``；接口返回其 ``DEST``，组件返回空串。
    找不到或存在多个定义时记录 error 并返回 ``None``，由调用方跳过相关条目；
    单个候选文件的解析失败不中断流程，但会在错误信息中一并报告。
    """
    short_name = autosar_path.rsplit("/", maxsplit=1)[-1]
    matches: list[tuple[Path, str]] = []
    failures: list[str] = []
    for candidate in candidate_files(arxml_files, short_name, store):
        try:
            document, _ = store.load(backend, candidate)
        except Exception as error:
            failures.append(f"{candidate}: {clean_message(error)}")
            continue
        if kind == "component":
            if document.component_exists(autosar_path):
                matches.append((candidate, ""))
            continue
        try:
            dest = document.interface_dest(autosar_path)
        except ValueError as error:
            LOGGER.error("%s", clean_message(error))
            continue
        if dest is not None:
            matches.append((candidate, dest))

    if len(matches) == 1:
        return matches[0]
    if not matches:
        detail = f"; parser failures: {'; '.join(failures)}" if failures else ""
        LOGGER.error(
            "No %s found at %s in listed ARXML%s",
            kind,
            autosar_path,
            detail,
        )
        return None
    matched_files = ", ".join(str(path) for path, _ in matches)
    LOGGER.error("Multiple definitions found for %s: %s", autosar_path, matched_files)
    return None


def resolve_components(
    backend: ArxmlBackend,
    components: dict[str, ComponentSpec],
    arxml_files: Sequence[Path],
    store: SourceStore,
) -> dict[str, Path]:
    """定位每个组件所在的 ARXML；无法唯一定位的组件不会出现在结果中。"""

    component_files: dict[str, Path] = {}
    for component in components.values():
        found = find_definition(
            backend, arxml_files, component.path, "component", store
        )
        if found is None:
            continue
        component_files[component.key] = found[0]
    return component_files


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
        document, _ = store.load(backend, component_files[component_key])
        for port in ports:
            if port.component_key != component_key:
                continue
            existing = document.read_port(components[component_key].path, port.name)
            if existing is not None:
                existing_ports[(component_key, port.name)] = existing
    com_spec_interfaces = {
        port.interface_path
        for port in ports
        if port.needs_com_specs
        and (port.component_key, port.name) not in existing_ports
    }
    interface_dests: dict[str, str] = {}
    interface_elements: dict[str, list[str]] = {}
    for interface_path in sorted({port.interface_path for port in ports}):
        found = find_definition(
            backend, arxml_files, interface_path, "interface", store
        )
        if found is None:
            continue
        interface_dests[interface_path] = found[1]
        if interface_path in com_spec_interfaces:
            document, _ = store.load(backend, found[0])
            interface_elements[interface_path] = document.interface_data_elements(
                interface_path
            )

    resolved: list[ResolvedPort] = []
    for port in ports:
        if port.component_key not in component_files:
            LOGGER.error(
                "Skipped port %s: component %s is unresolved",
                port.name,
                components[port.component_key].path,
            )
            continue
        if port.interface_path not in interface_dests:
            LOGGER.error(
                "Skipped port %s: interface %s is unresolved",
                port.name,
                port.interface_path,
            )
            continue
        interface_dest = interface_dests[port.interface_path]
        if (
            port.create_access
            and port.direction == "P"
            and interface_dest == "SENDER-RECEIVER-INTERFACE"
            and not port.runnable
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
            if existing is not None:
                data_paths = tuple(dict.fromkeys(
                    [*existing.update_elements]
                    + [path for path, _ in existing.queued_elements]
                    + [path for path, _, _ in existing.sender_elements]
                ))
                if not data_paths or any(
                    path.rpartition("/")[0] != port.interface_path for path in data_paths
                ):
                    LOGGER.error(
                        "Port %s/%s already exists with missing or conflicting ComSpec "
                        "references; please choose a different port name",
                        components[port.component_key].path,
                        port.name,
                    )
                    continue
            else:
                elements = interface_elements.get(port.interface_path, [])
                if not elements:
                    LOGGER.error(
                        "Skipped port %s: ComSpec flags require data elements but "
                        "interface %s has none",
                        port.name,
                        port.interface_path,
                    )
                    continue
                data_paths = tuple(
                    f"{port.interface_path}/{element}" for element in elements
                )
            if port.direction == "P":
                sender_elements = tuple(
                    (data_path, port.ack_timeout, port.uses_protection)
                    for data_path in data_paths
                )
            elif port.is_update:
                update_elements = data_paths
            else:
                queued_elements = tuple(
                    (data_path, port.queue_length) for data_path in data_paths
                )

        resolved.append(
            ResolvedPort(
                spec=port,
                component_path=components[port.component_key].path,
                component_file=component_files[port.component_key],
                interface_dest=interface_dest,
                update_elements=update_elements,
                queued_elements=queued_elements,
                sender_elements=sender_elements,
            )
        )
    return resolved


class InterfaceElementCache:
    """按接口路径缓存数据元素列表，避免同一接口反复扫描清单。"""

    def __init__(
        self,
        backend: ArxmlBackend,
        arxml_files: Sequence[Path],
        store: SourceStore,
    ) -> None:
        self._backend = backend
        self._arxml_files = arxml_files
        self._store = store
        self._elements: dict[str, list[str]] = {}

    def elements(self, interface_path: str) -> list[str]:
        """返回接口的数据元素简称；接口不可解析时返回空列表。"""

        if interface_path in self._elements:
            return self._elements[interface_path]
        found = find_definition(
            self._backend, self._arxml_files, interface_path, "interface", self._store
        )
        if found is None:
            self._elements[interface_path] = []
            return []
        document, _ = self._store.load(self._backend, found[0])
        self._elements[interface_path] = document.interface_data_elements(
            interface_path
        )
        return self._elements[interface_path]


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

    interfaces = InterfaceElementCache(backend, arxml_files, store)
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
            data_element = resolve_data_element(item.spec, item.interface_path, interfaces)
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


def expand_port_accesses(
    backend: ArxmlBackend,
    ports: Sequence[ResolvedPort],
    arxml_files: Sequence[Path],
    store: SourceStore,
) -> list[AccessSpec]:
    """把端口行上的 Y 展开为 SR 接口全部数据元素的访问点请求。"""

    interfaces = InterfaceElementCache(backend, arxml_files, store)
    specs: list[AccessSpec] = []
    for port in ports:
        if not port.spec.create_access or port.interface_dest != "SENDER-RECEIVER-INTERFACE":
            continue
        if not port.spec.runnable:
            LOGGER.error(
                "Skipped automatic data access for %s: Runnable is required",
                port.port_path,
            )
            continue
        elements = interfaces.elements(port.spec.interface_path)
        if not elements:
            LOGGER.error(
                "Skipped automatic data access for %s: interface has no data elements",
                port.port_path,
            )
            continue
        kind = (
            ACCESS_SEND
            if port.spec.direction == "P"
            else ACCESS_RECEIVE_VALUE
            if port.spec.receive_by_value
            else ACCESS_RECEIVE
        )
        specs.extend(
            AccessSpec(
                component_key=port.spec.component_key,
                runnable=port.spec.runnable,
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
    planned = {
        (port.spec.component_key, port.spec.name): port for port in resolved_ports
    }
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
        document, _ = store.load(backend, component_file)

        for spec in component_specs:
            runnable_path = document.find_runnable(component_path, spec.runnable)
            if runnable_path is None:
                LOGGER.error(
                    "Skipped data access for port %s: runnable %s not found below %s",
                    spec.port_name,
                    spec.runnable,
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
                    access.name: access
                    for access in document.list_accesses(runnable_path, kind)
                }
            located.append(
                LocatedAccess(
                    spec=spec,
                    component_path=component_path,
                    component_file=component_file,
                    runnable_path=runnable_path,
                    kind=kind,
                    interface_path=interface_path,
                    name_exists=bool(spec.name) and document.element_exists(
                        f"{runnable_path}/{spec.name}"
                    ),
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


def resolve_data_element(
    spec: AccessSpec, interface_path: str, interfaces: InterfaceElementCache
) -> str | None:
    """确定访问点指向的数据元素；无法唯一确定时记录 error 并返回 ``None``。"""

    elements = interfaces.elements(interface_path)
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
            "Skipped data access for port %s: data element %s not found in "
            "interface %s, available: %s",
            spec.port_name,
            spec.data_element,
            interface_path,
            elements,
        )
        return None
    return spec.data_element


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
    if existing.update_elements != port.update_elements:
        raise ValueError(
            f"{port.port_path} conflicts with workbook: "
            f"Enable Update elements={list(existing.update_elements)}; "
            "please choose a different port name"
        )
    if existing.queued_elements != port.queued_elements:
        raise ValueError(
            f"{port.port_path} conflicts with workbook: "
            f"QueueLength elements={list(existing.queued_elements)}; "
            "please choose a different port name"
        )
    if existing.sender_elements != port.sender_elements:
        raise ValueError(
            f"{port.port_path} conflicts with workbook: "
            f"sender ComSpec elements={list(existing.sender_elements)}; "
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


def local_name(tag: str) -> str:
    """去掉 ``{namespace}`` 前缀，返回 XML 标签本地名。"""

    return tag.rsplit("}", maxsplit=1)[-1]


def component_element_name(source: bytes, component_path: str) -> str:
    """在目标文件中确定组件对应的 AUTOSAR 元类标签名。"""

    root = ET.fromstring(source)
    component_name = component_path.rsplit("/", maxsplit=1)[-1]
    matches = []
    for element in root.iter():
        if local_name(element.tag) not in SWC_ELEMENT_NAMES:
            continue
        short_name = next(
            (
                child.text
                for child in element
                if local_name(child.tag) == "SHORT-NAME"
            ),
            None,
        )
        if short_name == component_name:
            matches.append(local_name(element.tag))
    if len(matches) != 1:
        raise ValueError(
            f"Expected one SWC named {component_name} in target file, found {len(matches)}"
        )
    return matches[0]


def insert_at_offset(
    source: bytes,
    offset: int,
    fragments: Sequence[str],
) -> bytes:
    """在指定结束标签偏移处按原换行和缩进插入片段。

    结束标签通常独占一行，直接复用该行缩进。对形如 ``<TAG></TAG>`` 写在同一行的
    空容器，改用行首缩进并把结束标签挪到新的一行，而不是要求容器提前展开成多行。
    片段按 UTF-8 编码写入，因此枚举标签、端口名等允许使用中文。
    """

    line_start = source.rfind(b"\n", 0, offset) + 1
    prefix = source[line_start:offset]
    newline = b"\r\n" if b"\r\n" in source else b"\n"

    if not prefix.strip(b" \t"):
        base_indentation = prefix
        insert_offset = line_start
        trailing = b""
    else:
        stripped = prefix.lstrip(b" \t")
        base_indentation = prefix[: len(prefix) - len(stripped)]
        insert_offset = offset
        trailing = newline + base_indentation

    child_indentation = base_indentation + b"  "
    encoded_fragments = [
        newline.join(
            child_indentation + line.encode("utf-8") for line in fragment.splitlines()
        )
        for fragment in fragments
    ]
    insertion = newline.join(encoded_fragments) + newline
    return source[:insert_offset] + insertion + trailing + source[insert_offset:]


def insert_in_container(
    source: bytes,
    block_pattern: re.Pattern[bytes],
    container_tag: str,
    fragments: Sequence[str],
    context: str,
) -> bytes:
    """在指定块内的容器结束标签之前插入片段。"""

    matches = list(block_pattern.finditer(source))
    if len(matches) != 1:
        raise ValueError(f"Expected one block for {context}, found {len(matches)}")
    block = matches[0]
    closing = f"</{container_tag}>".encode("ascii")
    offset = source.find(closing, block.start(), block.end())
    if offset < 0:
        raise ValueError(f"{container_tag} container not found in {context}")
    return insert_at_offset(source, offset, fragments)


def named_block_pattern(element_name: str, short_name: str) -> re.Pattern[bytes]:
    """构造匹配“指定标签 + 指定 SHORT-NAME”整块的字节正则。"""

    encoded_element = re.escape(element_name.encode("ascii"))
    return re.compile(
        rb"<"
        + encoded_element
        + rb"\b[^>]*>\s*<SHORT-NAME>"
        + re.escape(short_name.encode("ascii"))
        + rb"</SHORT-NAME>.*?</"
        + encoded_element
        + rb">",
        re.DOTALL,
    )


def insert_ports(
    source: bytes,
    component_path: str,
    fragments: Sequence[str],
) -> bytes:
    """在组件 ``</PORTS>`` 之前按原换行和缩进插入新端口。

    整文件序列化会规范化原有 ARXML，因此这里以字节为单位只插入新节点，
    其余字节保持不变。
    """
    if not fragments:
        return source
    element_name = component_element_name(source, component_path)
    component_name = component_path.rsplit("/", maxsplit=1)[-1]
    return insert_in_container(
        source,
        named_block_pattern(element_name, component_name),
        "PORTS",
        fragments,
        component_path,
    )


def insert_accesses(
    source: bytes,
    runnable_path: str,
    container_tag: str,
    fragments: Sequence[str],
) -> bytes:
    """在 runnable 的访问点容器末尾插入 VARIABLE-ACCESS。"""

    if not fragments:
        return source
    runnable_name = runnable_path.rsplit("/", maxsplit=1)[-1]
    return insert_in_container(
        source,
        named_block_pattern("RUNNABLE-ENTITY", runnable_name),
        container_tag,
        fragments,
        runnable_path,
    )


def insert_or_create_child_container(
    source: bytes,
    element_name: str,
    short_name: str,
    container_tag: str,
    fragments: Sequence[str],
    following_tags: Sequence[str] = (),
) -> bytes:
    """向命名元素的子容器追加片段，容器缺失时在后续标签前创建。"""

    pattern = named_block_pattern(element_name, short_name)
    matches = list(pattern.finditer(source))
    if len(matches) != 1:
        raise ValueError(
            f"Expected one {element_name} named {short_name}, found {len(matches)}"
        )
    block = matches[0]
    closing = f"</{container_tag}>".encode("ascii")
    offset = source.find(closing, block.start(), block.end())
    if offset >= 0:
        return insert_at_offset(source, offset, fragments)

    candidates = [
        source.find(f"<{tag}".encode("ascii"), block.start(), block.end())
        for tag in following_tags
    ]
    candidates = [candidate for candidate in candidates if candidate >= 0]
    offset = min(candidates) if candidates else source.rfind(
        f"</{element_name}>".encode("ascii"), block.start(), block.end()
    )
    if offset < 0:
        raise ValueError(f"Unable to insert {container_tag} in {short_name}")
    wrapped = "\n".join(
        [f"<{container_tag}>"]
        + [f"  {line}" for fragment in fragments for line in fragment.splitlines()]
        + [f"</{container_tag}>"]
    )
    return insert_at_offset(source, offset, [wrapped])


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
        if port.spec.create_access and port.interface_dest == "CLIENT-SERVER-INTERFACE"
    ]
    operations_by_interface: dict[str, list[str]] = {}
    for interface_path in sorted({port.spec.interface_path for port in requested}):
        found = find_definition(backend, arxml_files, interface_path, "interface", store)
        if found is None:
            operations_by_interface[interface_path] = []
            continue
        document, _ = store.load(backend, found[0])
        operations_by_interface[interface_path] = document.interface_operations(
            interface_path
        )

    by_file: dict[Path, list[ResolvedPort]] = defaultdict(list)
    for port in requested:
        by_file[port.component_file].append(port)

    created = 0
    skipped = 0
    for component_file, component_ports in by_file.items():
        document, _ = store.load(backend, component_file)
        source = store.read(component_file)
        call_fragments: dict[str, list[str]] = defaultdict(list)
        event_fragments: dict[str, list[str]] = defaultdict(list)
        runnable_fragments: dict[str, list[str]] = defaultdict(list)

        for port in component_ports:
            operations = operations_by_interface.get(port.spec.interface_path, [])
            if not operations:
                LOGGER.error(
                    "Skipped CS artifacts for %s: interface has no operations",
                    port.port_path,
                )
                skipped += 1
                continue
            behavior_path = document.internal_behavior_path(port.component_path)
            if behavior_path is None:
                LOGGER.error(
                    "Skipped CS artifacts for %s: component has no internal behavior",
                    port.port_path,
                )
                skipped += 1
                continue

            for operation in operations:
                operation_path = f"{port.spec.interface_path}/{operation}"
                if port.spec.direction == "R":
                    runnable_path = document.find_runnable(
                        port.component_path, port.spec.runnable
                    )
                    if runnable_path is None:
                        LOGGER.error(
                            "Skipped CS call for %s: runnable %s not found",
                            port.port_path,
                            port.spec.runnable,
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
                        document.add_server_call(
                            runnable_path, name, port.port_path, operation_path
                        )
                    )
                    created += 1
                    continue

                runnable_name = port.spec.runnable or f"RE_{port.spec.name}_{operation}"
                if port.spec.runnable and len(operations) > 1:
                    runnable_name = f"{port.spec.runnable}_{operation}"
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
                    runnable_path = f"{behavior_path}/{runnable_name}"
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
                        behavior_path,
                        event_name,
                        runnable_path,
                        port.port_path,
                        operation_path,
                    )
                )
                created += 1

        for runnable_path, fragments in call_fragments.items():
            source = insert_or_create_child_container(
                source,
                "RUNNABLE-ENTITY",
                runnable_path.rsplit("/", 1)[-1],
                "SERVER-CALL-POINTS",
                fragments,
                ("SYMBOL",),
            )
        for behavior_path, fragments in event_fragments.items():
            source = insert_or_create_child_container(
                source,
                "SWC-INTERNAL-BEHAVIOR",
                behavior_path.rsplit("/", 1)[-1],
                "EVENTS",
                fragments,
                ("EXCLUSIVE-AREAS", "RUNNABLES"),
            )
        for behavior_path, fragments in runnable_fragments.items():
            source = insert_or_create_child_container(
                source,
                "SWC-INTERNAL-BEHAVIOR",
                behavior_path.rsplit("/", 1)[-1],
                "RUNNABLES",
                fragments,
                ("SERVICE-DEPENDENCIES",),
            )
        if call_fragments or event_fragments or runnable_fragments:
            with tempfile.NamedTemporaryFile(suffix=".arxml", delete=False) as temporary:
                temporary.write(source)
                temporary_path = Path(temporary.name)
            try:
                LOGGER.info("Re-parsing ARXML for validation: %s", component_file)
                backend.load(temporary_path)
            finally:
                temporary_path.unlink(missing_ok=True)
            store.write(component_file, source)
    return created, skipped


def container_exists(
    source: bytes,
    block_pattern: re.Pattern[bytes],
    container_tag: str,
) -> bool:
    """判断块内是否存在目标容器，用于插入前预检。"""

    matches = list(block_pattern.finditer(source))
    if len(matches) != 1:
        return False
    block = matches[0]
    closing = f"</{container_tag}>".encode("ascii")
    return source.find(closing, block.start(), block.end()) >= 0


def port_container_ready(source: bytes, component_path: str) -> bool:
    """确认组件块和 ``PORTS`` 容器均可定位。"""

    try:
        element_name = component_element_name(source, component_path)
    except ValueError as error:
        LOGGER.error("%s", clean_message(error))
        return False
    component_name = component_path.rsplit("/", maxsplit=1)[-1]
    if not container_exists(
        source, named_block_pattern(element_name, component_name), "PORTS"
    ):
        LOGGER.error("PORTS container not found for %s", component_path)
        return False
    return True


def access_container_ready(
    source: bytes, runnable_path: str, container_tag: str
) -> bool:
    """确认 runnable 块和访问点容器均可定位。"""

    runnable_name = runnable_path.rsplit("/", maxsplit=1)[-1]
    if not container_exists(
        source, named_block_pattern("RUNNABLE-ENTITY", runnable_name), container_tag
    ):
        LOGGER.error("%s container not found in %s", container_tag, runnable_path)
        return False
    return True


class SourceStore:
    """保存已修改但尚未写盘的 ARXML 字节。

    新建接口后端口阶段需要能查到它，因此后续读取和解析都走这里的缓存，
    使 dry-run 也能验证完整链路。
    """

    def __init__(self) -> None:
        self._buffers: dict[Path, bytes] = {}
        self._short_name_index: dict[str, list[Path]] | None = None
        self._short_name_index_key: tuple[Path, ...] | None = None

    def read(self, path: Path) -> bytes:
        return self._buffers.get(path, b"") or path.read_bytes()

    def write(self, path: Path, document: bytes) -> None:
        self._buffers[path] = document
        self._short_name_index = None
        self._short_name_index_key = None

    def modified(self) -> dict[Path, bytes]:
        return dict(self._buffers)

    def short_name_index(self, arxml_files: Sequence[Path]) -> dict[str, list[Path]]:
        """返回 ``SHORT-NAME`` -> 所在文件列表的反向索引，按需构建一次并缓存。

        任何 :meth:`write` 都会使索引失效，下次查找时重新扫描，
        避免 ``candidate_files`` 每次调用都重新读取并正则匹配全部文件。
        """

        key = tuple(arxml_files)
        if self._short_name_index is not None and self._short_name_index_key == key:
            return self._short_name_index
        index: dict[str, list[Path]] = defaultdict(list)
        for arxml_file in arxml_files:
            try:
                text = self.read(arxml_file).decode("utf-8-sig")
            except UnicodeDecodeError:
                continue
            for match in SHORT_NAME_PATTERN.finditer(text):
                index[match.group(1)].append(arxml_file)
        self._short_name_index = dict(index)
        self._short_name_index_key = key
        return self._short_name_index

    def load(self, backend: ArxmlBackend, path: Path):
        """按缓存内容加载文档；未修改的文件直接从磁盘加载。"""

        if path not in self._buffers:
            LOGGER.info("Parsing ARXML: %s", path)
            return backend.load(path)
        LOGGER.info("Parsing ARXML (with pending changes): %s", path)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".arxml", delete=False) as temporary:
                temporary.write(self._buffers[path])
                temporary_path = Path(temporary.name)
            return backend.load(temporary_path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)


def package_elements_span(source: bytes, package_path: str) -> int | None:
    """返回包内 ``</ELEMENTS>`` 的偏移；无法唯一定位时返回 ``None``。

    AUTOSAR 要求 ``ELEMENTS`` 排在 ``AR-PACKAGES`` 之前，因此从包的 ``SHORT-NAME``
    往后找到的第一个 ``ELEMENTS`` 就属于本包；若中间先遇到子包则说明本包没有。
    """
    package_name = package_path.rsplit("/", maxsplit=1)[-1]
    anchor = f"<SHORT-NAME>{package_name}</SHORT-NAME>".encode("ascii")
    positions = [
        match.start() for match in re.finditer(re.escape(anchor), source)
    ]
    if len(positions) != 1:
        return None

    start = source.find(b"<ELEMENTS>", positions[0])
    if start < 0:
        return None
    if b"<AR-PACKAGE>" in source[positions[0] : start] or b"<AR-PACKAGE " in source[
        positions[0] : start
    ]:
        return None

    depth = 0
    cursor = start
    while cursor >= 0:
        opening = source.find(b"<ELEMENTS>", cursor)
        closing = source.find(b"</ELEMENTS>", cursor)
        if closing < 0:
            return None
        if 0 <= opening < closing:
            depth += 1
            cursor = opening + len(b"<ELEMENTS>")
            continue
        depth -= 1
        if depth == 0:
            return closing
        cursor = closing + len(b"</ELEMENTS>")
    return None


def package_block_end(source: bytes, package_path: str) -> int | None:
    """返回包内可追加子元素的位置：``<AR-PACKAGES>`` 之前或 ``</AR-PACKAGE>`` 之前。"""

    package_name = package_path.rsplit("/", maxsplit=1)[-1]
    anchor = f"<SHORT-NAME>{package_name}</SHORT-NAME>".encode("ascii")
    positions = [match.start() for match in re.finditer(re.escape(anchor), source)]
    if len(positions) != 1:
        return None
    start = source.rfind(b"<AR-PACKAGE", 0, positions[0])
    if start < 0:
        return None

    # ``<AR-PACKAGES>`` 也以 ``<AR-PACKAGE`` 开头，用前瞻区分开始标签。
    token = re.compile(rb"<AR-PACKAGE(?=[\s>])|</AR-PACKAGE>")
    depth = 0
    end = None
    for match in token.finditer(source, start):
        if match.group().startswith(b"</"):
            depth -= 1
            if depth == 0:
                end = match.start()
                break
        else:
            depth += 1
    if end is None:
        return None

    children = source.find(b"<AR-PACKAGES>", start, end)
    return children if children >= 0 else end


def wrap_in_elements(fragment: str) -> str:
    """把片段包进一个新的 ``<ELEMENTS>`` 容器，供空包使用。"""

    lines = ["  " + line for line in fragment.splitlines()]
    return "\n".join(["<ELEMENTS>"] + lines + ["</ELEMENTS>"])


def insert_package_element(
    store: SourceStore,
    target_file: Path,
    package_path: str,
    fragment: str,
    context: str,
) -> bool:
    """把元素插入包的 ``ELEMENTS`` 容器；包内还没有该容器时一并创建。"""

    source = store.read(target_file)
    offset = package_elements_span(source, package_path)
    if offset is None:
        offset = package_block_end(source, package_path)
        if offset is None:
            LOGGER.error(
                "Skipped %s: unable to locate package %s in %s",
                context,
                package_path,
                target_file,
            )
            return False
        fragment = wrap_in_elements(fragment)
    store.write(target_file, insert_at_offset(source, offset, [fragment]))
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
        document, diagnostics = store.load(backend, spec.target_file)
        for diagnostic in diagnostics:
            LOGGER.warning("%s: %s: %s", backend.name, spec.target_file, diagnostic)

        lower, upper = spec.limits
        elements = (
            (
                spec.compu_method_path,
                lambda package, name: document.add_compu_method(
                    package, name, spec.values
                ),
                "COMPU-METHOD",
                document.compu_method_definition,
                ("TEXTTABLE", tuple((value, value, label) for value, label in spec.values)),
            ),
            (
                spec.path,
                lambda package, name: document.add_enum_data_type(
                    package,
                    name,
                    spec.base_type,
                    spec.compu_method_path,
                    spec.data_constr_path,
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
                lambda package, name: document.add_data_constr(
                    package, name, lower, upper
                ),
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
                    "Skipped %s: package %s not found in %s",
                    context,
                    package_path,
                    spec.target_file,
                )
                failed = True
                break
            try:
                fragment = build_fragment(package_path, name)
            except ValueError as error:
                LOGGER.error("Skipped %s: %s", context, clean_message(error))
                failed = True
                break
            if not insert_package_element(
                store, spec.target_file, package_path, fragment, context
            ):
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
        document, diagnostics = store.load(backend, spec.target_file)
        for diagnostic in diagnostics:
            LOGGER.warning("%s: %s: %s", backend.name, spec.target_file, diagnostic)
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
                fragment = document.add_structure_data_type(
                    spec.package_path,
                    spec.name,
                    spec.members,
                )
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
    interfaces: Sequence[InterfaceSpec],
    store: SourceStore,
) -> tuple[list[str], int]:
    """创建尚不存在的 SR 接口，并把结果字节写入缓存。

    已存在时比较数据元素名称、类型引用、队列标志和服务标志，不修改已有定义。
    任何不一致都记录 error 并跳过该接口，由主流程阻止全部 ARXML 写盘。
    返回新建接口路径列表和被跳过的数量。
    """
    created: list[str] = []
    skipped = 0
    for spec in interfaces:
        document, diagnostics = store.load(backend, spec.target_file)
        for diagnostic in diagnostics:
            LOGGER.warning("%s: %s: %s", backend.name, spec.target_file, diagnostic)

        expected = list(spec.data_elements)
        if document.element_exists(spec.path):
            if not check_existing_definition(
                f"SR interface {spec.path} in {spec.target_file}",
                (
                    document.interface_dest(spec.path),
                    document.sender_receiver_definition(spec.path),
                    document.interface_is_service(spec.path),
                ),
                ("SENDER-RECEIVER-INTERFACE", expected, spec.is_service),
            ):
                skipped += 1
            continue

        if not document.package_exists(spec.package_path):
            LOGGER.error(
                "Skipped SR interface %s: package %s not found in %s",
                spec.path,
                spec.package_path,
                spec.target_file,
            )
            skipped += 1
            continue

        try:
            fragment = document.add_sr_interface(
                spec.package_path, spec.name, spec.data_elements, spec.is_service
            )
        except ValueError as error:
            LOGGER.error(
                "Skipped SR interface %s: %s", spec.path, clean_message(error)
            )
            skipped += 1
            continue
        if not insert_package_element(
            store,
            spec.target_file,
            spec.package_path,
            fragment,
            f"SR interface {spec.path}",
        ):
            skipped += 1
            continue

        created.append(spec.path)
        LOGGER.info(
            "create SR interface %s with %d data element(s) in %s",
            spec.path,
            len(spec.data_elements),
            spec.target_file,
        )
    return created, skipped


def apply_cs_interfaces(
    backend: ArxmlBackend,
    interfaces: Sequence[ClientServerInterfaceSpec],
    store: SourceStore,
) -> tuple[list[str], int]:
    """创建尚不存在的 CS 接口，并完整比较已有 operation 和参数定义。"""

    created: list[str] = []
    skipped = 0
    for spec in interfaces:
        document, diagnostics = store.load(backend, spec.target_file)
        for diagnostic in diagnostics:
            LOGGER.warning("%s: %s: %s", backend.name, spec.target_file, diagnostic)

        expected = [
            (operation, list(arguments))
            for operation, arguments in spec.operations
        ]
        if document.element_exists(spec.path):
            if not check_existing_definition(
                f"CS interface {spec.path} in {spec.target_file}",
                (
                    document.interface_dest(spec.path),
                    document.client_server_definition(spec.path),
                    document.interface_is_service(spec.path),
                ),
                ("CLIENT-SERVER-INTERFACE", expected, spec.is_service),
            ):
                skipped += 1
            continue
        if not document.package_exists(spec.package_path):
            LOGGER.error(
                "Skipped CS interface %s: package %s not found in %s",
                spec.path,
                spec.package_path,
                spec.target_file,
            )
            skipped += 1
            continue
        try:
            fragment = document.add_cs_interface(
                spec.package_path,
                spec.name,
                spec.operations,
                spec.is_service,
            )
        except ValueError as error:
            LOGGER.error(
                "Skipped CS interface %s: %s", spec.path, clean_message(error)
            )
            skipped += 1
            continue
        if not insert_package_element(
            store,
            spec.target_file,
            spec.package_path,
            fragment,
            f"CS interface {spec.path}",
        ):
            skipped += 1
            continue
        created.append(spec.path)
        LOGGER.info(
            "create CS interface %s with %d operation(s) in %s",
            spec.path,
            len(spec.operations),
            spec.target_file,
        )
    return created, skipped


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
    for source_file in list(ports_by_file) + [
        path for path in accesses_by_file if path not in ports_by_file
    ]:
        document, diagnostics = store.load(backend, source_file)
        for diagnostic in diagnostics:
            LOGGER.warning("%s: %s: %s", backend.name, source_file, diagnostic)

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
                else:
                    if not port_container_ready(source, port.component_path):
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
                    check_port(
                        document.read_port(port.component_path, port.spec.name), port
                    )
                    fragments_by_component[port.component_path].append(fragment)
                    created_ports.append(port)
                    checked_ports.append(port)
            except ValueError as error:
                LOGGER.error("Skipped port %s: %s", port.port_path, clean_message(error))
                skipped += 1
                continue

        for access in accesses_by_file.get(source_file, []):
            container = access.container
            try:
                existing_access = document.read_access(
                    access.runnable_path, access.name, access.kind
                )
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
                if not access_container_ready(source, access.runnable_path, container):
                    continue
                access_fragment = document.add_access(
                    access.runnable_path,
                    access.name,
                    access.kind,
                    access.port_path,
                    access.data_path,
                )
                check_access(
                    document.read_access(
                        access.runnable_path, access.name, access.kind
                    ),
                    access,
                )
            except ValueError as error:
                LOGGER.error(
                    "Skipped data access %s: %s",
                    access.access_path,
                    clean_message(error),
                )
                continue
            fragments_by_runnable[(access.runnable_path, container)].append(
                access_fragment
            )
            created_accesses.append(access.access_path)
            checked_accesses.append(access)
            LOGGER.info(
                "create %s %s -> %s",
                container,
                access.access_path,
                access.data_path,
            )

        for component_path, fragments in fragments_by_component.items():
            source = insert_ports(source, component_path, fragments)
        for (runnable_path, container), fragments in fragments_by_runnable.items():
            source = insert_accesses(source, runnable_path, container, fragments)
        LOGGER.info("Re-parsing ARXML for validation: %s", source_file)
        validate_document(backend, source, checked_ports, checked_accesses)
        store.write(source_file, source)
        changes.append(
            FileChange(
                source_file, source, created_ports, existing_ports, created_accesses
            )
        )
    return changes, skipped


def validate_document(
    backend: ArxmlBackend,
    document: bytes,
    expected_ports: Sequence[ResolvedPort],
    expected_accesses: Sequence[ResolvedAccess],
) -> None:
    """把修改后的字节写入临时文件，确认后端能完整读回已处理的内容。"""

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".arxml", delete=False) as temporary:
            temporary.write(document)
            temporary_path = Path(temporary.name)
        check_document, diagnostics = backend.load(temporary_path)
        for diagnostic in diagnostics:
            LOGGER.warning("%s validation: %s", backend.name, diagnostic)
        for port in expected_ports:
            check_port(
                check_document.read_port(port.component_path, port.spec.name), port
            )
        for access in expected_accesses:
            check_access(
                check_document.read_access(
                    access.runnable_path, access.name, access.kind
                ),
                access,
            )
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def output_path(source_file: Path, output_dir: Path, project_dir: Path) -> Path:
    """把仓库内的源文件映射到输出目录下的相同相对路径。"""

    try:
        relative_path = source_file.relative_to(project_dir)
    except ValueError as error:
        raise ValueError(f"Target ARXML is outside repository: {source_file}") from error
    return output_dir / relative_path


def write_changes(
    modified: dict[Path, bytes],
    in_place: bool,
    output_dir: Path | None,
    project_dir: Path,
) -> None:
    """写出字节发生变化的文件；无实际修改时不触碰任何文件。"""

    if not in_place and output_dir is None:
        raise ValueError("Output directory is required when not writing in place")
    resolved_output_dir = None if output_dir is None else output_dir.resolve()
    pending: list[tuple[Path, bytes, bytes | None]] = []
    for source_file, document in modified.items():
        if document == source_file.read_bytes():
            continue
        if in_place:
            destination = source_file
        else:
            assert resolved_output_dir is not None
            destination = output_path(source_file, resolved_output_dir, project_dir)
        pending.append(
            (destination, document, destination.read_bytes() if destination.is_file() else None)
        )

    temporary_files: list[Path] = []
    replaced: list[tuple[Path, bytes | None]] = []
    try:
        for destination, document, original in pending:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(document)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            temporary_files.append(temporary_path)

        for (destination, _document, original), temporary_path in zip(
            pending, temporary_files
        ):
            os.replace(temporary_path, destination)
            replaced.append((destination, original))
            LOGGER.info("ARXML written to: %s", destination)
    except OSError:
        for destination, original in reversed(replaced):
            if original is None:
                destination.unlink(missing_ok=True)
                continue
            with tempfile.NamedTemporaryFile(
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".restore.tmp",
                delete=False,
            ) as temporary:
                temporary.write(original)
                temporary.flush()
                os.fsync(temporary.fileno())
                restore_path = Path(temporary.name)
            os.replace(restore_path, destination)
        raise
    finally:
        for temporary_path in temporary_files:
            temporary_path.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    """命令入口：运行失败时记录错误并返回 1。"""

    try:
        return run(argv)
    except (OSError, UnicodeError, ValueError, RuntimeError) as error:
        LOGGER.error("error: %s", clean_message(error))
        return 1


def run(argv: Sequence[str] | None = None) -> int:
    """读取工作簿和清单、应用并验证端口，最后按请求写出文件。"""

    args = parse_args(argv)
    error_counter = setup_logging(args.log_dir.resolve(), args.log_level)
    project_dir = args.project_dir.resolve()
    workbook_path = args.workbook.resolve()
    list_file = (
        args.list_file if args.list_file.is_absolute() else project_dir / args.list_file
    ).resolve()
    if not project_dir.is_dir():
        raise NotADirectoryError(f"Project directory not found: {project_dir}")
    if not workbook_path.is_file():
        raise FileNotFoundError(f"Workbook not found: {workbook_path}")
    if not list_file.is_file():
        raise FileNotFoundError(f"ARXML list not found: {list_file}")

    backend = get_backend(args.backend)
    LOGGER.info("Workbook: %s", workbook_path)
    LOGGER.info("Project directory: %s", project_dir)
    LOGGER.info("ARXML list: %s", list_file)
    LOGGER.info("Backend: %s", backend.name)
    LOGGER.info("Reading workbook configuration")
    components, ports = read_workbook(workbook_path)
    enumerations = read_enumerations(workbook_path, project_dir)
    arrays = read_arrays(workbook_path, project_dir)
    structures = read_structures(workbook_path, project_dir)
    interfaces = read_sr_interfaces(workbook_path, project_dir)
    cs_interfaces = read_cs_interfaces(workbook_path, project_dir)
    access_specs = read_data_accesses(workbook_path, components)
    arxml_files = read_arxml_list(list_file, project_dir, args.strict_list, args.exclude_arxml)
    LOGGER.info("Loaded %d ARXML file(s) from list", len(arxml_files))
    store = SourceStore()
    LOGGER.info("Applying type and interface definitions")
    created_enumerations, skipped_enumerations = apply_enumerations(
        backend, enumerations, store
    )
    created_composite_types, skipped_composite_types = apply_composite_types(
        backend, arrays, structures, store
    )
    created_interfaces, skipped_interfaces = apply_interfaces(
        backend, interfaces, store
    )
    created_cs_interfaces, skipped_cs_interfaces = apply_cs_interfaces(
        backend, cs_interfaces, store
    )
    component_files = resolve_components(backend, components, arxml_files, store)
    LOGGER.info("Resolving components and ports")
    resolved_ports = resolve_ports(
        backend, components, ports, component_files, arxml_files, store
    )
    access_specs.extend(
        expand_port_accesses(backend, resolved_ports, arxml_files, store)
    )
    resolved_accesses = resolve_accesses(
        backend,
        components,
        access_specs,
        resolved_ports,
        component_files,
        arxml_files,
        store,
    )
    changes, skipped_while_applying = build_changes(
        backend, resolved_ports, resolved_accesses, store
    )
    LOGGER.info("Applying client-server artifacts")
    created_cs_artifacts, skipped_cs_artifacts = apply_client_server_artifacts(
        backend, resolved_ports, arxml_files, store
    )

    created = [port for change in changes for port in change.created_ports]
    existing = [port for change in changes for port in change.existing_ports]
    accesses = [access for change in changes for access in change.created_accesses]
    for port in created:
        LOGGER.info(
            "create %s-Port %s -> %s (%s)",
            port.spec.direction,
            port.port_path,
            port.spec.interface_path,
            port.interface_dest,
        )
    LOGGER.info(
        "Validated %d of %d requested ports: %d to create, %d already valid, "
        "%d skipped; %d enumeration(s) to create, %d skipped; "
        "%d array/structure type(s) to create, %d skipped; "
        "%d SR interface(s) to create, %d skipped; "
        "%d CS interface(s) to create, %d skipped; "
        "%d of %d data access(es) to create; %d CS artifact(s) to create, %d skipped; "
        "%d ARXML file(s) affected.",
        len(created) + len(existing),
        len(ports),
        len(created),
        len(existing),
        len(ports) - len(resolved_ports) + skipped_while_applying,
        len(created_enumerations),
        skipped_enumerations,
        len(created_composite_types),
        skipped_composite_types,
        len(created_interfaces),
        skipped_interfaces,
        len(created_cs_interfaces),
        skipped_cs_interfaces,
        len(accesses),
        len(access_specs),
        created_cs_artifacts,
        skipped_cs_artifacts,
        len(store.modified()),
    )

    error_count = error_counter.count
    if error_count:
        LOGGER.error(
            "Validation failed with %d error(s); no ARXML files were written",
            error_count,
        )
        return 1

    if args.in_place or args.output_dir is not None:
        write_changes(store.modified(), args.in_place, args.output_dir, project_dir)
    else:
        LOGGER.info(
            "Dry run successful; use --in-place or --output-dir to write ARXML"
        )
    return 0


