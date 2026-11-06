"""SWC 工具的处理流程：读取工作簿和清单，依次应用类型、接口、端口、访问点和 CS 对象，并汇总结果。

所有修改都先写入 :class:`SourceStore` 的内存缓存，后面的步骤读取的是前面步骤修改后的内容；
是否写盘由入口根据 ERROR 数量和输出选项决定。本模块导入 armodel，入口须先完成依赖检查。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from ..common.arxml_list import ArxmlExclusions, read_arxml_list
from ..common.paths import path_key
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
from .resolve import (
    ResolvedPort,
    expand_port_accesses,
    resolve_accesses,
    resolve_components,
    resolve_operation_ports,
    resolve_ports,
)
from .store import SourceStore
from .workbook import (
    load_workbook_sheets,
    read_arrays,
    read_cs_interfaces,
    read_data_accesses,
    read_enumerations,
    read_operations,
    read_sr_interfaces,
    read_structures,
    read_workbook,
    warn_case_variant_paths,
)

LOGGER = logging.getLogger(__name__)


@dataclass
class RunSummary:
    """一次运行各步骤的统计，用于输出汇总日志；``arxml_files`` 为查找范围内的全部 ARXML（清单在前）。"""

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
    arxml_files: list[Path] = field(default_factory=list)

    @property
    def planned_objects(self) -> int:
        return len(self.planned_ports) + len(self.planned_accesses) + sum(
            len(result.planned) for result in (
                self.enumerations, self.composite_types, self.sr_interfaces, self.cs_interfaces, self.cs_artifacts,
            ))

    def log(self, will_write: bool = True) -> None:
        """``will_write`` 为假（预演或有 ERROR）时用 “would create”，避免误以为已经生成。"""

        verb = "to create" if will_write else "would create"
        for port in self.planned_ports:
            LOGGER.info(
                "Prepared %s-Port %s -> %s (%s) in memory",
                port.spec.direction, port.port_path, port.spec.interface_path, port.interface_dest,
            )
        LOGGER.info(
            "Validated %d of %d requested ports: %d %s, %d already valid, %d skipped",
            len(self.planned_ports) + len(self.existing_ports), self.requested_ports,
            len(self.planned_ports), verb, len(self.existing_ports), self.skipped_ports,
        )
        for label, result in (
            ("Enumerations", self.enumerations),
            ("Array/structure types", self.composite_types),
            ("SR interfaces", self.sr_interfaces),
            ("CS interfaces", self.cs_interfaces),
            ("CS artifacts", self.cs_artifacts),
        ):
            LOGGER.info("%s: %d %s, %d skipped", label, len(result.planned), verb, result.skipped)
        LOGGER.info("Data accesses: %d of %d %s, %d skipped",
                    len(self.planned_accesses), self.requested_accesses, verb, self.skipped_accesses)
        LOGGER.info("%d ARXML file(s) affected", self.affected_files)

    def rows(self, created: str) -> list[tuple[str, str]]:
        """结果工作簿汇总页的统计行；``created`` 为新建对象的说明（新建/将新建/可新建）。"""

        lines = [(
            "端口",
            f"请求 {self.requested_ports}：{created} {len(self.planned_ports)}，"
            f"已存在 {len(self.existing_ports)}，跳过 {self.skipped_ports}",
        )]
        for label, result in (
            ("枚举类型", self.enumerations),
            ("数组/结构类型", self.composite_types),
            ("SR 接口", self.sr_interfaces),
            ("CS 接口", self.cs_interfaces),
            ("CS 对象（Runnable/事件/调用点）", self.cs_artifacts),
        ):
            lines.append((label, f"{created} {len(result.planned)}，跳过 {result.skipped}"))
        lines.append((
            "数据访问点",
            f"请求 {self.requested_accesses}：{created} {len(self.planned_accesses)}，跳过 {self.skipped_accesses}",
        ))
        lines.append(("涉及的 ARXML 文件", str(self.affected_files)))
        return lines


def _is_within(path: Path, directory: Path) -> bool:
    resolved, root = path_key(path), path_key(directory)
    try:
        return os.path.commonpath([resolved, root]) == root
    except ValueError:
        return False  # Windows 上位于不同驱动器


def exclude_output_files(arxml_files: Sequence[Path], output_dir: Path, project_root: Path) -> list[Path]:
    """去掉位于输出目录中的清单文件。

    输出目录在 projectRoot 内、清单用 ``**`` 等通配符时，上次运行的输出会被当作输入。输出目录包含整个
    projectRoot 时不排除，由写出前的路径重合检查报错。
    """

    if _is_within(project_root, output_dir):
        return list(arxml_files)
    kept: list[Path] = []
    for path in arxml_files:
        if _is_within(path, output_dir):
            LOGGER.info("Exclude ARXML file under outputDir: %s", path)
        else:
            kept.append(path)
    if not kept:
        raise ValueError(f"No usable ARXML files found outside outputDir {output_dir}")
    return kept


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
    operation_specs = read_operations(sheets, components)
    if not any((ports, enumerations, arrays, structures, interfaces, cs_interfaces, access_specs, operation_specs)):
        LOGGER.error("No usable records found in workbook")
    arxml_files = read_arxml_list(
        options.arxml_file_list, project_root,
        exclusions=ArxmlExclusions(project_root, options.exclude_arxml), strict=options.strict_list,
    )
    if options.output_dir is not None:
        arxml_files = exclude_output_files(arxml_files, options.output_dir, project_root)

    store = SourceStore([
        *arxml_files,
        *(spec.target_file for spec in [*enumerations, *arrays, *structures, *interfaces, *cs_interfaces]),
    ])
    lookup_files = _lookup_files(arxml_files, [spec.target_file for spec in [*interfaces, *cs_interfaces]])
    summary.arxml_files = lookup_files
    warn_case_variant_paths([
        *((path, "enumeration") for spec in enumerations
          for path in (spec.path, spec.compu_method_path, spec.data_constr_path)),
        *((spec.path, "array") for spec in arrays),
        *((spec.path, "structure") for spec in structures),
        *((spec.path, "SR interface") for spec in interfaces),
        *((spec.path, "CS interface") for spec in cs_interfaces),
    ])
    # 前一步成功创建的类型已在缓存中，可直接查到；只有同一批次中尚未写回的类型才按计划处理，
    # 失败的类型因此不会被后续步骤当作存在。
    composite_types = {spec.path: IMPLEMENTATION_DATA_TYPE for spec in [*arrays, *structures]}
    LOGGER.info("Applying type and interface definitions")
    summary.enumerations = apply_enumerations(enumerations, store, {
        **{spec.path: IMPLEMENTATION_DATA_TYPE for spec in enumerations}, **composite_types,
    })
    summary.composite_types = apply_composite_types(arrays, structures, store, composite_types)
    summary.sr_interfaces = apply_interfaces(interfaces, store)
    summary.cs_interfaces = apply_interfaces(cs_interfaces, store)

    LOGGER.info("Resolving components and ports")
    component_files = resolve_components(components, arxml_files, store)
    resolved_ports = resolve_ports(components, ports, component_files, lookup_files, store)
    port_changes, port_result = build_changes(resolved_ports, [], store)
    # 只有成功准备或已存在的端口才继续生成 RTE 对象；失败的端口请求不再派生新的 ERROR。
    usable_ports = [port for change in port_changes for port in (*change.planned_ports, *change.existing_ports)]
    usable_keys = {(port.spec.component_key, port.spec.name) for port in usable_ports}
    blocked_ports = set(getattr(ports, "rejected", ())) | {
        (port.component_key, port.name) for port in ports
    } - usable_keys

    LOGGER.info("Applying client-server artifacts")
    operation_ports = resolve_operation_ports(
        components, operation_specs, usable_ports, component_files, lookup_files, store, blocked_ports,
    )
    summary.cs_artifacts = apply_client_server_artifacts(usable_ports, lookup_files, store, operation_ports)

    access_specs.extend(expand_port_accesses(usable_ports, lookup_files, store))
    resolved_accesses, merged_accesses = resolve_accesses(
        components, access_specs, usable_ports, component_files, lookup_files, store, blocked_ports,
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


def _lookup_files(arxml_files: Sequence[Path], targets: Sequence[Path]) -> list[Path]:
    """接口查找范围：清单文件在前，再加本工作簿新建接口所在、但不在清单中的 Target ARXML。

    这样端口可以直接引用本次新建的接口，不必另外把目标文件加入清单。
    """

    files = list(arxml_files)
    known = {path_key(path) for path in files}
    for target in targets:
        if path_key(target) not in known:
            known.add(path_key(target))
            files.append(target)
            LOGGER.info("Interface lookup also uses Target ARXML not in the list: %s", target)
    return files
