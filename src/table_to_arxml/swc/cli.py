"""表格转 SWC ARXML 工具入口：根据 Excel 工作簿向已有 ARXML 中的组件批量添加端口、接口和数据类型。

工作簿格式见 :mod:`.workbook`，参数与配置见 :mod:`.options`，处理流程见 :mod:`.pipeline`。工具只在
``--arxml-file-list`` 列出的 ARXML 中查找组件和接口；新元素以 XML 片段插入原文件字节（:mod:`.xml_patch`），
其余内容保持不变，再重新解析验证。默认只验证，``--in-place`` 覆盖源文件，``--output-dir``
按 projectRoot 内的相对路径写到其他目录。任一 ERROR 都阻止写出全部 ARXML。

退出码：0 成功；1 运行失败（输入错误、验证失败等）；2 命令行或配置错误（不创建日志）。
运行日志写入 ``logDir`` 下的 ``table_to_swc_arxml_<时间>_<进程号>.log``，同时输出到控制台。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Sequence

from ..common import report
from ..common.dependencies import check_dependencies
from ..common.logs import ErrorCollector, setup_logging
from ..common.messages import clean_message
from .options import Options, parse_args

TOOL_NAME = "table_to_swc_arxml"
LOGGER = logging.getLogger(__name__)


def main(argv: Sequence[str] | None = None) -> int:
    """命令入口；参数错误由 argparse 以退出码 2 结束，运行失败记录错误并返回 1。"""

    try:
        options = parse_args(argv)
        return run(options)
    except (OSError, UnicodeError, ValueError, RuntimeError) as error:
        LOGGER.error("%s failed; no ARXML files were written: %s", TOOL_NAME, clean_message(error))
        return 1
    except Exception:
        # 非预期异常多为程序缺陷，保留堆栈便于定位。
        LOGGER.exception("%s failed unexpectedly; no ARXML files were written", TOOL_NAME)
        return 1


class _Run:
    """一次运行的产出，供结束时写结果工作簿、差异文件和结论行。"""

    def __init__(self) -> None:
        self.outcome = report.FAILED
        self.detail = "no ARXML files were written"
        self.summary = None
        # 源文件 -> (修改前内容, 修改后内容)；在写出前记录，原地覆盖后仍能生成差异。
        self.originals: dict[Path, tuple[bytes | None, bytes]] = {}
        self.written: list[Path] = []
        self.destinations: dict[Path, Path] = {}
        # 校验失败时的 ERROR 数；不含随后汇总用的那条 ERROR。
        self.errors: int | None = None


def run(options: Options) -> int:
    """检查输入和依赖、执行处理流程，最后按输出选项写出文件；返回退出码。

    无论成功与否，结束时都在日志目录写出逐行结果工作簿和变更差异，并输出 ``RESULT`` 结论行。
    """

    log_file, errors = setup_logging(TOOL_NAME, options.log_dir, options.log_level)
    run_report = report.start()
    state = _Run()
    try:
        code = _run(options, errors, state)
    except (OSError, UnicodeError, ValueError, RuntimeError) as error:
        LOGGER.error("%s failed; no ARXML files were written: %s", TOOL_NAME, clean_message(error))
        code = 1
    except Exception:
        LOGGER.exception("%s failed unexpectedly; no ARXML files were written", TOOL_NAME)
        code = 1
    if code:
        state.outcome = report.FAILED
    _finish(options, log_file, errors, run_report, state)
    return code


def _run(options: Options, errors: ErrorCollector, state: _Run) -> int:
    if not options.project_root.is_dir():
        raise NotADirectoryError(f"Project directory not found: {options.project_root}")
    if not options.workbook.is_file():
        raise FileNotFoundError(f"Workbook not found: {options.workbook}")
    if not options.arxml_file_list.is_file():
        raise FileNotFoundError(f"ARXML list not found: {options.arxml_file_list}")

    LOGGER.info("Python interpreter: %s", sys.executable)
    check_dependencies()
    # 依赖检查通过后才导入 armodel 相关模块，版本不符时给出明确错误而非导入异常。
    from .pipeline import execute
    from .store import output_path, write_changes

    LOGGER.info("Workbook: %s", options.workbook)
    LOGGER.info("Project directory: %s", options.project_root)
    LOGGER.info("ARXML list: %s", options.arxml_file_list)
    summary, store = execute(options)
    state.summary = summary
    modified = store.modified()
    state.originals = {
        path: (path.read_bytes() if path.is_file() else None, document) for path, document in modified.items()
    }
    writes = options.in_place or options.output_dir is not None
    summary.log(will_write=writes and not errors.count)

    if errors.count:
        state.errors = errors.count
        LOGGER.error("Validation failed with %d error(s); no ARXML files were written", errors.count)
        return 1
    if writes:
        state.written = write_changes(
            modified, options.in_place, options.output_dir, options.project_root,
            tuple(store.source_files), arxml_list=options.output_arxml_list,
            protected=(options.workbook, options.arxml_file_list),
        )
        state.destinations = {
            path: path if options.in_place else output_path(path, options.output_dir, options.project_root)
            for path in modified
        }
        LOGGER.info("Successfully wrote %d ARXML file(s)", len(state.written))
        state.outcome = report.WRITTEN
        state.detail = (
            f"{summary.planned_objects} object(s) created, {len(state.written)} ARXML file(s) written"
        )
        if options.output_arxml_list is not None:
            state.detail += f", ARXML list {options.output_arxml_list}"
    else:
        LOGGER.info("Dry run successful; use --in-place or --output-dir to write ARXML")
        if options.output_arxml_list is not None:
            LOGGER.info("Dry run: ARXML list not written: %s", options.output_arxml_list)
        state.outcome = report.DRY_RUN
        state.detail = (
            f"{summary.planned_objects} object(s) would be created in {len(modified)} ARXML file(s); "
            "no files written"
        )
    return 0


def _finish(options: Options, log_file: Path, errors: ErrorCollector, run_report, state: _Run) -> None:
    """写结果工作簿和差异文件后输出结论行；这些报告文件写入失败只记录 WARNING。"""

    diff_file = _write_diff(log_file.with_name(log_file.stem + ".diff"), state)
    result_file = _write_result_workbook(
        log_file.with_name(log_file.stem + "_result.xlsx"), options, run_report, state, diff_file,
    )
    if result_file is not None:
        LOGGER.info("Row results: %s", result_file)
    if diff_file is not None:
        LOGGER.info("ARXML changes: %s", diff_file)
    if state.outcome == report.FAILED and errors.count:
        state.detail = "no ARXML files were written; see the log and the row results"
    error_count = errors.count if state.errors is None else state.errors
    LOGGER.info(
        "%s", report.result_line(TOOL_NAME, state.outcome, error_count, errors.warnings, state.detail)
    )


def _write_diff(path: Path, state: _Run) -> Path | None:
    if not state.originals:
        return None
    changes = [
        (str(source_file), str(state.destinations.get(source_file, source_file)), original, document)
        for source_file, (original, document) in state.originals.items()
    ]
    title = {
        report.WRITTEN: "written",
        report.DRY_RUN: "dry run, not written",
        report.FAILED: "not written because of errors",
    }[state.outcome]
    return report.write_diff(path, changes, f"# table-to-swc-arxml ARXML changes ({title})\n")


def _write_result_workbook(
    path: Path, options: Options, run_report, state: _Run, diff_file: Path | None,
) -> Path | None:
    try:
        from openpyxl import Workbook, load_workbook

        try:
            workbook = load_workbook(options.workbook)
        except Exception:  # 工作簿本身无法读取时只输出汇总页
            workbook = Workbook()
            workbook.remove(workbook.active)
        for sheet in workbook.worksheets:
            if sheet.title.casefold() != "readme":
                report.append_result_columns(
                    sheet, run_report, "", sheet.title, state.outcome, report.data_rows(sheet),
                )
        lines = [
            ("结论", report.TITLES[state.outcome]),
            ("说明", state.detail),
            ("工作簿", str(options.workbook)),
            ("ARXML 清单", str(options.arxml_file_list)),
            ("写出的 ARXML", "\n".join(str(path) for path in state.written) or "无"),
            ("ARXML 差异", str(diff_file) if diff_file else "无修改"),
        ]
        if options.output_arxml_list is not None and state.outcome == report.WRITTEN:
            lines.append(("生效 ARXML 清单", str(options.output_arxml_list)))
        if state.summary is not None:
            lines.extend(state.summary.rows(report.created_label(state.outcome)))
        report.add_summary_sheet(workbook, lines, run_report)
        report.add_objects_sheet(workbook, run_report, state.outcome)
    except Exception as error:  # 报告失败不影响本次运行结果
        LOGGER.warning("Could not build result workbook %s: %s", path, clean_message(error))
        return None
    return report.save_workbook(workbook, path)


if __name__ == "__main__":
    raise SystemExit(main())
