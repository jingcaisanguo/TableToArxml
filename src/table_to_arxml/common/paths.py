"""Filesystem path matching and output identity checks shared by both tools."""

from __future__ import annotations

import glob
import os
import re
from pathlib import Path
from typing import Iterable

_WINDOWS = os.name == "nt"
_LONG_PATH_PREFIX = re.compile(r"^[\\/]{2}\?[\\/](UNC[\\/])?", re.IGNORECASE)
_WINDOWS_ONLY_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")
DEFAULT_OVERLAP_HINT = "choose an output location that does not overwrite input files"


def strip_long_path_prefix(value: str) -> str:
    r"""Drop a Windows ``\\?\`` or ``\\?\UNC\`` prefix so its ``?`` is not mistaken for a wildcard."""

    match = _LONG_PATH_PREFIX.match(value) if _WINDOWS else None
    if match is None:
        return value
    return ("\\\\" if match.group(1) else "") + value[match.end():]


def has_wildcard(value: str) -> bool:
    return glob.has_magic(strip_long_path_prefix(value))


def filesystem_path(value: str) -> Path:
    if not _WINDOWS and _WINDOWS_ONLY_PATH.match(value):
        raise ValueError(f"Windows drive/UNC path cannot be used on this platform: {value}")
    if "${" in value:
        raise ValueError(f"Unresolved placeholder in filesystem path: {value}")
    path = Path(value.replace("\\", os.sep).replace("/", os.sep))
    if not path.is_absolute() and (path.drive or path.root):
        raise ValueError(f"Path must be absolute or relative to the working directory: {value}")
    return path


def absolute_path(
    path: str | os.PathLike[str], base: str | os.PathLike[str] | None = None, *, resolve_links: bool = True,
) -> Path:
    """Return ``base / path`` as a normalized absolute path (``base`` defaults to the working directory).

    Replaces ``Path.resolve()`` for user paths: on Python 3.9 for Windows it returns a relative path
    for missing files (bpo-38671) and raises for wildcard characters or drives that cannot report a
    final path. ``resolve_links=False`` keeps symlinks and junctions as written.
    """

    joined = os.fspath(path) if base is None else os.path.join(os.fspath(base), os.fspath(path))
    absolute = os.path.abspath(joined)
    if resolve_links:
        try:
            absolute = os.path.realpath(absolute)
        except (OSError, ValueError):
            pass
    return Path(absolute)


def path_key(path: str | os.PathLike[str]) -> str:
    """Identity key for comparing paths: links resolved, case-insensitive only on Windows."""

    return os.path.normcase(str(absolute_path(path)))


def path_exists(path: Path) -> bool:
    """``Path.exists()`` without the ``PermissionError`` raised by Python 3.9-3.12."""

    try:
        return path.exists()
    except (OSError, ValueError):
        return False


def is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except (OSError, ValueError):
        return False


def is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except (OSError, ValueError):
        return False


def literal_path_exists(path: Path) -> bool:
    """Some Windows drives report wildcard paths as existing, so check magic parts in the directory listing."""

    if not path_exists(path):
        return False
    current = Path(path.anchor)
    for part in path.parts[1 if path.anchor else 0:]:
        if glob.has_magic(part):
            try:
                names = {os.path.normcase(entry.name) for entry in os.scandir(current)}
            except OSError:
                return False
            if os.path.normcase(part) not in names:
                return False
        current /= part
    return True


def expand_path(entry: Path, base_dir: Path, *, literal_name: bool = True) -> list[Path]:
    """Prefer an existing literal path; expand only the caller's path expression.

    ``literal_name=False`` always expands wildcards in the final component, for inputs whose
    names cannot legally contain glob characters.
    """

    candidate = entry if entry.is_absolute() else base_dir / entry
    if not has_wildcard(str(entry)) or (
            (literal_name or not glob.has_magic(entry.name)) and literal_path_exists(candidate)):
        return [candidate]
    # Existing directory names (including brackets) are literals, not glob syntax.
    parent = candidate
    tail: list[str] = []
    while parent == candidate or not (is_dir(parent) and literal_path_exists(parent)):
        if parent == parent.parent:
            break
        tail.append(parent.name)
        parent = parent.parent
    pattern = str(Path(glob.escape(str(parent))).joinpath(*reversed(tail)))
    return sorted((Path(match) for match in glob.glob(pattern, recursive=True)),
                  key=lambda path: str(path).casefold())


def same_file(first: Path, second: Path) -> bool:
    if path_key(first) == path_key(second):
        return True
    try:
        return first.exists() and second.exists() and first.samefile(second)
    except (OSError, ValueError):
        return False


def validate_output_paths(
    destinations: Iterable[Path], protected: Iterable[Path] = (), *, overlap_hint: str = DEFAULT_OVERLAP_HINT,
) -> None:
    """Reject duplicate outputs and aliases of protected input files before writing.

    Names that differ only in case conflict only on Windows, matching its file systems.
    ``overlap_hint`` tells the user how the calling tool avoids overwriting inputs.
    """

    seen: list[Path] = []
    keys: set[str] = set()
    sources = tuple(protected)
    for destination in destinations:
        key = path_key(destination)
        if key in keys or any(same_file(destination, previous) for previous in seen):
            raise ValueError(f"Conflicting output path: {destination}")
        for source in sources:
            if same_file(destination, source):
                raise ValueError(f"Output path overlaps an input file: {destination} -> {source}; {overlap_hint}")
        keys.add(key)
        seen.append(destination)
