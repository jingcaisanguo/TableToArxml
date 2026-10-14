"""命令行与 JSON 配置合并成一个冻结的 :class:`Options`。

JSON 键与命令行参数一一对应：参数名为 JSON 键的 kebab-case（``defaultVariant`` 对应 ``--variant``），
命令行优先。命令行相对路径以当前工作目录为基准；JSON 中的相对路径以配置文件所在目录为基准，
支持 ``${variant}`` 替换和 ``*`` 唯一匹配。
"""

from __future__ import annotations

import argparse
import glob
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .lib.arxml_backend import BACKENDS, clean_message

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
CONFIG_KEYS = (
    "defaultVariant",
    "projectRoot",
    "arxmlFileList",
    "workbook",
    "strictList",
    "excludeArxml",
    "logDir",
    "logLevel",
    "backend",
    "inPlace",
    "outputDir",
    "dryRun",
)
PATH_KEYS = ("projectRoot", "arxmlFileList", "workbook", "logDir", "outputDir")
DIRECTORY_KEYS = ("projectRoot", "logDir", "outputDir")
REQUIRED_KEYS = ("projectRoot", "arxmlFileList", "workbook", "logDir")
BOOLEAN_KEYS = ("strictList", "inPlace", "dryRun")
OUTPUT_KEYS = ("inPlace", "outputDir", "dryRun")


@dataclass(frozen=True)
class Options:
    project_root: Path
    arxml_file_list: Path
    workbook: Path
    log_dir: Path
    log_level: str = "INFO"
    backend: str = "armodel"
    strict_list: bool = False
    exclude_arxml: tuple[str, ...] = ()
    in_place: bool = False
    output_dir: Path | None = None
    dry_run: bool = False
    variant: str | None = None


def attribute(key: str) -> str:
    """JSON 键对应的 :class:`Options` 字段名。"""

    return "variant" if key == "defaultVariant" else re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower()


def option_flag(key: str) -> str:
    """JSON 键对应的命令行参数。"""

    return "--variant" if key == "defaultVariant" else "--" + re.sub(r"(?<!^)(?=[A-Z])", "-", key).lower()


def resolve_config_path(value: object, key: str, variant: str | None, config_dir: Path) -> Path:
    """按配置文件目录解析路径，先替换 variant，再匹配唯一文件或目录。"""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Config option {key} must be a non-empty path string")
    if "${variant}" in value:
        if variant is None:
            raise ValueError(f"Config option {key} uses ${{variant}}; provide --variant or config defaultVariant")
        value = value.replace("${variant}", variant)
    path = Path(value.replace("\\", "/"))
    if not path.is_absolute():
        path = config_dir / path
    if glob.has_magic(str(path)):
        directory = key in DIRECTORY_KEYS
        matches = sorted({
            candidate.resolve()
            for match in glob.glob(str(path))
            for candidate in (Path(match),)
            if (candidate.is_dir() if directory else candidate.is_file())
        })
        if not matches:
            raise ValueError(f"Config option {key}: no paths match {path}")
        if len(matches) != 1:
            raise ValueError(
                f"Config option {key}: pattern {path} matches multiple paths: "
                + ", ".join(str(match) for match in matches)
            )
        return matches[0]
    return path.resolve()


def resolve_cli_path(value: str, option: str) -> Path:
    """命令行路径只接受绝对路径或相对当前工作目录的普通路径。"""

    if any(token in value for token in ("*", "?", "${")):
        raise ValueError(f"{option} must be a literal path; templates and wildcards are only supported in JSON")
    path = Path(value)
    if not path.is_absolute() and (path.drive or path.root):
        raise ValueError(f"{option} must be absolute or relative to the working directory: {value}")
    return (Path.cwd() / path).resolve()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Add component ports, interfaces and data types to ARXML from an Excel workbook.",
        allow_abbrev=False,
    )
    parser.add_argument("--config", help="JSON configuration; paths inside it are relative to its directory")
    parser.add_argument("--variant", help="Replace ${variant} in JSON paths (overrides config defaultVariant)")
    parser.add_argument("--project-root", help="Project root; relative Target ARXML and list entries use it")
    parser.add_argument("--workbook", help="Excel workbook; required via CLI or config")
    parser.add_argument("--arxml-file-list", help="ARXML list file; required via CLI or config")
    parser.add_argument(
        "--strict-list",
        action="store_true",
        default=None,
        help="Fail on any invalid or missing list entry instead of logging a warning and skipping it",
    )
    parser.add_argument(
        "--exclude-arxml",
        action="append",
        help="Exclude a filename or projectRoot-relative ARXML path; repeat for multiple entries; overrides config.excludeArxml",
    )
    parser.add_argument("--log-dir", help="Log directory; required via CLI or config; no default")
    parser.add_argument(
        "--log-level", type=str.upper, choices=LOG_LEVELS, help="Log level, case-insensitive (default: INFO)"
    )
    parser.add_argument("--backend", choices=sorted(BACKENDS), help="ARXML parsing backend (default: armodel)")
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument(
        "--in-place", action="store_true", default=None, help="Explicitly overwrite affected ARXML files"
    )
    output_group.add_argument(
        "--output-dir", help="Write changed files below this directory, keeping their projectRoot-relative layout"
    )
    output_group.add_argument(
        "--dry-run", action="store_true", default=None, help="Build and validate changes without writing files"
    )
    return parser


def _read_config(path: Path) -> dict[str, object]:
    try:
        config = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read config file {path}: {clean_message(error)}") from error
    if not isinstance(config, dict):
        raise ValueError(f"Config file must contain a JSON object: {path}")
    unknown = sorted(set(config) - set(CONFIG_KEYS))
    if unknown:
        raise ValueError(
            f"Unknown config option(s) in {path}: {', '.join(unknown)}; supported: {', '.join(CONFIG_KEYS)}"
        )
    return config


def _merge(args: argparse.Namespace) -> dict[str, object]:
    """命令行优先，未提供的项取自 JSON；返回 ``JSON 键 -> 值``，路径均已解析为绝对路径。"""

    values: dict[str, object] = {}
    for key in CONFIG_KEYS:
        value = getattr(args, attribute(key))
        if key in PATH_KEYS and value is not None:
            value = resolve_cli_path(value, option_flag(key))
        values[key] = value

    if args.config is None:
        return values
    config_path = resolve_cli_path(args.config, "--config")
    config = _read_config(config_path)
    if values["defaultVariant"] is None:
        values["defaultVariant"] = config.get("defaultVariant")
    variant = values["defaultVariant"]
    if variant is not None and (not isinstance(variant, str) or not variant.strip()):
        raise ValueError("--variant or config defaultVariant must be a non-empty string")
    for key, value in config.items():
        if values[key] is not None:
            continue
        if key in PATH_KEYS and value is not None:
            value = resolve_config_path(value, key, variant, config_path.parent)
        values[key] = value
    return values


def _options(values: dict[str, object]) -> Options:
    missing = [option_flag(key) for key in REQUIRED_KEYS if values[key] is None]
    if missing:
        raise ValueError(", ".join(missing) + " required unless supplied by --config")
    log_level = "INFO" if values["logLevel"] is None else values["logLevel"]
    if not isinstance(log_level, str) or log_level.upper() not in LOG_LEVELS:
        raise ValueError(f"logLevel must be one of: {', '.join(LOG_LEVELS)}")
    backend = "armodel" if values["backend"] is None else values["backend"]
    if backend not in BACKENDS:
        raise ValueError(f"backend must be one of: {', '.join(sorted(BACKENDS))}")
    for key in BOOLEAN_KEYS:
        if values[key] is None:
            values[key] = False
        if not isinstance(values[key], bool):
            raise ValueError(f"{key} must be true or false")
    exclude_arxml = [] if values["excludeArxml"] is None else values["excludeArxml"]
    if not isinstance(exclude_arxml, list) or any(
        not isinstance(value, str) or not value.strip() for value in exclude_arxml
    ):
        raise ValueError("excludeArxml must be a list of non-empty strings")
    if sum(values[key] is not None and values[key] is not False for key in OUTPUT_KEYS) > 1:
        raise ValueError("Only one of inPlace, outputDir, and dryRun may be selected")
    return Options(
        project_root=values["projectRoot"],
        arxml_file_list=values["arxmlFileList"],
        workbook=values["workbook"],
        log_dir=values["logDir"],
        log_level=log_level.upper(),
        backend=backend,
        strict_list=values["strictList"],
        exclude_arxml=tuple(exclude_arxml),
        in_place=values["inPlace"],
        output_dir=values["outputDir"],
        dry_run=values["dryRun"],
        variant=values["defaultVariant"],
    )


def parse_args(argv: Sequence[str] | None = None) -> Options:
    """解析命令行并合并 JSON 配置；参数错误时按 argparse 惯例退出码为 2。

    三个输出选项互斥，均未指定时等同于只验证不写文件。
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return _options(_merge(args))
    except ValueError as error:
        parser.error(clean_message(error))
