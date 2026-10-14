"""表格转 SWC ARXML 工具：根据 Excel 工作簿向指定组件或 ARXML 批量添加端口、接口和数据类型。

工作簿格式见 :mod:`.workbook`，参数与配置见 :mod:`.options`。工具只在 ``--arxml-file-list``
列出的 ARXML 中查找组件和接口；新元素以 XML 片段插入原文件字节（:mod:`.xml_patch`），
其余内容保持不变，再重新解析验证。默认只验证，``--in-place`` 覆盖源文件，``--output-dir``
按 projectRoot 内的相对路径写到其他目录。任一 ERROR 都阻止写出全部 ARXML。

ARXML 的解析和序列化全部通过 :mod:`.lib.arxml_backend` 完成，更换解析库只需在该模块新增后端。
运行日志写入 ``logDir`` 下的 ``table_to_swc_arxml_<时间>_<进程号>.log``，同时输出到控制台。
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Sequence

from .apply import (
    apply_client_server_artifacts,
    apply_composite_types,
    apply_enumerations,
    apply_interfaces,
    build_changes,
)
from .lib.arxml_backend import clean_message, get_backend
from .options import parse_args
from .resolve import expand_port_accesses, read_arxml_list, resolve_accesses, resolve_components, resolve_ports
from .store import SourceStore
from .workbook import (
    load_workbook_sheets,
    read_arrays,
    read_cs_interfaces,
    read_data_accesses,
    read_enumerations,
    read_sr_interfaces,
    read_structures,
    read_workbook,
)
from .writer import write_changes

TOOL_NAME = "table_to_swc_arxml"
# 各模块使用 ``logging.getLogger(__name__)``，都是本包 logger 的子 logger，日志统一由这里输出。
LOGGER = logging.getLogger(__package__)


class ErrorCounter(logging.Handler):
    """统计本次运行记录的 ERROR，供最终退出码和写入保护使用。"""

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.count = 0

    def emit(self, record: logging.LogRecord) -> None:
        self.count += 1


def setup_logging(log_dir: Path, level_name: str) -> ErrorCounter:
    """同时向日志文件和控制台输出；普通信息走 stdout，警告和错误走 stderr。"""

    LOGGER.setLevel(getattr(logging, level_name))
    LOGGER.propagate = False
    for handler in list(LOGGER.handlers):
        LOGGER.removeHandler(handler)
        handler.close()

    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{TOOL_NAME}_{datetime.now():%Y%m%d_%H%M%S_%f}_{os.getpid()}.log"
    file_handler = logging.FileHandler(log_file, mode="w", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
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


def main(argv: Sequence[str] | None = None) -> int:
    """命令入口：运行失败时记录错误并返回 1。"""

    try:
        return run(argv)
    except (OSError, UnicodeError, ValueError, RuntimeError) as error:
        LOGGER.error("%s", clean_message(error))
        return 1


def run(argv: Sequence[str] | None = None) -> int:
    """读取工作簿和清单、应用并验证全部请求，最后按请求写出文件。"""

    options = parse_args(argv)
    error_counter = setup_logging(options.log_dir, options.log_level)
    project_root = options.project_root
    if not project_root.is_dir():
        raise NotADirectoryError(f"Project directory not found: {project_root}")
    if not options.workbook.is_file():
        raise FileNotFoundError(f"Workbook not found: {options.workbook}")
    if not options.arxml_file_list.is_file():
        raise FileNotFoundError(f"ARXML list not found: {options.arxml_file_list}")

    backend = get_backend(options.backend)
    LOGGER.info("Workbook: %s", options.workbook)
    LOGGER.info("Project directory: %s", project_root)
    LOGGER.info("ARXML list: %s", options.arxml_file_list)
    LOGGER.info("Backend: %s", backend.name)
    LOGGER.info("Reading workbook configuration")
    sheets = load_workbook_sheets(options.workbook)
    components, ports = read_workbook(sheets)
    enumerations = read_enumerations(sheets, project_root)
    arrays = read_arrays(sheets, project_root)
    structures = read_structures(sheets, project_root)
    interfaces = read_sr_interfaces(sheets, project_root)
    cs_interfaces = read_cs_interfaces(sheets, project_root)
    access_specs = read_data_accesses(sheets, components)
    if not any((ports, enumerations, arrays, structures, interfaces, cs_interfaces, access_specs)):
        LOGGER.error("No usable records found in workbook")
    arxml_files = read_arxml_list(
        options.arxml_file_list, project_root, options.strict_list, options.exclude_arxml
    )
    LOGGER.info("Loaded %d ARXML file(s) from list", len(arxml_files))
    store = SourceStore()
    LOGGER.info("Applying type and interface definitions")
    created_enumerations, skipped_enumerations = apply_enumerations(backend, enumerations, store)
    created_composite_types, skipped_composite_types = apply_composite_types(
        backend, arrays, structures, store
    )
    created_interfaces, skipped_interfaces = apply_interfaces(backend, interfaces, store)
    created_cs_interfaces, skipped_cs_interfaces = apply_interfaces(backend, cs_interfaces, store)
    component_files = resolve_components(backend, components, arxml_files, store)
    LOGGER.info("Resolving components and ports")
    resolved_ports = resolve_ports(backend, components, ports, component_files, arxml_files, store)
    access_specs.extend(expand_port_accesses(backend, resolved_ports, arxml_files, store))
    resolved_accesses = resolve_accesses(
        backend, components, access_specs, resolved_ports, component_files, arxml_files, store
    )
    changes, skipped_while_applying = build_changes(backend, resolved_ports, resolved_accesses, store)
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

    if error_counter.count:
        LOGGER.error("Validation failed with %d error(s); no ARXML files were written", error_counter.count)
        return 1

    if options.in_place or options.output_dir is not None:
        write_changes(store.modified(), options.in_place, options.output_dir, project_root)
    else:
        LOGGER.info("Dry run successful; use --in-place or --output-dir to write ARXML")
    return 0
