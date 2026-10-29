"""Filesystem path matching and output identity checks shared by both tools."""

from __future__ import annotations

import glob
import os
from pathlib import Path, PureWindowsPath
from typing import Iterable


def filesystem_path(value: str) -> Path:
    if os.name != "nt" and PureWindowsPath(value).drive:
        raise ValueError(f"Windows drive/UNC path cannot be used on this platform: {value}")
    if "${" in value:
        raise ValueError(f"Unresolved placeholder in filesystem path: {value}")
    path = Path(value.replace("\\", os.sep).replace("/", os.sep))
    if not path.is_absolute() and (path.drive or path.root):
        raise ValueError(f"Path must be absolute or relative to the working directory: {value}")
    return path


def literal_path_exists(path: Path) -> bool:
    """Some Windows drives report wildcard paths as existing, so check magic parts in the directory listing."""

    if not path.exists():
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
    if not glob.has_magic(str(entry)) or (
            (literal_name or not glob.has_magic(entry.name)) and literal_path_exists(candidate)):
        return [candidate]
    # Existing directory names (including brackets) are literals, not glob syntax.
    parent = candidate
    tail: list[str] = []
    while parent == candidate or not (parent.is_dir() and literal_path_exists(parent)):
        if parent == parent.parent:
            break
        tail.append(parent.name)
        parent = parent.parent
    pattern = str(Path(glob.escape(str(parent))).joinpath(*reversed(tail)))
    return sorted((Path(match) for match in glob.glob(pattern, recursive=True)),
                  key=lambda path: str(path).casefold())


def same_file(first: Path, second: Path) -> bool:
    if first.resolve() == second.resolve():
        return True
    return first.exists() and second.exists() and first.samefile(second)


def validate_output_paths(destinations: Iterable[Path], protected: Iterable[Path] = ()) -> None:
    """Reject duplicate outputs and aliases of protected input files before writing."""

    seen: list[Path] = []
    keys: set[str] = set()
    sources = tuple(protected)
    for destination in destinations:
        key = str(destination.resolve()).casefold()
        if key in keys or any(same_file(destination, previous) for previous in seen):
            raise ValueError(f"Conflicting output path: {destination}")
        for source in sources:
            if same_file(destination, source):
                raise ValueError(
                    f"Output path overlaps an input file: {destination} -> {source}; "
                    "use --in-place explicitly to overwrite source ARXML"
                )
        keys.add(key)
        seen.append(destination)
