#!/usr/bin/env python
"""表格转 System ARXML 工具：绿表 -> 白表 Excel -> Composition/System ARXML，也可直接输入白表。

与原先 ``greenToWhiteTable`` + ``xlsx2c_parallel`` 两个命令相比，本工具在同一个
进程内只解析一次 ARXML，两个阶段共用同一个 AUTOSAR 文档，省掉重复解析耗时。

工具本身与具体工程解耦：所有工程相关的路径、车型、命名都放在 config.json 中。
产物和日志目录必须通过命令行或配置文件显式指定，不提供默认目录。
需要分发到工程其他位置时由外部
（例如 CMB 动作中的 cp 命令）自行复制。
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import mimetypes
import os
import re
import shutil
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import TYPE_CHECKING

from .lib.common import (
    DEFAULT_AR_RELEASE,
    DEFAULT_E2E_WRAPPER,
    DEFAULT_PROTOTYPE_PREFIX,
    DEFAULT_SIGNAL_PATH_TEMPLATE,
    norm,
)
from .lib.file_list import (
    DEFAULT_EXCLUDE_PATTERNS,
    filesystem_path,
    generate_file_list,
    read_file_list,
    resolve_preference_files,
    tresos_project_dir,
)

if TYPE_CHECKING:
    from .lib.green_table import GreenWorkbookInfo
    from .lib.white_table import CompositionInput


# Workaround: avoid slow registry probing on some Windows setups when importing openpyxl.
if os.name == "nt" and hasattr(mimetypes, "_winreg"):
    mimetypes._winreg = None  # type: ignore[attr-defined]


LOGGER = logging.getLogger(__name__)
TOOL_NAME = "table_to_system_arxml"
WHITE_TABLE_SUFFIX = "WhiteTable.xlsx"
MERGED_GREEN_TABLE_SUFFIX = "GreenTables.xlsx"
SYSTEM_ARXML_SUFFIX = "System.arxml"
DEFAULT_BUILD_TYPE = "Debug"
# CMB 约定的 tresos 生成目录，EB 导入器把它作为 TRESOS_OUTPUT_BASE_DIR。
DEFAULT_TRESOS_OUTPUT_BASE_DIR = "build/${variant}.${buildType}"
LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"]
INTERFACE_MISMATCH_POLICIES = ("error", "skip", "map")
# 与 requirements.txt 保持一致；1.9.4 起缺少本工具依赖的导出。
ARMODEL_REQUIREMENT = ">=1.8.7,<1.9.4"
CONFIG_KEYS = frozenset({
    "project", "defaultVariant", "buildType", "projectRoot", "logLevel", "outputDir", "logDir",
    "outputPrefix", "greenTables", "whiteTable", "tresosPreferenceFiles", "arxmlFileList",
    "tresosOutputBaseDir", "excludeArxmlPatterns", "excludeArxml", "general", "componentAliases",
    "prototypePrefix", "e2eWrapperComponent", "signalPathTemplate", "arRelease", "interfaceMismatch",
    "interfaceMappings", "generateSystem",
})
GENERAL_CONFIG_KEYS = frozenset({"Composition", "System", "EcuInstance", "RootSwCompositionPrototype", "systemMapping"})
INTERFACE_MAPPING_KEYS = {"providerInterface", "requesterInterface", "dataElements"}


def _is_armodel_record(record: logging.LogRecord) -> bool:
    return getattr(record, "armodel_parser_diagnostic", False) or "armodel" in Path(record.pathname).parts


class _AbortOnError(logging.Handler):
    """收集运行期间非 armodel 的 ERROR 日志，发布产物前统一失败。

    不在 ``emit`` 里抛异常：记录日志的第三方代码不会在日志调用处被打断。
    """

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.errors: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        # armodel 的加载报错照常按 ERROR 打印，但不影响退出码。
        if not _is_armodel_record(record):
            self.errors.append(record.getMessage())

    def raise_if_failed(self) -> None:
        if self.errors:
            raise RuntimeError("; ".join(self.errors))


@contextmanager
def _abort_on_error():
    handler = _AbortOnError()
    logger = logging.getLogger()
    logger.addHandler(handler)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        handler.close()


def _publish_outputs(staging: Path, output_dir: Path) -> None:
    created_directory = not output_dir.exists()
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        with TemporaryDirectory(prefix=".table-to-system-", dir=output_dir) as directory:
            pending = Path(directory)
            artifacts = sorted(path for path in staging.iterdir() if path.is_file())
            for artifact in artifacts:
                shutil.copyfile(artifact, pending / artifact.name)
            backups = pending / "backups"
            backups.mkdir()
            published: list[Path] = []
            replaced: list[Path] = []
            try:
                for artifact in artifacts:
                    target = output_dir / artifact.name
                    if target.exists():
                        if not target.is_file():
                            raise ValueError(f"Output target is not a file: {target}")
                        target.replace(backups / target.name)
                        replaced.append(target)
                    (pending / artifact.name).replace(target)
                    published.append(target)
            except Exception:
                for target in reversed(published):
                    target.unlink()
                for target in reversed(replaced):
                    (backups / target.name).replace(target)
                raise
    except Exception:
        if created_directory and not any(output_dir.iterdir()):
            output_dir.rmdir()
        raise


def _check_python_dependencies() -> None:
    failures: list[str] = []
    for module_name in ("packaging", "openpyxl", "armodel"):
        try:
            importlib.import_module(module_name)
        except Exception as exc:
            failures.append(f"{module_name}: {type(exc).__name__}: {exc}")
    if not failures:
        specifier = importlib.import_module("packaging.specifiers").SpecifierSet(ARMODEL_REQUIREMENT)
        installed_version = version("armodel")
        if installed_version not in specifier:
            failures.append(f"armodel{ARMODEL_REQUIREMENT} is required; installed: {installed_version}")
    if failures:
        raise RuntimeError(
            f"Python dependencies are missing or unusable (interpreter: {sys.executable}). "
            "Install or repair the project dependencies in this interpreter. "
            + "; ".join(failures))


def _load_config(config_path: Path) -> dict:
    with config_path.open("r", encoding="utf-8-sig") as handle:
        config = json.load(handle)
    if not isinstance(config, dict):
        raise ValueError(f"Config must be a JSON object: {config_path}")
    unknown = sorted(set(config) - CONFIG_KEYS)
    if unknown:
        raise ValueError(
            f"Unknown config option(s) in {config_path}: {', '.join(unknown)}; "
            f"supported: {', '.join(sorted(CONFIG_KEYS))}")
    return config


def _section(config: dict, key: str) -> dict:
    value = config.get(key, {})
    if not isinstance(value, dict):
        raise ValueError(f"config.{key} must be an object")
    return value


def _string_list(config: dict, key: str, required: bool) -> list[str]:
    value = config.get(key)
    if value is None:
        if required:
            raise KeyError(f"Missing config.{key}")
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"config.{key} must be a string or list of strings")
    entries = [item.strip() for item in value if item.strip()]
    if required and not entries:
        raise KeyError(f"Missing config.{key}")
    return entries


def _required(config: dict, key: str, label: str) -> str:
    value = norm(config.get(key))
    if not value:
        raise KeyError(f"Missing config.{label}")
    return value


def _string_option(config: dict, key: str, default: str, allow_empty: bool = False) -> str:
    value = config.get(key, default)
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValueError(f"config.{key} must be a {'possibly empty' if allow_empty else 'nonempty'} string")
    return value.strip()


def _filename_part(value: str, label: str) -> str:
    if value in (".", "..") or re.search(r'[<>:"/\\|?*\x00-\x1f]', value) or value.endswith((".", " ")):
        raise ValueError(f"{label} must be a portable filename component without a directory: {value}")
    return value


def _setup_logging(log_level: str, log_dir: Path) -> Path:
    """控制台与文件同时输出，文件名带时间戳避免多次执行互相覆盖。"""

    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{TOOL_NAME}_{datetime.now():%Y%m%d_%H%M%S_%f}_{os.getpid()}.log"

    root_logger = logging.getLogger()
    # 同一进程多次调用 run() 时关闭上一次的日志文件，避免句柄泄漏和 Windows 下文件被占用。
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
        handler.close()
    root_logger.setLevel(getattr(logging, log_level))

    formatter = logging.Formatter("%(asctime)s %(levelname)s: %(message)s")

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    return log_file


def _resolve_config_path(value: str, config_dir: Path) -> Path:
    """config 中的相对路径一律以配置文件所在目录为基准。"""

    path = filesystem_path(value)
    return path if path.is_absolute() else config_dir / path


def _optional_directory(config: dict, key: str, config_dir: Path) -> Path | None:
    if key not in config:
        return None
    return _resolve_config_path(_string_option(config, key, ""), config_dir).resolve()


def _resolve_project_root(config: dict, project_root: str | None, config_path: Path) -> Path:
    """工程根目录，用于解析清单里的相对路径；命令行优先，二者必须提供其一。"""

    if project_root:
        return filesystem_path(project_root).resolve()
    configured = norm(config.get("projectRoot"))
    if not configured:
        raise ValueError(f"{config_path}: project root is required via --project-root or config field 'projectRoot'")
    return _resolve_config_path(configured, config_path.parent).resolve()


def _exclude_patterns(config: dict) -> list[str]:
    patterns = _string_list(config, "excludeArxmlPatterns", required=False)
    for pattern in patterns:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"config.excludeArxmlPatterns: invalid regular expression {pattern!r}: {exc}") from exc
    return patterns


def _interface_mappings(value: object) -> dict[tuple[str, str], dict[str, str]]:
    """校验 ``interfaceMappings``，返回 ``(提供方接口, 请求方接口) -> 数据元素名映射``。"""

    if value is None:
        return {}
    if not isinstance(value, list):
        raise ValueError("interfaceMappings must be a list")
    mappings: dict[tuple[str, str], dict[str, str]] = {}
    for spec in value:
        if not isinstance(spec, dict) or set(spec) != INTERFACE_MAPPING_KEYS:
            raise ValueError("interfaceMappings entries require providerInterface, requesterInterface and dataElements")
        first, second, elements = spec["providerInterface"], spec["requesterInterface"], spec["dataElements"]
        if not all(isinstance(path, str) and path.startswith("/") for path in (first, second)):
            raise ValueError("interfaceMappings interface paths must be absolute AUTOSAR paths")
        if (not isinstance(elements, dict) or not elements
                or not all(isinstance(key, str) and key and isinstance(name, str) and name
                           for key, name in elements.items())):
            raise ValueError("interfaceMappings.dataElements must map provider names to requester names")
        if (first, second) in mappings:
            raise ValueError(f"Duplicate interfaceMappings entry: {first} -> {second}")
        mappings[(first, second)] = dict(elements)
    return mappings


@dataclass
class _Job:
    """一个配置文件对应的一次生成任务；配置在加载 ARXML 前一次性解析和校验。"""

    config_path: Path
    variant: str
    project_root: Path
    output_prefix: str
    general: dict
    system_mapping: str
    white_table: Path | None
    green_table_entries: list[str]
    prototype_prefix: str
    e2e_wrapper: str
    signal_template: str
    ar_release: str
    mismatch_policy: str
    interface_mappings: dict[tuple[str, str], dict[str, str]]
    component_aliases: dict
    generate_system: bool
    log_level: str
    output_dir: Path | None
    log_dir: Path | None
    arxml_file_list: Path | None
    exclude_patterns: list[str]
    exclude_arxml: list[str]
    preference_entries: list[str]
    build_type: str
    tresos_output_dir: Path | None
    green_tables: list[tuple[str, Path]] = field(default_factory=list)
    # 加载 ARXML 前预校验时解析的输入表，生成阶段直接复用，不再重复读取 Excel。
    green_infos: list[GreenWorkbookInfo] = field(default_factory=list)
    white_input: CompositionInput | None = None

    @property
    def config_dir(self) -> Path:
        return self.config_path.parent


def _prepare_job(
    config_path: Path, project_root: str | None, variant: str | None, white_table: str | None,
) -> _Job:
    """读取并校验单个配置；不依赖 armodel，可在加载 ARXML 前完成。"""

    config_path = config_path.resolve()
    config = _load_config(config_path)
    config_dir = config_path.parent
    resolved_variant = norm(variant) or norm(config.get("defaultVariant"))
    if not resolved_variant:
        raise ValueError(f"{config_path}: variant is required via --variant or config field 'defaultVariant'")
    _filename_part(resolved_variant, "variant")

    # 产物命名全部由 config.outputPrefix 推导，例如 Bcm -> BcmWhiteTable.xlsx / BcmGreenTables.xlsx / BcmSystem.arxml。
    output_prefix = _filename_part(_required(config, "outputPrefix", "outputPrefix"), "outputPrefix")
    general = _section(config, "general")
    unknown_general = sorted(set(general) - GENERAL_CONFIG_KEYS)
    if unknown_general:
        raise ValueError(
            f"Unknown config.general option(s) in {config_path}: {', '.join(unknown_general)}; "
            f"supported: {', '.join(sorted(GENERAL_CONFIG_KEYS))}")

    input_white_table: Path | None = None
    if white_table:
        input_white_table = filesystem_path(white_table).resolve()
    elif ("greenTables" in config) == ("whiteTable" in config):
        raise ValueError(
            f"{config_path}: config must contain exactly one of 'greenTables' (green mode) or 'whiteTable' (white mode)")
    elif "whiteTable" in config:
        input_white_table = _resolve_config_path(
            _required(config, "whiteTable", "whiteTable").replace("${variant}", resolved_variant),
            config_dir).resolve()
    if input_white_table is not None and not input_white_table.is_file():
        raise FileNotFoundError(f"White table does not exist: {input_white_table}")

    prototype_prefix = _string_option(config, "prototypePrefix", DEFAULT_PROTOTYPE_PREFIX, allow_empty=True)
    if prototype_prefix and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", prototype_prefix):
        raise ValueError("config.prototypePrefix must be an AUTOSAR name prefix")
    signal_template = _string_option(config, "signalPathTemplate", DEFAULT_SIGNAL_PATH_TEMPLATE)
    if not signal_template.startswith("/") or "${signal}" not in signal_template:
        raise ValueError("config.signalPathTemplate must be absolute and contain ${signal}")
    if "${" in signal_template.replace("${channel}", "").replace("${signal}", ""):
        raise ValueError("config.signalPathTemplate only supports ${channel} and ${signal}")
    mismatch_policy = _string_option(config, "interfaceMismatch", "error")
    if mismatch_policy not in INTERFACE_MISMATCH_POLICIES:
        raise ValueError("interfaceMismatch must be error, skip or map")
    generate_system = config.get("generateSystem", True)
    if not isinstance(generate_system, bool):
        raise ValueError("config.generateSystem must be true or false")

    arxml_file_list = None
    if "arxmlFileList" in config:
        configured = _string_option(config, "arxmlFileList", "").replace("${variant}", resolved_variant)
        arxml_file_list = _resolve_config_path(configured, config_dir).resolve()
    build_type = norm(config.get("buildType")) or DEFAULT_BUILD_TYPE
    configured_output = norm(config.get("tresosOutputBaseDir"))
    tresos_output_dir = _resolve_config_path(
        configured_output.replace("${variant}", resolved_variant).replace("${buildType}", build_type),
        config_dir) if configured_output else None

    return _Job(
        config_path=config_path,
        variant=resolved_variant,
        project_root=_resolve_project_root(config, project_root, config_path),
        output_prefix=output_prefix,
        general=general,
        system_mapping=norm(general.get("systemMapping")) or f"{output_prefix}SystemImpl",
        white_table=input_white_table,
        green_table_entries=(
            [] if input_white_table is not None else _string_list(config, "greenTables", required=True)),
        prototype_prefix=prototype_prefix,
        e2e_wrapper=_string_option(config, "e2eWrapperComponent", DEFAULT_E2E_WRAPPER, allow_empty=True),
        signal_template=signal_template,
        ar_release=_string_option(config, "arRelease", DEFAULT_AR_RELEASE),
        mismatch_policy=mismatch_policy,
        interface_mappings=_interface_mappings(config.get("interfaceMappings")),
        component_aliases=_section(config, "componentAliases"),
        generate_system=generate_system,
        log_level=norm(config.get("logLevel")).upper(),
        output_dir=_optional_directory(config, "outputDir", config_dir),
        log_dir=_optional_directory(config, "logDir", config_dir),
        arxml_file_list=arxml_file_list,
        exclude_patterns=_exclude_patterns(config),
        exclude_arxml=_string_list(config, "excludeArxml", required=False),
        preference_entries=_string_list(config, "tresosPreferenceFiles", required=False),
        build_type=build_type,
        tresos_output_dir=tresos_output_dir,
    )


def _resolve_file_list(job: _Job, default_list_file: Path, cli_file_list: str | None) -> tuple[Path, bool]:
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
    job: _Job,
    generated_arxml_names: list[str],
    list_file: Path,
    reuse_existing_list: bool,
    generated_lists: dict[tuple, Path],
    exclude_arxml: list[str] | None = None,
) -> list[Path]:
    """用 EB 导入器生成清单，再读出本次需要加载的 ARXML。

    两个阶段共用同一份文件集合，因此只需要加载一次；本次将要生成的同名 ARXML
    必须排除，否则 composition 会把上一次的结果当成输入。
    """

    exclude_patterns: list[str] = list(DEFAULT_EXCLUDE_PATTERNS)
    exclude_patterns.extend(rf".*[\\/]{re.escape(name)}$" for name in generated_arxml_names)
    exclude_patterns.extend(job.exclude_patterns)
    excluded = exclude_arxml if exclude_arxml is not None else job.exclude_arxml

    if reuse_existing_list:
        LOGGER.info("Reusing the configured ARXML file list: %s; skipping XDM and EB importer", list_file)
        return read_file_list(list_file, job.project_root, job.project_root, "", exclude_patterns, excluded)

    tresos_output_path = job.tresos_output_dir or job.project_root / DEFAULT_TRESOS_OUTPUT_BASE_DIR.replace(
        "${variant}", job.variant).replace("${buildType}", job.build_type)
    if not job.preference_entries:
        raise KeyError("Missing config.tresosPreferenceFiles")
    preference_files = resolve_preference_files(job.preference_entries, job.variant, job.config_dir)

    generate_key = (tuple(preference_files), tresos_output_path)
    if generate_key in generated_lists:
        list_file = generated_lists[generate_key]
    else:
        generate_file_list(preference_files, tresos_output_path, list_file)
        generated_lists[generate_key] = list_file

    return read_file_list(
        list_file,
        job.project_root,
        tresos_project_dir(preference_files),
        tresos_output_path.name,
        exclude_patterns,
        excluded,
    )


def _resolve_run_directory(
    jobs: list[_Job], cli_value: str | None, attribute: str, config_key: str, cli_option: str,
) -> Path:
    if cli_value is not None:
        if not isinstance(cli_value, str) or not cli_value.strip():
            raise ValueError(f"{cli_option} must specify a nonempty directory")
        path = filesystem_path(cli_value.strip())
        return (path if path.is_absolute() else Path.cwd() / path).resolve()

    directories: list[Path] = []
    for job in jobs:
        directory = getattr(job, attribute)
        if directory is None:
            raise ValueError(
                f"{job.config_path}: directory is required via {cli_option} "
                f"or nonempty config.{config_key}")
        directories.append(directory)
    if any(directory != directories[0] for directory in directories[1:]):
        raise ValueError(
            f"config.{config_key} must resolve to the same directory across configs; "
            f"use {cli_option} to override")
    return directories[0]


def _resolve_log_level(jobs: list[_Job], cli_value: str | None) -> str:
    """命令行优先；否则各配置中写了的 logLevel 必须一致，都没写时为 INFO。"""

    if norm(cli_value):
        level = norm(cli_value).upper()
    else:
        levels = {job.log_level for job in jobs} - {""}
        if len(levels) > 1:
            raise ValueError(
                f"config.logLevel differs across configs: {', '.join(sorted(levels))}; "
                "use --log-level to override")
        level = levels.pop() if levels else "INFO"
    if level not in LOG_LEVELS:
        raise ValueError(f"Invalid log level: {level}")
    return level


def run(
    config_paths: Path | list[Path],
    project_root: str | None = None,
    variant: str | None = None,
    output_dir: str | None = None,
    log_level: str | None = None,
    file_list: str | None = None,
    log_dir: str | None = None,
    white_table: str | None = None,
    exclude_arxml: list[str] | None = None,
) -> list[tuple[Path, Path]]:
    """完整流程，按配置顺序返回每个配置的 ``(白表路径, Composition ARXML 路径)``。

    多个配置共用一次 ARXML 加载；前一个配置生成的 ARXML 会追加进模型，供后续配置引用，
    因此被引用的配置要排在前面。config 中写 ``greenTables`` 走绿表模式，写 ``whiteTable``
    走白表模式，二者必须且只能写一个；命令行 ``--white-table`` 强制白表模式，仅限单个配置。
    """

    if isinstance(config_paths, Path):
        config_paths = [config_paths]
    if not config_paths:
        raise ValueError("At least one config is required")
    if white_table and len(config_paths) > 1:
        raise ValueError("--white-table can only be used with a single --config")
    jobs = [_prepare_job(path, project_root, variant, white_table) for path in config_paths]
    prefixes = [job.output_prefix for job in jobs]
    duplicated = sorted({prefix for prefix in prefixes if prefixes.count(prefix) > 1})
    if duplicated:
        raise ValueError(f"config.outputPrefix must be unique across configs: {', '.join(duplicated)}")

    resolved_log_level = _resolve_log_level(jobs, log_level)
    resolved_log_dir = _resolve_run_directory(jobs, log_dir, "log_dir", "logDir", "--log-dir")
    resolved_output_dir = _resolve_run_directory(jobs, output_dir, "output_dir", "outputDir", "--output-dir")
    if resolved_log_dir == resolved_output_dir:
        raise ValueError("Log directory and output directory must be different")
    log_file = _setup_logging(resolved_log_level, resolved_log_dir)

    LOGGER.info("Log file: %s", log_file)
    LOGGER.info("Python interpreter: %s", sys.executable)
    _check_python_dependencies()
    try:
        from .lib.arxml_generator import generate_composition_arxml
        from .lib.arxml_model import ArxmlModel
        from .lib.green_table import collect_green_workbooks, merge_green_workbooks, parse_green_workbook
        from .lib.green_to_white import generate_white_table
        from .lib.white_table import read_composition_input
    except (ImportError, OSError, AttributeError) as exc:
        raise RuntimeError(
            f"Cannot load conversion dependencies (interpreter: {sys.executable}): "
            f"{type(exc).__name__}: {exc}. Check armodel, openpyxl and their dependencies."
        ) from exc

    LOGGER.info("Output directory: %s", resolved_output_dir)
    # 加载 ARXML 前先校验所有输入表；逐个检查完再失败，一次报出全部问题。
    invalid_tables = 0
    for job in jobs:
        LOGGER.info("Config: %s (variant %s, project root %s)", job.config_path, job.variant, job.project_root)
        if job.white_table is not None:
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

    generated_names = [f"{job.output_prefix}{SYSTEM_ARXML_SUFFIX}" for job in jobs]
    with _abort_on_error() as errors, TemporaryDirectory(prefix="table-to-system-") as directory:
        staging = Path(directory)
        generated_lists: dict[tuple, Path] = {}
        arxml_paths: list[Path] = []
        seen: set[str] = set()
        for index, job in enumerate(jobs):
            list_name = f"{job.variant}.lst" if index == 0 else f"{job.variant}_{index}.lst"
            list_file, reuse_existing_list = _resolve_file_list(job, staging / list_name, file_list)
            for path in _collect_arxml_paths(
                    job, generated_names, list_file, reuse_existing_list, generated_lists, exclude_arxml):
                key = os.path.normcase(str(path))
                if key not in seen:
                    seen.add(key)
                    arxml_paths.append(path)
        started = perf_counter()
        model = ArxmlModel.load(arxml_paths)
        LOGGER.info("Loaded %s ARXML files in %.1fs", len(model.paths), perf_counter() - started)

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
                generate_system=job.generate_system)
            if index < len(jobs) - 1:
                # 同名旧文件已被排除，后续配置需要看到本次新生成的内容。
                model.add(composition_arxml)
            results.append((
                job.white_table or resolved_output_dir / white_table_path.name,
                resolved_output_dir / composition_arxml.name))
        errors.raise_if_failed()
        _publish_outputs(staging, resolved_output_dir)
    return results


def build_argument_parser(default_config: Path | None = None) -> argparse.ArgumentParser:
    default_location = (
        str(default_config) if default_config is not None
        else "config.json in the current working directory")
    parser = argparse.ArgumentParser(
        description="Convert green tables into the white table Excel and the composition ARXML",
        allow_abbrev=False)
    parser.add_argument(
        "--config",
        action="append",
        help="Path to a tool configuration; repeat to process several configs with one ARXML load, "
             f"in the given order. Default: {default_location}",
    )
    parser.add_argument(
        "--project-root",
        help="Project root used to resolve the project paths in the configuration. "
             "Required via CLI or config field 'projectRoot'",
    )
    parser.add_argument(
        "--variant",
        help="Variant used to expand ${variant}. Default: config field 'defaultVariant'",
    )
    parser.add_argument(
        "--output-dir",
        help="Output directory; overrides config.outputDir. Required via CLI or config; no default",
    )
    parser.add_argument(
        "--log-dir",
        help="Log directory, separate from --output-dir; overrides config.logDir. "
             "Required via CLI or config; no default",
    )
    parser.add_argument(
        "--log-level",
        type=str.upper,
        choices=LOG_LEVELS,
        help="Override config field 'logLevel', case-insensitive (default: INFO)",
    )
    parser.add_argument(
        "--arxml-file-list",
        help="Use an existing ARXML list file instead of running the EB importer. "
             "Overrides config field 'arxmlFileList'",
    )
    parser.add_argument(
        "--exclude-arxml",
        action="append",
        help="Exclude a filename or projectRoot-relative ARXML path; repeat for multiple entries; overrides config.excludeArxml",
    )
    parser.add_argument(
        "--white-table",
        help="Use this white table as input (white mode) regardless of config 'greenTables'/'whiteTable'",
    )
    return parser


def main(default_config: Path | None = None) -> int:
    args = build_argument_parser(default_config).parse_args()
    started = perf_counter()
    try:
        results = run(
            config_paths=[filesystem_path(path).resolve()
                          for path in args.config or [str(
                              default_config if default_config is not None else Path.cwd() / "config.json")]],
            project_root=args.project_root,
            variant=args.variant,
            output_dir=args.output_dir,
            log_level=args.log_level,
            file_list=args.arxml_file_list,
            log_dir=args.log_dir,
            white_table=args.white_table,
            exclude_arxml=args.exclude_arxml,
        )
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        LOGGER.error("%s failed; no new artifacts published: %s", TOOL_NAME, exc)
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
