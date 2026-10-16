"""读取 ARXML 清单（``*.lst``），两个工具使用同一套规则。

- 每行一个条目；空行和 ``#`` 开头的行忽略，条目两端的引号会去掉，反斜杠按路径分隔符处理。
- 相对条目在 ``projectRoot`` 和调用方给出的其他基准目录下查找，多处都存在时全部收集；
  条目可以含通配符（支持 ``**``），只保留扩展名为 ``.arxml`` 的文件。
- 条目缺失、不是 ``.arxml``、通配符没有匹配时记录 WARNING 并跳过；``strict=True`` 时直接失败。
- ``excludeArxml`` 的值为文件名（任意位置）或相对 ``projectRoot`` 的路径；正则排除匹配完整路径，不区分大小写。
- 同一文件多次出现只保留第一次。
"""

from __future__ import annotations

import glob
import logging
import os
import re
from pathlib import Path
from typing import Callable, Iterable, Sequence

LOGGER = logging.getLogger(__name__)

ARXML_SUFFIX = ".arxml"
# 标定用的 swcd 副本不属于系统描述，EB 导入时同样不会使用。
DEFAULT_EXCLUDE_PATTERNS = (r".*[\\/](dem|dcm)[\\/]swcd_calibration[\\/].*",)


class ArxmlExclusions:
    """``excludeArxml`` 与正则排除规则。"""

    def __init__(
        self, project_root: Path, exclude_arxml: Iterable[str] = (), patterns: Iterable[str] = (),
    ) -> None:
        self.names: set[str] = set()
        self.paths: set[Path] = set()
        for value in exclude_arxml:
            excluded = Path(value.strip().strip('"').replace("\\", "/"))
            if excluded.name == str(excluded):
                self.names.add(excluded.name.casefold())
            else:
                self.paths.add((project_root / excluded).resolve())
        self.patterns = [re.compile(pattern, flags=re.IGNORECASE) for pattern in patterns]

    def matches(self, path: Path) -> bool:
        return (
            path.name.casefold() in self.names
            or path.resolve() in self.paths
            or any(pattern.match(str(path)) for pattern in self.patterns)
        )


def _problem(message: str, strict: bool, error_type: type[Exception]) -> None:
    if strict:
        raise error_type(message)
    LOGGER.warning("%s; skipped", message)


def read_arxml_list(
    list_file: Path,
    project_root: Path,
    *,
    search_dirs: Sequence[Path] = (),
    normalize_entry: Callable[[str], str] | None = None,
    exclusions: ArxmlExclusions | None = None,
    strict: bool = False,
) -> list[Path]:
    """读取清单并返回去重后的 ARXML 绝对路径列表；没有可用文件时抛出 ``ValueError``。"""

    if not list_file.is_file():
        raise FileNotFoundError(f"ARXML list not found: {list_file}")
    exclusions = exclusions or ArxmlExclusions(project_root)
    files: list[Path] = []
    seen: set[str] = set()

    for line_number, line in enumerate(list_file.read_text(encoding="utf-8-sig").splitlines(), start=1):
        entry = line.strip().strip('"')
        if not entry or entry.startswith("#"):
            continue
        if normalize_entry is not None:
            entry = normalize_entry(entry)
        location = f"{list_file}:{line_number}"
        entry_path = Path(entry.replace("\\", "/"))
        bases = [entry_path] if entry_path.is_absolute() else [
            base / entry_path for base in (project_root, *search_dirs)
        ]

        literal_matches = [path for path in bases if path.is_file()]
        # 文件名本身可能含 ``[`` 等通配符字符：只要按字面存在，就不再当作通配符展开。
        if glob.has_magic(entry) and not literal_matches:
            candidates = sorted(
                {Path(match) for base in bases for match in glob.glob(str(base), recursive=True)},
                key=lambda path: str(path).casefold(),
            )
            candidates = [path for path in candidates if path.is_file() and path.suffix.casefold() == ARXML_SUFFIX]
            if not candidates:
                _problem(f"No ARXML matches pattern at {location}: {entry}", strict, FileNotFoundError)
                continue
        else:
            # 相对条目在多个基准目录下都存在时全部收集（与 EB 模式原有行为一致）。
            candidates = literal_matches
            if not candidates:
                if exclusions.matches(bases[0]):
                    LOGGER.info("Exclude ARXML file: %s", bases[0])
                else:
                    _problem(f"Listed ARXML not found at {location}: {entry}", strict, FileNotFoundError)
                continue
            if candidates[0].suffix.casefold() != ARXML_SUFFIX:
                _problem(f"Non-ARXML record at {location}: {entry}", strict, ValueError)
                continue

        for candidate in candidates:
            path = candidate.resolve()
            if exclusions.matches(path):
                LOGGER.info("Exclude ARXML file: %s", path)
                continue
            key = os.path.normcase(str(path))
            if key in seen:
                LOGGER.info("Skip duplicate ARXML entry at %s: %s", location, path)
                continue
            seen.add(key)
            files.append(path)

    if not files:
        raise ValueError(f"No usable ARXML files found in {list_file}")
    LOGGER.info("Resolved %d ARXML file(s) from %s", len(files), list_file)
    return files
