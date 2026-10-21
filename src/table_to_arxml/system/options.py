"""System CLI configuration, precedence and validation (no conversion dependencies)."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ..common.config_file import read_config_file
from ..common.errors import ConfigError
from ..common.logs import LOG_LEVELS
from ..common.messages import clean_message
from ..common.names import autosar_path, c_identifier, short_name
from ..common.paths import filesystem_path, validate_output_paths
from .values import DEFAULT_AR_RELEASE, DEFAULT_E2E_WRAPPER, DEFAULT_PROTOTYPE_PREFIX, DEFAULT_SIGNAL_PATH_TEMPLATE, norm

if TYPE_CHECKING:
    from .green_table import GreenWorkbookInfo
    from .white_table import CompositionInput

WHITE_TABLE_SUFFIX = "WhiteTable.xlsx"
MERGED_GREEN_TABLE_SUFFIX = "GreenTables.xlsx"
SYSTEM_ARXML_SUFFIX = "System.arxml"
DEFAULT_BUILD_TYPE = "Debug"
# CMB 约定的 tresos 生成目录，EB 导入器把它作为 TRESOS_OUTPUT_BASE_DIR。
DEFAULT_TRESOS_OUTPUT_BASE_DIR = "build/${variant}.${buildType}"
INTERFACE_MISMATCH_POLICIES = ("error", "skip", "map")
CONFIG_KEYS = frozenset({
    "defaultVariant", "buildType", "projectRoot", "logLevel", "outputDir", "logDir",
    "outputPrefix", "greenTables", "whiteTable", "tresosPreferenceFiles", "arxmlFileList", "strictList",
    "tresosOutputBaseDir", "excludeArxmlPatterns", "excludeArxml", "general", "componentAliases",
    "prototypePrefix", "e2eWrapperComponent", "signalPathTemplate", "arRelease", "interfaceMismatch",
    "interfaceMappings", "generateSystem",
})
GENERAL_CONFIG_KEYS = frozenset({"Composition", "System", "EcuInstance", "RootSwCompositionPrototype", "systemMapping"})
INTERFACE_MAPPING_KEYS = {"providerInterface", "requesterInterface", "dataElements"}


@dataclass(frozen=True)
class RunOptions:
    """命令行参数；``None`` 表示未在命令行指定，取各配置文件中的值。"""

    config_paths: tuple[Path, ...]
    project_root: str | None = None
    variant: str | None = None
    output_dir: str | None = None
    log_dir: str | None = None
    log_level: str | None = None
    arxml_file_list: str | None = None
    white_table: str | None = None
    exclude_arxml: tuple[str, ...] | None = None
    strict_list: bool | None = None


def _load_config(config_path: Path) -> dict:
    config = read_config_file(config_path)
    unknown = sorted(set(config) - CONFIG_KEYS)
    if unknown:
        raise ConfigError(
            f"Unknown config option(s) in {config_path}: {', '.join(unknown)}; "
            f"supported: {', '.join(sorted(CONFIG_KEYS))}")
    return config


def _section(config: dict, key: str) -> dict:
    value = config.get(key, {})
    if not isinstance(value, dict):
        raise ConfigError(f"config.{key} must be an object; got {value!r}")
    return value


def _string_list(config: dict, key: str, required: bool) -> list[str]:
    value = config.get(key)
    if value is None:
        if required:
            raise ConfigError(f"Missing config.{key}")
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise ConfigError(f"config.{key} must be a string or list of strings; got {value!r}")
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ConfigError(f"config.{key}[{index}] must be a string; got {item!r}")
    entries = [item.strip() for item in value if item.strip()]
    if required and not entries:
        raise ConfigError(f"Missing config.{key}")
    return entries


def _required(config: dict, key: str, label: str) -> str:
    if key not in config or config[key] is None or config[key] == "":
        raise ConfigError(f"Missing config.{label}")
    return _string_option(config, key, "")


def _string_option(config: dict, key: str, default: str, allow_empty: bool = False) -> str:
    value = config.get(key, default)
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ConfigError(
            f"config.{key} must be a {'possibly empty' if allow_empty else 'nonempty'} string; got {value!r}"
        )
    return value.strip()


def _boolean_option(config: dict, key: str, default: bool) -> bool:
    value = config.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"config.{key} must be true or false; got {value!r}")
    return value


def _filename_part(value: str, label: str) -> str:
    if value in (".", "..") or re.search(r'[<>:"/\\|?*\x00-\x1f]', value) or value.endswith((".", " ")):
        raise ConfigError(f"{label} must be a portable filename component without a directory: {value}")
    return value


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
    configured = _string_option(config, "projectRoot", "", allow_empty=True)
    if not configured:
        raise ConfigError(f"{config_path}: project root is required via --project-root or config field 'projectRoot'")
    return _resolve_config_path(configured, config_path.parent).resolve()


def _exclude_patterns(config: dict) -> list[str]:
    patterns = _string_list(config, "excludeArxmlPatterns", required=False)
    for pattern in patterns:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ConfigError(f"config.excludeArxmlPatterns: invalid regular expression {pattern!r}: {exc}") from exc
    return patterns


def _interface_mappings(value: object) -> dict[tuple[str, str], dict[str, str]]:
    """校验 ``interfaceMappings``，返回 ``(提供方接口, 请求方接口) -> 数据元素名映射``。"""

    if value is None:
        return {}
    if not isinstance(value, list):
        raise ConfigError(f"interfaceMappings must be a list; got {value!r}")
    mappings: dict[tuple[str, str], dict[str, str]] = {}
    for index, spec in enumerate(value):
        context = f"interfaceMappings[{index}]"
        if not isinstance(spec, dict) or set(spec) != INTERFACE_MAPPING_KEYS:
            raise ConfigError(
                f"{context} entries require providerInterface, requesterInterface and dataElements; got {spec!r}"
            )
        first, second, elements = spec["providerInterface"], spec["requesterInterface"], spec["dataElements"]
        if not all(isinstance(path, str) and path.startswith("/") for path in (first, second)):
            raise ConfigError(f"{context} interface paths must be absolute AUTOSAR paths; got {first!r}, {second!r}")
        for key, path in (("providerInterface", first), ("requesterInterface", second)):
            autosar_path(path, f"{context}.{key}")
        if (not isinstance(elements, dict) or not elements
                or not all(isinstance(key, str) and key and isinstance(name, str) and name
                           for key, name in elements.items())):
            raise ConfigError(f"{context}.dataElements must map provider names to requester names; got {elements!r}")
        for provider_name, requester_name in elements.items():
            short_name(provider_name, f"{context}.dataElements key")
            short_name(requester_name, f"{context}.dataElements[{provider_name!r}]")
        if (first, second) in mappings:
            raise ConfigError(f"Duplicate interfaceMappings entry at {context}: {first} -> {second}")
        mappings[(first, second)] = dict(elements)
    return mappings


@dataclass
class Job:
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
    strict_list: bool
    green_tables: list[tuple[str, Path]] = field(default_factory=list)
    # 加载 ARXML 前预校验时解析的输入表，生成阶段直接复用，不再重复读取 Excel。
    green_infos: list[GreenWorkbookInfo] = field(default_factory=list)
    white_input: CompositionInput | None = None

    @property
    def config_dir(self) -> Path:
        return self.config_path.parent


def _prepare_job(
    config_path: Path, options: RunOptions,
) -> Job:
    """读取并校验单个配置；不依赖 armodel，可在加载 ARXML 前完成。"""

    config_path = config_path.resolve()
    config = _load_config(config_path)
    config_dir = config_path.parent
    overrides = {
        "defaultVariant": options.variant,
        "projectRoot": options.project_root,
        "outputDir": options.output_dir,
        "logDir": options.log_dir,
        "logLevel": options.log_level,
        "arxmlFileList": options.arxml_file_list,
        "whiteTable": options.white_table,
        "excludeArxml": None if options.exclude_arxml is None else list(options.exclude_arxml),
        "strictList": options.strict_list,
    }
    for key, value in overrides.items():
        if value is None:
            continue
        if key in {"projectRoot", "outputDir", "logDir", "arxmlFileList", "whiteTable"}:
            if not isinstance(value, str) or not value.strip():
                raise ConfigError(f"Command-line {key} must be a nonempty path")
            if any(token in value for token in ("*", "?", "${")):
                raise ConfigError(f"Command-line {key} must be a literal path")
            value = str(filesystem_path(value).resolve())
        config[key] = value
    if options.white_table is not None:
        config.pop("greenTables", None)

    resolved_variant = _string_option(config, "defaultVariant", "", allow_empty=True)
    if not resolved_variant:
        raise ConfigError(f"{config_path}: variant is required via --variant or config field 'defaultVariant'")
    _filename_part(resolved_variant, "variant")

    # 产物命名全部由 config.outputPrefix 推导，例如 Bcm -> BcmWhiteTable.xlsx / BcmGreenTables.xlsx / BcmSystem.arxml。
    output_prefix = _filename_part(_required(config, "outputPrefix", "outputPrefix"), "outputPrefix")
    general = _section(config, "general")
    unknown_general = sorted(set(general) - GENERAL_CONFIG_KEYS)
    if unknown_general:
        raise ConfigError(
            f"Unknown config.general option(s) in {config_path}: {', '.join(unknown_general)}; "
            f"supported: {', '.join(sorted(GENERAL_CONFIG_KEYS))}")

    input_white_table: Path | None = None
    if ("greenTables" in config) == ("whiteTable" in config):
        raise ConfigError(
            f"{config_path}: config must contain exactly one of 'greenTables' (green mode) or 'whiteTable' (white mode)")
    elif "whiteTable" in config:
        input_white_table = _resolve_config_path(
            _required(config, "whiteTable", "whiteTable").replace("${variant}", resolved_variant),
            config_dir).resolve()
    prototype_prefix = _string_option(config, "prototypePrefix", DEFAULT_PROTOTYPE_PREFIX, allow_empty=True)
    if prototype_prefix:
        short_name(prototype_prefix, "config.prototypePrefix")
    for alias, target in _section(config, "componentAliases").items():
        if isinstance(target, dict):
            if set(target) != {"type", "prototype"}:
                raise ConfigError(f"componentAliases.{alias} requires type and prototype")
            if not isinstance(target["type"], str) or not isinstance(target["prototype"], str):
                raise ConfigError(f"componentAliases.{alias} requires string values")
            autosar_path(target["type"], f"componentAliases.{alias}.type")
            short_name(target["prototype"], f"componentAliases.{alias}.prototype")
    if general.get("systemMapping"):
        if not isinstance(general["systemMapping"], str):
            raise ConfigError("general.systemMapping must be a string")
        c_identifier(general["systemMapping"], "general.systemMapping")
    signal_template = _string_option(config, "signalPathTemplate", DEFAULT_SIGNAL_PATH_TEMPLATE)
    if not signal_template.startswith("/") or "${signal}" not in signal_template:
        raise ConfigError("config.signalPathTemplate must be absolute and contain ${signal}")
    if "${" in signal_template.replace("${channel}", "").replace("${signal}", ""):
        raise ConfigError("config.signalPathTemplate only supports ${channel} and ${signal}")
    mismatch_policy = _string_option(config, "interfaceMismatch", "error")
    if mismatch_policy not in INTERFACE_MISMATCH_POLICIES:
        raise ConfigError("interfaceMismatch must be error, skip or map")
    generate_system = _boolean_option(config, "generateSystem", True)

    arxml_file_list = None
    if "arxmlFileList" in config:
        configured = _string_option(config, "arxmlFileList", "").replace("${variant}", resolved_variant)
        arxml_file_list = _resolve_config_path(configured, config_dir).resolve()
    reuse_list = arxml_file_list is not None
    build_type = DEFAULT_BUILD_TYPE if reuse_list else (
        _string_option(config, "buildType", DEFAULT_BUILD_TYPE, allow_empty=True) or DEFAULT_BUILD_TYPE
    )
    configured_output = "" if reuse_list else _string_option(
        config, "tresosOutputBaseDir", "", allow_empty=True
    )
    tresos_output_dir = _resolve_config_path(
        configured_output.replace("${variant}", resolved_variant).replace("${buildType}", build_type),
        config_dir) if configured_output else None

    return Job(
        config_path=config_path,
        variant=resolved_variant,
        project_root=_resolve_project_root(config, None, config_path),
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
        log_level=_string_option(config, "logLevel", "", allow_empty=True).upper(),
        output_dir=_optional_directory(config, "outputDir", config_dir),
        log_dir=_optional_directory(config, "logDir", config_dir),
        arxml_file_list=arxml_file_list,
        exclude_patterns=_exclude_patterns(config),
        exclude_arxml=_string_list(config, "excludeArxml", required=False),
        preference_entries=[] if reuse_list else _string_list(config, "tresosPreferenceFiles", required=False),
        build_type=build_type,
        tresos_output_dir=tresos_output_dir,
        strict_list=_boolean_option(config, "strictList", False),
    )


def resolve_run_directory(
    jobs: list[Job], cli_value: str | None, attribute: str, config_key: str, cli_option: str,
) -> Path:
    if cli_value is not None:
        if not isinstance(cli_value, str) or not cli_value.strip():
            raise ConfigError(f"{cli_option} must specify a nonempty directory")
        path = filesystem_path(cli_value.strip())
        return (path if path.is_absolute() else Path.cwd() / path).resolve()

    directories: list[Path] = []
    for job in jobs:
        directory = getattr(job, attribute)
        if directory is None:
            raise ConfigError(
                f"{job.config_path}: directory is required via {cli_option} "
                f"or nonempty config.{config_key}")
        directories.append(directory)
    if any(directory != directories[0] for directory in directories[1:]):
        raise ConfigError(
            f"config.{config_key} must resolve to the same directory across configs; "
            f"use {cli_option} to override")
    return directories[0]


def resolve_log_level(jobs: list[Job], cli_value: str | None) -> str:
    """命令行优先；否则各配置中写了的 logLevel 必须一致，都没写时为 INFO。"""

    if norm(cli_value):
        level = norm(cli_value).upper()
    else:
        levels = {job.log_level for job in jobs} - {""}
        if len(levels) > 1:
            raise ConfigError(
                f"config.logLevel differs across configs: {', '.join(sorted(levels))}; "
                "use --log-level to override")
        level = levels.pop() if levels else "INFO"
    if level not in LOG_LEVELS:
        raise ConfigError(f"Invalid log level: {level}")
    return level


def prepare_jobs(options: RunOptions) -> list[Job]:
    """读取并校验全部配置；不依赖 armodel，配置问题以 ``ConfigError`` 报告。"""

    if not options.config_paths:
        raise ConfigError("At least one config is required")
    if options.white_table and len(options.config_paths) > 1:
        raise ConfigError("--white-table can only be used with a single --config")
    jobs: list[Job] = []
    for config_path in options.config_paths:
        try:
            job = _prepare_job(config_path, options)
        except (ConfigError, ValueError) as error:
            raise ConfigError(f"{config_path}: {clean_message(error)}") from error
        if (options.arxml_file_list is None and job.arxml_file_list is None
                and not job.preference_entries):
            raise ConfigError(
                f"{job.config_path}: an ARXML list is required via --arxml-file-list, "
                "config.arxmlFileList or config.tresosPreferenceFiles")
        jobs.append(job)
    try:
        validate_output_paths(
            Path(f"{job.output_prefix}{suffix}") for job in jobs
            for suffix in (SYSTEM_ARXML_SUFFIX, WHITE_TABLE_SUFFIX, MERGED_GREEN_TABLE_SUFFIX)
        )
    except ValueError as error:
        raise ConfigError(f"config.outputPrefix must be unique across configs: {error}") from error
    return jobs


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="table-to-system-arxml",
        description="Convert green tables into the white table Excel and the composition ARXML",
        allow_abbrev=False)
    parser.add_argument(
        "--config",
        action="append",
        required=True,
        help="Path to a TOML tool configuration (required); repeat to process several configs with one "
             "ARXML load, in the given order",
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
        "--strict-list",
        action="store_true",
        default=None,
        help="Fail on any missing or non-ARXML list entry instead of logging a warning and skipping it",
    )
    parser.add_argument(
        "--exclude-arxml",
        action="append",
        help="Exclude a filename or projectRoot-relative ARXML path; repeat for multiple entries; "
             "overrides config.excludeArxml",
    )
    parser.add_argument(
        "--white-table",
        help="Use this white table as input (white mode) regardless of config 'greenTables'/'whiteTable'",
    )
    return parser
