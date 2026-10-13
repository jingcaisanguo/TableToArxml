"""用 EB tresos 的导入器生成并读取 ARXML 清单（``*.lst``）。

清单由 EB 的 ``PrefSystemImporter --file-list`` 从 tresos 导入器配置（``.xdm``）生成，
与 ``tools/scripts/gen_ab_project.py`` 使用的方式一致，因此清单内容与工程的
Autosar Builder 工程保持同源。

``PrefSystemImporter`` 由 EB 安装提供，需要在 PATH 中可见（CMB 通过 conan profile 注入）。
"""

from __future__ import annotations

import glob
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path, PureWindowsPath
from typing import Iterable

IMPORTER = "PrefSystemImporter"
TRESOS_OUTPUT_BASE_DIR_ENV = "TRESOS_OUTPUT_BASE_DIR"
TRESOS_PROJECT_SUBDIR = "tresos"
GLOB_CHARS = "*?[]"
# 标定用的 swcd 副本不属于系统描述，EB 导入时同样不会使用。
DEFAULT_EXCLUDE_PATTERNS = (r".*[\\/](dem|dcm)[\\/]swcd_calibration[\\/].*",)


def _has_glob(value: str) -> bool:
    return any(char in value for char in GLOB_CHARS)


def filesystem_path(value: str) -> Path:
    if os.name != "nt" and PureWindowsPath(value).drive:
        raise ValueError(f"Windows drive/UNC path cannot be used on this platform: {value}")
    if "${" in value:
        raise ValueError(f"Unresolved placeholder in filesystem path: {value}")
    return Path(value.replace("\\", os.sep).replace("/", os.sep))


def resolve_preference_files(
    entries: Iterable[str],
    variant: str,
    base_dir: Path,
) -> list[Path]:
    """解析配置中的 tresos 导入器配置文件路径。"""

    resolved: list[Path] = []
    for entry in entries:
        rendered = str(entry).strip().replace("${variant}", variant)
        if not rendered:
            continue
        path = filesystem_path(rendered)
        if not path.is_absolute():
            path = base_dir / path
        if not path.is_file():
            raise FileNotFoundError(f"tresos preference file does not exist: {path}")
        resolved.append(path.resolve())
    return resolved


def tresos_project_dir(preference_files: list[Path]) -> Path:
    """导入器配置位于 ``<tresos 工程>/.prefs``，取其上一级作为 base path。"""

    return preference_files[0].parent.parent


def generate_file_list(
    preference_files: list[Path],
    tresos_output_base_dir: Path,
    output_file: Path,
) -> Path:
    """调用 EB 导入器，把导入器配置引用的全部 ARXML 写入清单文件。"""

    executable = shutil.which(IMPORTER)
    if executable is None:
        raise FileNotFoundError(
            f"{IMPORTER} was not found in PATH; run this tool in the EB tresos environment "
            "or provide a ready-made list via config.arxmlFileList / --arxml-file-list")

    output_file.parent.mkdir(parents=True, exist_ok=True)
    command = (
        [executable, "--file-list", "--base-path", str(tresos_project_dir(preference_files))]
        + [str(path) for path in preference_files]
        + [str(output_file),
           "--env", f"{TRESOS_OUTPUT_BASE_DIR_ENV}={tresos_output_base_dir}"]
    )
    logging.info("Running: %s", subprocess.list2cmdline(command))
    return_code = subprocess.call(command)
    if return_code != 0:
        raise RuntimeError(f"{IMPORTER} failed with exit code {return_code}")
    return output_file


def _normalize_entry(entry: str, build_dir_name: str) -> str:
    """EB 返回的生成目录条目缺少 ``tresos`` 这一层，这里补齐。"""

    parts = str(filesystem_path(entry)).split(os.sep)
    if build_dir_name and build_dir_name in parts:
        insert_at = parts.index(build_dir_name) + 1
        if insert_at < len(parts) and parts[insert_at].casefold() != TRESOS_PROJECT_SUBDIR:
            parts.insert(insert_at, TRESOS_PROJECT_SUBDIR)
    return os.sep.join(parts)


def read_file_list(
    list_file: Path,
    project_root: Path,
    tresos_project: Path,
    build_dir_name: str,
    exclude_patterns: Iterable[str] = DEFAULT_EXCLUDE_PATTERNS,
) -> list[Path]:
    """读取清单文件并展开成去重后的 ARXML 文件列表。

    清单里的相对路径依次按工程根目录和 tresos 工程目录解析，条目支持通配符；
    不存在的条目只告警并跳过，因为清单可能记录尚未生成的文件。
    """

    if not list_file.is_file():
        raise FileNotFoundError(f"ARXML file list does not exist: {list_file}")

    excludes = [re.compile(pattern, flags=re.IGNORECASE) for pattern in exclude_patterns]
    resolved: list[Path] = []
    seen: set[Path] = set()

    with list_file.open("r", encoding="utf-8-sig") as handle:
        for raw_line in handle:
            entry = raw_line.strip()
            if not entry or entry.startswith("#"):
                continue
            entry = _normalize_entry(entry, build_dir_name)

            if os.path.isabs(entry):
                patterns = [entry]
            else:
                patterns = [str(project_root / entry), str(tresos_project / entry)]

            if _has_glob(entry):
                matches = [
                    Path(match)
                    for pattern in patterns
                    for match in glob.glob(pattern, recursive=True)
                ]
            else:
                matches = [Path(pattern) for pattern in patterns if Path(pattern).is_file()]
                if not matches:
                    logging.warning("Skip missing ARXML file: %s", entry)
                    continue

            for match in sorted(matches, key=lambda item: str(item).casefold()):
                candidate = match.resolve()
                if candidate.suffix.casefold() != ".arxml" or not candidate.is_file():
                    continue
                if any(exclude.match(str(candidate)) for exclude in excludes):
                    logging.info("Exclude ARXML file: %s", candidate)
                    continue
                if candidate not in seen:
                    seen.add(candidate)
                    resolved.append(candidate)

    if not resolved:
        raise FileNotFoundError(f"No ARXML files were resolved from {list_file}")
    logging.info("Resolved %s ARXML files from %s", len(resolved), list_file)
    return resolved
