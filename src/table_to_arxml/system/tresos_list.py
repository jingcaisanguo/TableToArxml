"""用 EB tresos 的导入器生成 ARXML 清单（``*.lst``），并按 tresos 工程的约定读取。

清单由 EB 的 ``PrefSystemImporter --file-list`` 从 tresos 导入器配置（``.xdm``）生成，
与第三方脚本 ``third_party/autosar_builder/gen_ab_project/gen_ab_project.py`` 使用的方式一致，因此清单内容与工程的
Autosar Builder 工程保持同源。清单条目的解析规则见 :mod:`table_to_arxml.common.arxml_list`。

``PrefSystemImporter`` 由 EB 安装提供，需要在 PATH 中可见（CMB 通过 conan profile 注入）。
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Iterable

from ..common.arxml_list import ArxmlExclusions, read_arxml_list
from ..common.paths import filesystem_path

LOGGER = logging.getLogger(__name__)

IMPORTER = "PrefSystemImporter"
TRESOS_OUTPUT_BASE_DIR_ENV = "TRESOS_OUTPUT_BASE_DIR"
TRESOS_PROJECT_SUBDIR = "tresos"


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
    LOGGER.info("Running: %s", subprocess.list2cmdline(command))
    return_code = subprocess.call(command)
    if return_code != 0:
        raise RuntimeError(f"{IMPORTER} failed with exit code {return_code}")
    return output_file


def normalize_generated_entry(entry: str, build_dir_name: str) -> str:
    """EB 返回的生成目录条目缺少 ``tresos`` 这一层，这里补齐。"""

    parts = entry.replace("\\", "/").split("/")
    if build_dir_name and build_dir_name in parts:
        insert_at = parts.index(build_dir_name) + 1
        if insert_at < len(parts) and parts[insert_at].casefold() != TRESOS_PROJECT_SUBDIR:
            parts.insert(insert_at, TRESOS_PROJECT_SUBDIR)
    return "/".join(parts)


def read_generated_list(
    list_file: Path,
    project_root: Path,
    tresos_project: Path,
    build_dir_name: str,
    exclusions: ArxmlExclusions,
    strict: bool,
) -> list[Path]:
    """读取 EB 导入器生成的清单：相对条目再按 tresos 工程目录查找，生成目录补齐 ``tresos`` 层级。"""

    return read_arxml_list(
        list_file,
        project_root,
        search_dirs=(tresos_project,),
        normalize_entry=lambda entry: normalize_generated_entry(entry, build_dir_name),
        exclusions=exclusions,
        strict=strict,
    )
