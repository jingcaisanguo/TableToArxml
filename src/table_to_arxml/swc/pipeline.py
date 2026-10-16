"""SWC 工具的处理流程：读取工作簿和清单，依次应用类型、接口、端口、访问点和 CS 对象，并汇总结果。

所有修改都先写入 :class:`SourceStore` 的内存缓存，后面的步骤读取的是前面步骤修改后的内容；
是否写盘由入口根据 ERROR 数量和输出选项决定。本模块导入 armodel，入口须先完成依赖检查。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..common.arxml_list import ArxmlExclusions, read_arxml_list
from .apply import (
    StepResult,
    apply_client_server_artifacts,
    apply_composite_types,
    apply_enumerations,
    apply_interfaces,
    build_changes,
)
from .options import Options
from .resolve import ResolvedPort, expand_port_accesses, resolve_accesses, resolve_components, resolve_ports
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

LOGGER = logging.getLogger(__name__)


@dataclass
class RunSummary:
    """一次运行各步骤的统计，用于输出汇总日志。"""

    requested_ports: int = 0
    created_ports: list[ResolvedPort] = field(default_factory=list)
    existing_ports: list[ResolvedPort] = field(default_factory=list)
    skipped_ports: int = 0
    enumerations: StepResult = field(default_factory=StepResult)
    composite_types: StepResult = field(default_factory=StepResult)
    sr_interfaces: StepResult = field(default_factory=StepResult)
    cs_interfaces: StepResult = field(default_factory=StepResult)
    requested_accesses: int = 0
    created_accesses: list[str] = field(default_factory=list)
    cs_artifacts: StepResult = field(default_factory=StepResult)
    affected_files: int = 0

    def log(self) -> None:
        for port in self.created_ports:
            LOGGER.info(
                "create %s-Port %s -> %s (%s)",
                port.spec.direction, port.port_path, port.spec.interface_path, port.interface_dest,
            )
        LOGGER.info(
            "Validated %d of %d requested ports: %d to create, %d already valid, %d skipped",
            len(self.created_ports) + len(self.existing_ports), self.requested_ports,
            len(self.created_ports), len(self.existing_ports), self.skipped_ports,
        )
        for label, result in (
            ("Enumerations", self.enumerations),
            ("Array/structure types", self.composite_types),
            ("SR interfaces", self.sr_interfaces),
            ("CS interfaces", self.cs_interfaces),
            ("CS artifacts", self.cs_artifacts),
        ):
            LOGGER.info("%s: %d to create, %d skipped", label, len(result.created), result.skipped)
        LOGGER.info("Data accesses: %d of %d to create", len(self.created_accesses), self.requested_accesses)
        LOGGER.info("%d ARXML file(s) affected", self.affected_files)


def execute(options: Options) -> tuple[RunSummary, SourceStore]:
    """执行全部处理步骤，返回统计和保存修改结果的缓存；错误只记入日志，不中断后续步骤。"""

    project_root = options.project_root
    summary = RunSummary()
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
        options.arxml_file_list, project_root,
        exclusions=ArxmlExclusions(project_root, options.exclude_arxml), strict=options.strict_list,
    )

    store = SourceStore([
        *arxml_files,
        *(spec.target_file for spec in [*enumerations, *arrays, *structures, *interfaces, *cs_interfaces]),
    ])
    LOGGER.info("Applying type and interface definitions")
    summary.enumerations = apply_enumerations(enumerations, store)
    summary.composite_types = apply_composite_types(arrays, structures, store)
    summary.sr_interfaces = apply_interfaces(interfaces, store)
    summary.cs_interfaces = apply_interfaces(cs_interfaces, store)

    LOGGER.info("Resolving components and ports")
    component_files = resolve_components(components, arxml_files, store)
    resolved_ports = resolve_ports(components, ports, component_files, arxml_files, store)
    access_specs.extend(expand_port_accesses(resolved_ports, arxml_files, store))
    resolved_accesses = resolve_accesses(
        components, access_specs, resolved_ports, component_files, arxml_files, store
    )
    changes, port_result = build_changes(resolved_ports, resolved_accesses, store)

    LOGGER.info("Applying client-server artifacts")
    summary.cs_artifacts = apply_client_server_artifacts(resolved_ports, arxml_files, store)

    summary.requested_ports = len(ports)
    summary.created_ports = [port for change in changes for port in change.created_ports]
    summary.existing_ports = [port for change in changes for port in change.existing_ports]
    summary.skipped_ports = len(ports) - len(resolved_ports) + port_result.skipped
    summary.requested_accesses = len(access_specs)
    summary.created_accesses = [access for change in changes for access in change.created_accesses]
    summary.affected_files = len(store.modified())
    return summary, store
