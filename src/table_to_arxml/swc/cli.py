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
from typing import Sequence

from ..common.dependencies import check_dependencies
from ..common.logs import setup_logging
from ..common.messages import clean_message
from .options import Options, parse_args

TOOL_NAME = "table_to_swc_arxml"
LOGGER = logging.getLogger(__name__)


def main(argv: Sequence[str] | None = None) -> int:
    """命令入口；参数错误由 argparse 以退出码 2 结束，运行失败记录错误并返回 1。"""

    options = parse_args(argv)
    try:
        return run(options)
    except (OSError, UnicodeError, ValueError, RuntimeError) as error:
        LOGGER.error("%s failed; no ARXML files were written: %s", TOOL_NAME, clean_message(error))
        return 1
    except Exception:
        # 非预期异常多为程序缺陷，保留堆栈便于定位。
        LOGGER.exception("%s failed unexpectedly; no ARXML files were written", TOOL_NAME)
        return 1


def run(options: Options) -> int:
    """检查输入和依赖、执行处理流程，最后按输出选项写出文件；返回退出码。"""

    _log_file, errors = setup_logging(TOOL_NAME, options.log_dir, options.log_level)
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
    from .store import write_changes

    LOGGER.info("Workbook: %s", options.workbook)
    LOGGER.info("Project directory: %s", options.project_root)
    LOGGER.info("ARXML list: %s", options.arxml_file_list)
    summary, store = execute(options)
    summary.log()

    if errors.count:
        LOGGER.error("Validation failed with %d error(s); no ARXML files were written", errors.count)
        return 1
    if options.in_place or options.output_dir is not None:
        written_files = write_changes(
            store.modified(), options.in_place, options.output_dir, options.project_root,
            tuple(store.source_files),
        )
        LOGGER.info("Successfully wrote %d ARXML file(s)", len(written_files))
    else:
        LOGGER.info("Dry run successful; use --in-place or --output-dir to write ARXML")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
