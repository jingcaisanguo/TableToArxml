"""SWC 工具的处理流程：读取工作簿和清单，依次应用类型、接口、端口、访问点和 CS 对象，并汇总结果。

所有修改都先写入 :class:`SourceStore` 的内存缓存，后面的步骤读取的是前面步骤修改后的内容；
是否写盘由入口根据 ERROR 数量和输出选项决定。本模块导入 armodel，入口须先完成依赖检查。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..common.arxml_list import ArxmlExclusions, read_arxml_list
from .apply import (
    IMPLEMENTATION_DATA_TYPE,
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
    case_conflicting_paths,
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
    planned_ports: list[ResolvedPort] = field(default_factory=list)
    existing_ports: list[ResolvedPort] = field(default_factory=list)
    skipped_ports: int = 0
    enumerations: StepResult = field(default_factory=StepResult)
    composite_types: StepResult = field(default_factory=StepResult)
    sr_interfaces: StepResult = field(default_factory=StepResult)
    cs_interfaces: StepResult = field(default_factory=StepResult)
    requested_accesses: int = 0
    planned_accesses: list[str] = field(default_factory=list)
    skipped_accesses: int = 0
    cs_artifacts: StepResult = field(default_factory=StepResult)
    affected_files: int = 0

    def log(self) -> None:
        for port in self.planned_ports:
            LOGGER.info(
                "Prepared %s-Port %s -> %s (%s) in memory",
                port.spec.direction, port.port_path, port.spec.interface_path, port.interface_dest,
            )
        LOGGER.info(
            "Validated %d of %d requested ports: %d to create, %d already valid, %d skipped",
            len(self.planned_ports) + len(self.existing_ports), self.requested_ports,
            len(self.planned_ports), len(self.existing_ports), self.skipped_ports,
        )
        for label, result in (
            ("Enumerations", self.enumerations),
            ("Array/structure types", self.composite_types),
            ("SR interfaces", self.sr_interfaces),
            ("CS interfaces", self.cs_interfaces),
            ("CS artifacts", self.cs_artifacts),
        ):
            LOGGER.info("%s: %d to create, %d skipped", label, len(result.planned), result.skipped)
        LOGGER.info("Data accesses: %d of %d to create, %d skipped",
                    len(self.planned_accesses), self.requested_accesses, self.skipped_accesses)
        LOGGER.info("All counts describe in-memory changes, not files written")
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
    conflicts = case_conflicting_paths([
        *((path, "enumeration") for spec in enumerations
          for path in (spec.path, spec.compu_method_path, spec.data_constr_path)),
        *((spec.path, "array") for spec in arrays),
        *((spec.path, "structure") for spec in structures),
        *((spec.path, "SR interface") for spec in interfaces),
        *((spec.path, "CS interface") for spec in cs_interfaces),
    ])
    if conflicts:
        enumerations = [
            spec for spec in enumerations
            if not conflicts & {spec.path, spec.compu_method_path, spec.data_constr_path}
        ]
        arrays = [spec for spec in arrays if spec.path not in conflicts]
        structures = [spec for spec in structures if spec.path not in conflicts]
        interfaces = [spec for spec in interfaces if spec.path not in conflicts]
        cs_interfaces = [spec for spec in cs_interfaces if spec.path not in conflicts]
    planned_types = {
        spec.path: IMPLEMENTATION_DATA_TYPE for spec in [*enumerations, *arrays, *structures]
    }
    LOGGER.info("Applying type and interface definitions")
    summary.enumerations = apply_enumerations(enumerations, store, planned_types)
    summary.composite_types = apply_composite_types(arrays, structures, store, planned_types)
    summary.sr_interfaces = apply_interfaces(interfaces, store, planned_types)
    summary.cs_interfaces = apply_interfaces(cs_interfaces, store, planned_types)

    LOGGER.info("Resolving components and ports")
    component_files = resolve_components(components, arxml_files, store)
    resolved_ports = resolve_ports(components, ports, component_files, arxml_files, store)
    port_changes, port_result = build_changes(resolved_ports, [], store)
    # 只有成功准备或已存在的端口才继续生成 RTE 对象。
    usable_ports = [port for change in port_changes for port in (*change.planned_ports, *change.existing_ports)]

    LOGGER.info("Applying client-server artifacts")
    summary.cs_artifacts = apply_client_server_artifacts(usable_ports, arxml_files, store)

    access_specs.extend(expand_port_accesses(usable_ports, arxml_files, store))
    resolved_accesses, merged_accesses = resolve_accesses(
        components, access_specs, usable_ports, component_files, arxml_files, store
    )
    access_changes, access_result = build_changes([], resolved_accesses, store)
    changes = [*port_changes, *access_changes]

    summary.requested_ports = len(ports)
    summary.planned_ports = [port for change in changes for port in change.planned_ports]
    summary.existing_ports = [port for change in changes for port in change.existing_ports]
    summary.skipped_ports = len(ports) - len(resolved_ports) + port_result.skipped
    summary.requested_accesses = len(access_specs) - merged_accesses
    summary.planned_accesses = [access for change in changes for access in change.planned_accesses]
    summary.skipped_accesses = (
        summary.requested_accesses - len(resolved_accesses) + access_result.skipped_accesses
    )
    summary.affected_files = len(store.modified())
    return summary, store
