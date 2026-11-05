"""读取 ARXML 清单（``*.lst``），两个工具使用同一套规则。

- 清单按 UTF-8 读取（允许 BOM）；带 BOM 的 UTF-16 也可读取；不是 UTF-8 时按系统 ANSI 代码页
  （Windows，如 EB 导入器在中文路径下生成的 GBK 清单）或 locale 编码读取，并记录所用编码。
- 每行一个条目；空行和 ``#`` 开头的行忽略，条目两端的引号会去掉，反斜杠按路径分隔符处理。
- 相对条目在 ``projectRoot`` 和调用方给出的其他基准目录下查找，多处都存在时全部收集；
  条目可以含通配符（支持 ``**``），只保留扩展名为 ``.arxml`` 的文件。
- 条目缺失、不是 ``.arxml``、通配符没有匹配或路径在本平台无效时记录 WARNING 并跳过；``strict=True`` 时直接失败。
- ``excludeArxml`` 的值不含 ``/``、``\\`` 时为文件名（任意位置），否则为相对 ``projectRoot`` 的路径；
  正则排除匹配完整路径，不区分大小写。
- 返回规范化的绝对路径，但不解析符号链接和 junction，以便按清单中的目录布局映射输出；
  按解析链接后的实际文件去重，同一文件多次出现只保留第一次。
"""

from __future__ import annotations

import codecs
import locale
import logging
import os
import re
from pathlib import Path
from typing import Callable, Iterable, Sequence

from .messages import clean_message
from .paths import absolute_path, expand_path, filesystem_path, has_wildcard, is_file, literal_path_exists, path_key

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
        self.paths: set[str] = set()
        for value in exclude_arxml:
            text = value.strip().strip('"')
            # 由原始字符串判断是否带目录：pathlib 会把 ``./Foo.arxml`` 规范成单纯的文件名。
            if "/" in text or "\\" in text:
                self.paths.add(path_key(project_root / Path(text.replace("\\", "/"))))
            else:
                self.names.add(text.casefold())
        self.patterns = [re.compile(pattern, flags=re.IGNORECASE) for pattern in patterns]

    def matches(self, path: Path, resolved: Path | None = None) -> bool:
        """``path`` 为清单中的路径；文件名和正则同时检查链接本身及其指向的实际文件。"""

        resolved = absolute_path(path) if resolved is None else resolved
        candidates = (path,) if resolved == path else (path, resolved)
        return (
            any(candidate.name.casefold() in self.names for candidate in candidates)
            or os.path.normcase(str(resolved)) in self.paths
            or any(pattern.match(str(candidate)) for pattern in self.patterns for candidate in candidates)
        )


def _problem(message: str, strict: bool, error_type: type[Exception]) -> None:
    if strict:
        raise error_type(message)
    LOGGER.warning("%s; skipped", message)


def _legacy_encoding() -> str:
    """Windows 为 ANSI 代码页（如 ``cp936``），其他平台为 locale 编码。"""

    if os.name == "nt":
        import ctypes

        return f"cp{ctypes.windll.kernel32.GetACP()}"
    return locale.getpreferredencoding(False)


def _read_list_text(list_file: Path) -> str:
    data = list_file.read_bytes()
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError as error:
            raise ValueError(f"Cannot decode UTF-16 ARXML list {list_file}: {error}") from error
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        utf8_error = error
    encoding = _legacy_encoding()
    try:
        fallback = codecs.lookup(encoding).name != "utf-8"
    except LookupError:
        fallback = False
    if not fallback:
        raise ValueError(f"ARXML list is not valid UTF-8: {list_file}: {utf8_error}") from utf8_error
    try:
        text = data.decode(encoding)
    except UnicodeDecodeError:
        raise ValueError(
            f"ARXML list is neither UTF-8 nor {encoding}: {list_file}: {utf8_error}") from utf8_error
    LOGGER.info("ARXML list %s is not UTF-8; decoded with the system encoding %s", list_file, encoding)
    return text


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

    if not is_file(list_file):
        raise FileNotFoundError(f"ARXML list not found: {list_file}")
    exclusions = exclusions or ArxmlExclusions(project_root)
    files: list[Path] = []
    seen: set[str] = set()

    for line_number, line in enumerate(_read_list_text(list_file).splitlines(), start=1):
        entry = line.strip().strip('"')
        if not entry or entry.startswith("#"):
            continue
        if normalize_entry is not None:
            entry = normalize_entry(entry)
        location = f"{list_file}:{line_number}"
        try:
            entry_path = filesystem_path(entry)
        except ValueError as error:
            _problem(f"Invalid ARXML list entry at {location}: {clean_message(error)}", strict, ValueError)
            continue
        bases = [entry_path] if entry_path.is_absolute() else [
            base / entry_path for base in (project_root, *search_dirs)
        ]

        literal_matches = [path for path in bases if is_file(path) and literal_path_exists(path)]
        # 文件名本身可能含 ``[`` 等通配符字符：只要按字面存在，就不再当作通配符展开。
        if has_wildcard(entry) and not literal_matches:
            candidates = sorted(
                {match for base in (project_root, *search_dirs) for match in expand_path(entry_path, base)},
                key=lambda path: str(path).casefold(),
            )
            candidates = [path for path in candidates if is_file(path) and path.suffix.casefold() == ARXML_SUFFIX]
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
            path = absolute_path(candidate, resolve_links=False)
            resolved = absolute_path(path)
            if exclusions.matches(path, resolved):
                LOGGER.info("Exclude ARXML file: %s", path)
                continue
            key = os.path.normcase(str(resolved))
            if key in seen:
                LOGGER.info("Skip duplicate ARXML entry at %s: %s", location, path)
                continue
            seen.add(key)
            files.append(path)

    if not files:
        raise ValueError(f"No usable ARXML files found in {list_file}")
    LOGGER.info("Resolved %d ARXML file(s) from %s", len(files), list_file)
    return files