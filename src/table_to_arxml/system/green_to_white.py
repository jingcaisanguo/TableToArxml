"""绿表 -> 白表（System Excel）转换。

绿表中的端口/信号关系写入白表的 Components、AssemblySwConnection、SenderReceiverToSignal 三页：
- 写入前去重，多个绿表从两端声明的同一连接只写一次；
- 普通组件连接写入 AssemblySwConnection，CAN/LIN 直连写入 SenderReceiverToSignal；
- 配置的 E2E wrapper 按普通组件处理，只参与 Components/AssemblySwConnection。
"""

from __future__ import annotations

import logging
from pathlib import Path

from openpyxl import Workbook

from ..common import report
from ..common.report import Source
from .arxml_model import ArxmlModel
from .values import norm
from .component_map import ComponentTypeMap, build_component_type_map
from .general import resolve_general
from .green_table import R_PORT_SHEET, P_PORT_SHEET, GreenRow, GreenWorkbookInfo
from .white_table import (
    ASSEMBLY_SHEET, COMPONENTS_SHEET, SIGNAL_SHEET, create_white_workbook, format_white_workbook,
)

LOGGER = logging.getLogger(__name__)

BUS_MODULES = {"CAN", "LIN"}
MULTI_VALUE_SEPARATOR = "#"


def load_channel_sets(model: ArxmlModel) -> dict[str, frozenset[str]]:
    """从已加载的 ARXML 中读取各总线类型的物理通道短名集合，仅用于提示拼写问题。"""

    channels = model.channels()
    return {
        module_name: frozenset(name for name, modules in channels.items() if module_name in modules)
        for module_name in BUS_MODULES
    }


def _split_multi_value(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(MULTI_VALUE_SEPARATOR) if item and item.strip()]


def _build_signal_path(channel: str, signal: str, template: str) -> str:
    """把通道名和信号名拼成白表 SenderReceiverToSignal 所需的全路径。"""

    if signal.startswith("/"):
        return signal
    return template.replace("${channel}", channel).replace("${signal}", signal)


def _is_bus_row(row: GreenRow, e2e_wrapper: str) -> bool:
    """CAN/LIN 行是 app 端口与总线信号的映射，不是组件连线；E2E wrapper 除外。"""

    return row.module.upper() in BUS_MODULES and row.peer != e2e_wrapper


def _existing_rows(ws, width: int) -> dict[tuple[str, ...], int]:
    """读取白表页中已有记录的前 ``width`` 列，返回 ``记录 -> Excel 行号``。"""

    return {
        tuple(norm(cell) for cell in row[:width]): number
        for number, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2)
        if row and len(row) >= width and any(norm(cell) for cell in row[:width])
    }


def _append_unique(ws, existing_rows: dict[tuple[str, ...], int], row_key: tuple[str, ...]) -> tuple[int, bool]:
    """字段齐全且尚未存在时追加一行；返回记录所在行号（字段不全时为 0）和是否实际写入。"""

    if not all(row_key):
        return 0, False
    if row_key in existing_rows:
        return existing_rows[row_key], False
    ws.append(list(row_key))
    existing_rows[row_key] = ws.max_row
    return ws.max_row, True


def _append_component(
    ws, existing_rows: dict[tuple[str, ...], int], prototype: str, component_type: str, action: str,
) -> bool:
    """向 Components 页追加一行组件定义，返回是否实际写入。"""

    row_key = (prototype, prototype, component_type, action)
    if row_key in existing_rows:
        return False
    if any(row[0] == prototype for row in existing_rows):
        raise ValueError(f"Conflicting component prototype '{prototype}'")
    ws.append(list(row_key))
    existing_rows[row_key] = ws.max_row
    return True


def process_green_table(
    info: GreenWorkbookInfo,
    component_type_map: ComponentTypeMap,
    app_names: set[str],
    channel_sets: dict[str, frozenset[str]],
    workbook: Workbook,
    e2e_wrapper: str,
    signal_path_template: str,
    model: ArxmlModel,
    errors: list[str],
    white_book: str = "",
) -> dict[str, int]:
    """把一个绿表写入白表工作簿；出错的组件或行记入 ``errors`` 后继续处理其余内容。

    先补 Components：绿表定义的 app 写成 Add，其他被引用组件写成 Reference；
    再依次处理 R-Port、P-Port 页：普通组件写 AssemblySwConnection，CAN/LIN 写 SenderReceiverToSignal。
    写出或合并到的白表行关联到产生它的绿表行（``white_book`` 为白表的来源标识），生成结果会同时显示在绿表行上。
    """

    components_ws = workbook[COMPONENTS_SHEET]
    assembly_ws = workbook[ASSEMBLY_SHEET]
    signal_ws = workbook[SIGNAL_SHEET]
    existing_components = _existing_rows(components_ws, 4)
    existing_assemblies = _existing_rows(assembly_ws, 4)
    existing_signals = _existing_rows(signal_ws, 5)

    def fail(location: str, error: Exception, sources=()) -> None:
        errors.append(f"{location}: {error}")
        LOGGER.error("%s", errors[-1], extra=report.located(sources))

    counters = {"components": 0, "assembly": 0, "signal": 0}
    app_component_info = component_type_map.resolve(info.app_name)
    if not app_component_info:
        raise ValueError(
            f"Cannot find the SW component type of green table '{info.app_name}' in the "
            "loaded ARXML files")
    app_prototype, app_component_type = app_component_info
    app_definitions = {component_type_map.resolve(name) for name in app_names}

    peer_sources: dict[str, list[Source]] = {}
    for row in (*info.r_port_rows, *info.p_port_rows):
        if not _is_bus_row(row, e2e_wrapper):
            peer_sources.setdefault(row.peer, []).append(row.source)
    for name, component_type, action in [(app_prototype, app_component_type, "Add")] + [
            (name, "", "") for name in sorted(peer_sources)]:
        try:
            if action != "Add":
                component_info = component_type_map.resolve(name)
                if not component_info:
                    raise ValueError(f"unresolved component '{name}'")
                name, component_type = component_info
                action = "Add" if component_info in app_definitions else "Reference"
            if _append_component(components_ws, existing_components, name, component_type, action):
                counters["components"] += 1
        except ValueError as exc:
            fail(f"Green table '{info.app_name}'", exc, peer_sources.get(name, ()))

    for sheet, rows, direction in (
        (R_PORT_SHEET, info.r_port_rows, "IN"),
        (P_PORT_SHEET, info.p_port_rows, "OUT"),
    ):
        for row in rows:
            try:
                if _is_bus_row(row, e2e_wrapper):
                    module_name = row.module.upper()
                    channels = _split_multi_value(row.peer)
                    if not channels or not row.peer_port:
                        raise ValueError("channel or signal is empty")
                    for channel in channels:
                        signal_path = _build_signal_path(channel, row.peer_port, signal_path_template)
                        # 浅校验只要求最终 SystemSignal 存在；通道名仅在参与拼接路径时提示。
                        if (not row.peer_port.startswith("/") and "${channel}" in signal_path_template
                                and channel not in channel_sets[module_name]):
                            LOGGER.warning(
                                "%s %s row %s: channel '%s' is not a %s physical channel in the loaded ARXML",
                                info.app_name, sheet, row.excel_row, channel, module_name,
                                extra=report.located([row.source]))
                        model.validate_signal_mapping(signal_path)
                        # DataElement 优先取绿表 H 列，未填时与 app 端口同名。
                        signal_key = (signal_path, direction, app_prototype, row.app_port,
                                      row.data_element or row.app_port)
                        number, appended = _append_unique(signal_ws, existing_signals, signal_key)
                        counters["signal"] += appended
                        if number:
                            report.link((white_book, SIGNAL_SHEET, number), [row.source])
                    continue
                # 未解析的对端组件已在 Components 阶段报错。
                peer = component_type_map.get(row.peer)
                if peer is None:
                    continue
                if direction == "IN":
                    assembly_key = (peer[0], row.peer_port, app_prototype, row.app_port)
                else:
                    assembly_key = (app_prototype, row.app_port, peer[0], row.peer_port)
                number, appended = _append_unique(assembly_ws, existing_assemblies, assembly_key)
                counters["assembly"] += appended
                if number:
                    report.link((white_book, ASSEMBLY_SHEET, number), [row.source])
            except ValueError as exc:
                fail(f"{info.app_name} {sheet} row {row.excel_row}", exc, [row.source])
    return counters


def _check_declarations(
    green_infos: list[GreenWorkbookInfo], component_type_map: ComponentTypeMap, e2e_wrapper: str,
) -> None:
    """两端都有绿表的组件连接应在提供方 P-Port 页和请求方 R-Port 页各声明一次。

    只有一侧声明时连接仍按该行生成，但两张表可能不同步：汇总为一条 WARNING，
    逐行说明写入结果表（提示级别，不影响结论）。
    """

    apps: dict[str, GreenWorkbookInfo] = {}
    for info in green_infos:
        resolved = component_type_map.get(info.app_name)
        if resolved is not None:
            apps[resolved[0]] = info
    declared: dict[tuple[str, str, str, str], dict[str, list[GreenRow]]] = {}
    for prototype, info in apps.items():
        for side, rows in (("requester", info.r_port_rows), ("provider", info.p_port_rows)):
            for row in rows:
                peer = None if _is_bus_row(row, e2e_wrapper) else component_type_map.get(row.peer)
                if peer is None:
                    continue
                key = ((peer[0], row.peer_port, prototype, row.app_port) if side == "requester"
                       else (prototype, row.app_port, peer[0], row.peer_port))
                declared.setdefault(key, {}).setdefault(side, []).append(row)
    one_sided = 0
    for (provider, provider_port, requester, requester_port), sides in declared.items():
        if provider not in apps or requester not in apps or len(sides) == 2:
            continue
        missing_side, other = (
            ("provider", apps[provider]) if "requester" in sides else ("requester", apps[requester]))
        sheet = P_PORT_SHEET if missing_side == "provider" else R_PORT_SHEET
        one_sided += 1
        report.note(
            [row.source for rows in sides.values() for row in rows], report.HINT,
            f"对端绿表 {other.app_name} 的 {sheet} 页没有声明这条连接 "
            f"{provider}.{provider_port} -> {requester}.{requester_port}；连接仍按本行生成，请确认两张表是否同步",
        )
    if one_sided:
        LOGGER.warning(
            "%d component connection(s) are declared in only one of the two green tables involved; "
            "they are still generated; see the row results for the rows concerned", one_sided)


def generate_white_table(
    model: ArxmlModel,
    green_infos: list[GreenWorkbookInfo],
    general: dict,
    output_file: Path,
    component_aliases: dict[str, str | dict[str, str]],
    prototype_prefix: str,
    e2e_wrapper: str,
    signal_path_template: str,
) -> Path:
    """基于已加载的 ARXML 模型和已解析的绿表生成白表；有任何错误时全部处理完再失败。"""

    general = resolve_general(general, model)
    channel_sets = load_channel_sets(model)
    component_type_map = build_component_type_map(
        model, general["Composition"], component_aliases, prototype_prefix)
    workbook = create_white_workbook(general)

    # 一个绿表对应一个要创建的模块，模块类型必须能在 ARXML 中找到。
    missing_components = sorted(
        info.app_name for info in green_infos if component_type_map.resolve(info.app_name) is None)
    if missing_components:
        raise ValueError(
            "Cannot find the SW component type of green table(s) in the loaded ARXML files: "
            + ", ".join(missing_components))

    errors: list[str] = []
    total_counters = {"components": 0, "assembly": 0, "signal": 0}
    app_names = {info.app_name for info in green_infos}
    for green_info in green_infos:
        counters = process_green_table(
            green_info, component_type_map, app_names, channel_sets, workbook,
            e2e_wrapper, signal_path_template, model, errors, str(output_file))
        LOGGER.info(
            "Processed %s: +%s Components, +%s AssemblySwConnection, +%s SenderReceiverToSignal",
            green_info.app_name, counters["components"], counters["assembly"], counters["signal"])
        for key in total_counters:
            total_counters[key] += counters[key]
    _check_declarations(green_infos, component_type_map, e2e_wrapper)
    if errors:
        raise ValueError(f"{len(errors)} green table error(s); see the messages above")

    format_white_workbook(workbook)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output_file)
    workbook.close()
    LOGGER.info(
        "White table done: %s Components, %s AssemblySwConnection, %s SenderReceiverToSignal -> %s",
        total_counters["components"], total_counters["assembly"], total_counters["signal"], output_file.name)
    return output_file
