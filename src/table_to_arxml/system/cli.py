"""表格转 System ARXML 工具入口：绿表 -> 白表 Excel -> Composition/System ARXML，也可直接输入白表。

两个阶段在同一进程内共用一个 AUTOSAR 文档，避免重复解析 ARXML。

工具本身与具体工程解耦：所有工程相关的路径、车型、命名都放在 TOML 配置文件中。产物和日志目录必须通过
命令行或配置文件显式指定，不提供默认目录；需要分发到工程其他位置时由外部（例如 CMB 动作中的 cp 命令）复制。

退出码：0 成功；1 运行失败（输入错误、生成失败等），不发布任何产物；2 命令行或配置错误（不创建日志）。
"""

from __future__ import annotations

import logging
import mimetypes
import os
import re
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Sequence

from ..common.arxml_list import DEFAULT_EXCLUDE_PATTERNS, ArxmlExclusions, read_arxml_list
from ..common.dependencies import check_dependencies
from ..common.errors import ConfigError
from ..common.logs import setup_logging
from ..common.messages import clean_message
from ..common.output import write_files_atomically
from ..common.paths import filesystem_path
from .tresos_list import (
    generate_file_list,
    read_generated_list,
    resolve_preference_files,
    tresos_project_dir,
)
from .options import (
    DEFAULT_TRESOS_OUTPUT_BASE_DIR, MERGED_GREEN_TABLE_SUFFIX,
    SYSTEM_ARXML_SUFFIX, WHITE_TABLE_SUFFIX, Job, RunOptions, build_argument_parser,
    prepare_jobs, resolve_log_level, resolve_run_directory,
)

# 绕行：部分 Windows 环境下导入 openpyxl 时 mimetypes 会逐项探测注册表，耗时很长。
if os.name == "nt" and hasattr(mimetypes, "_winreg"):
    mimetypes._winreg = None  # type: ignore[attr-defined]


LOGGER = logging.getLogger(__name__)
TOOL_NAME = "table_to_system_arxml"


def _resolve_file_list(job: Job, default_list_file: Path, cli_file_list: str | None) -> tuple[Path, bool]:
    """确定清单文件位置，并告知是否需要调 EB 导入器生成。

    优先级：``--arxml-file-list`` > ``config.arxmlFileList`` > 用 EB 导入器生成到输出目录。
    配置中存在 arxmlFileList 时必须是有效清单，空值或缺失文件不回退到 EB。
    """

    if cli_file_list:
        selected = filesystem_path(cli_file_list).resolve()
    elif job.arxml_file_list is not None:
        selected = job.arxml_file_list
    else:
        return default_list_file, False
    if not selected.is_file():
        raise FileNotFoundError(f"ARXML file list does not exist: {selected}")
    return selected, True


def _collect_arxml_paths(
    job: Job,
    generated_arxml_names: list[str],
    list_file: Path,
    reuse_existing_list: bool,
    generated_lists: dict[tuple, Path],
    options: RunOptions,
) -> list[Path]:
    """用 EB 导入器生成清单（或复用已有清单），再读出本次需要加载的 ARXML。

    两个阶段共用同一份文件集合，因此只需要加载一次；本次将要生成的同名 ARXML
    必须排除，否则 composition 会把上一次的结果当成输入。
    """

    patterns = [*DEFAULT_EXCLUDE_PATTERNS, *job.exclude_patterns]
    patterns.extend(rf".*[\\/]{re.escape(name)}$" for name in generated_arxml_names)
    excluded = options.exclude_arxml if options.exclude_arxml is not None else job.exclude_arxml
    exclusions = ArxmlExclusions(job.project_root, excluded, patterns)
    strict = options.strict_list if options.strict_list is not None else job.strict_list

    if reuse_existing_list:
        LOGGER.info("Reusing the configured ARXML file list: %s; skipping XDM and EB importer", list_file)
        return read_arxml_list(list_file, job.project_root, exclusions=exclusions, strict=strict)

    tresos_output_path = job.tresos_output_dir or job.project_root / DEFAULT_TRESOS_OUTPUT_BASE_DIR.replace(
        "${variant}", job.variant).replace("${buildType}", job.build_type)
    if not job.preference_entries:
        raise ValueError("Missing config.tresosPreferenceFiles")
    preference_files = resolve_preference_files(job.preference_entries, job.variant, job.config_dir)

    generate_key = (tuple(preference_files), tresos_output_path)
    if generate_key in generated_lists:
        list_file = generated_lists[generate_key]
    else:
        generate_file_list(preference_files, tresos_output_path, list_file)
        generated_lists[generate_key] = list_file

    return read_generated_list(
        list_file, job.project_root, tresos_project_dir(preference_files), tresos_output_path.name,
        exclusions, strict,
    )


def _read_input_tables(jobs: list[Job]) -> None:
    """加载 ARXML 前先校验所有输入表；逐个检查完再失败，一次报出全部问题。"""

    from .green_table import collect_green_workbooks, parse_green_workbook
    from .white_table import read_composition_input

    invalid_tables = 0
    for job in jobs:
        LOGGER.info("Config: %s (variant %s, project root %s)", job.config_path, job.variant, job.project_root)
        if not job.project_root.is_dir():
            raise NotADirectoryError(f"Project directory not found: {job.project_root}")
        if job.white_table is not None:
            if not job.white_table.is_file():
                raise FileNotFoundError(f"White table does not exist: {job.white_table}")
            try:
                job.white_input = read_composition_input(job.white_table)
            except ValueError as exc:
                LOGGER.error("%s", exc)
                invalid_tables += 1
                continue
            LOGGER.info("Selected white table: %s", job.white_table)
            continue
        # 配置里的相对路径以配置文件所在目录为基准。
        job.green_tables = collect_green_workbooks(job.green_table_entries, job.variant, job.config_dir)
        for app_name, green_excel in job.green_tables:
            try:
                job.green_infos.append(parse_green_workbook(green_excel, app_name))
            except ValueError as exc:
                LOGGER.error("%s", exc)
                invalid_tables += 1
        LOGGER.info("Selected %s green tables", len(job.green_tables))
    if invalid_tables:
        raise ValueError(f"{invalid_tables} input table(s) are invalid; see the messages above")


def run(options: RunOptions) -> list[tuple[Path, Path]]:
    """完整流程，按配置顺序返回每个配置的 ``(白表路径, Composition ARXML 路径)``。

    多个配置共用一次 ARXML 加载；前一个配置生成的 ARXML 会追加进模型，供后续配置引用，
    因此被引用的配置要排在前面。config 中写 ``greenTables`` 走绿表模式，写 ``whiteTable``
    走白表模式，二者必须且只能写一个；命令行 ``--white-table`` 强制白表模式，仅限单个配置。
    """

    jobs = prepare_jobs(options)
    resolved_log_level = resolve_log_level(jobs, options.log_level)
    resolved_log_dir = resolve_run_directory(jobs, options.log_dir, "log_dir", "logDir", "--log-dir")
    resolved_output_dir = resolve_run_directory(jobs, options.output_dir, "output_dir", "outputDir", "--output-dir")
    if resolved_log_dir == resolved_output_dir:
        raise ConfigError("Log directory and output directory must be different")

    _log_file, errors = setup_logging(TOOL_NAME, resolved_log_dir, resolved_log_level)
    LOGGER.info("Python interpreter: %s", sys.executable)
    check_dependencies()
    # 依赖检查通过后才导入 armodel 相关模块，版本不符时给出明确错误而非导入异常。
    from .arxml_generator import generate_composition_arxml
    from .arxml_model import ArxmlModel
    from .green_table import merge_green_workbooks
    from .green_to_white import generate_white_table
    from .white_table import read_composition_input

    LOGGER.info("Output directory: %s", resolved_output_dir)
    _read_input_tables(jobs)

    generated_names = [f"{job.output_prefix}{SYSTEM_ARXML_SUFFIX}" for job in jobs]
    with TemporaryDirectory(prefix="table-to-system-") as directory:
        staging = Path(directory)
        generated_lists: dict[tuple, Path] = {}
        arxml_paths: list[Path] = []
        seen: set[str] = set()
        for index, job in enumerate(jobs):
            list_name = f"{job.variant}.lst" if index == 0 else f"{job.variant}_{index}.lst"
            list_file, reuse_existing_list = _resolve_file_list(job, staging / list_name, options.arxml_file_list)
            for path in _collect_arxml_paths(
                    job, generated_names, list_file, reuse_existing_list, generated_lists, options):
                key = os.path.normcase(str(path))
                if key not in seen:
                    seen.add(key)
                    arxml_paths.append(path)
        started = perf_counter()
        model = ArxmlModel.load(arxml_paths)
        LOGGER.info("Loaded %s ARXML files in %.1fs", len(model.paths), perf_counter() - started)
        input_schemas = model.schema_files()

        results: list[tuple[Path, Path]] = []
        for index, job in enumerate(jobs):
            LOGGER.info("Generating %s from %s", job.output_prefix, job.config_path)
            if job.white_input is not None:
                white_table_path = job.white_table
                composition_input = job.white_input
            else:
                white_table_path = generate_white_table(
                    model=model, green_infos=job.green_infos, general=job.general,
                    output_file=staging / f"{job.output_prefix}{WHITE_TABLE_SUFFIX}",
                    component_aliases=job.component_aliases, prototype_prefix=job.prototype_prefix,
                    e2e_wrapper=job.e2e_wrapper, signal_path_template=job.signal_template)
                merge_green_workbooks(
                    job.green_tables, staging / f"{job.output_prefix}{MERGED_GREEN_TABLE_SUFFIX}")
                composition_input = read_composition_input(white_table_path)
            composition_arxml = generate_composition_arxml(
                document=model.document, data=composition_input,
                output_file=staging / f"{job.output_prefix}{SYSTEM_ARXML_SUFFIX}",
                system_mapping=job.system_mapping, ar_release=job.ar_release,
                interface_mismatch=job.mismatch_policy, interface_mappings=job.interface_mappings,
                generate_system=job.generate_system, input_schemas=input_schemas)
            if index < len(jobs) - 1:
                # 同名旧文件已被排除，后续配置需要看到本次新生成的内容。
                model.add(composition_arxml)
            results.append((
                job.white_table or resolved_output_dir / white_table_path.name,
                resolved_output_dir / composition_arxml.name))
        if errors.count:
            raise RuntimeError("; ".join(errors.messages))
        artifacts = sorted(path for path in staging.iterdir() if path.is_file())
        write_files_atomically([(resolved_output_dir / path.name, path.read_bytes()) for path in artifacts])
    return results


def main(argv: Sequence[str] | None = None) -> int:
    """命令入口；配置错误以退出码 2 结束，运行失败记录错误并返回 1。"""

    parser = build_argument_parser()
    args = parser.parse_args(argv)
    started = perf_counter()
    try:
        options = RunOptions(
            config_paths=tuple(filesystem_path(path).resolve() for path in args.config),
            project_root=args.project_root,
            variant=args.variant,
            output_dir=args.output_dir,
            log_dir=args.log_dir,
            log_level=args.log_level,
            arxml_file_list=args.arxml_file_list,
            white_table=args.white_table,
            exclude_arxml=None if args.exclude_arxml is None else tuple(args.exclude_arxml),
            strict_list=args.strict_list,
        )
        results = run(options)
    except ConfigError as exc:
        parser.error(clean_message(exc))
    except (OSError, ValueError, RuntimeError) as exc:
        LOGGER.error("%s failed; no new artifacts published: %s", TOOL_NAME, clean_message(exc))
        return 1
    except Exception:
        # 非预期异常多为程序缺陷，保留堆栈便于定位。
        LOGGER.exception("%s failed unexpectedly; no new artifacts published", TOOL_NAME)
        return 1

    for white_table, composition_arxml in results:
        LOGGER.info("Generated: %s, %s", white_table, composition_arxml)
    LOGGER.info("Finished in %.1fs", perf_counter() - started)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
