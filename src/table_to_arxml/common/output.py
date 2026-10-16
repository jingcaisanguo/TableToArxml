"""原子写出：先在目标目录写临时文件，再逐个替换；任一步失败时恢复已替换的文件。"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Sequence


def _write_temporary(destination: Path, content: bytes, suffix: str) -> Path:
    with tempfile.NamedTemporaryFile(
        dir=destination.parent, prefix=f".{destination.name}.", suffix=suffix, delete=False
    ) as temporary:
        temporary.write(content)
        temporary.flush()
        os.fsync(temporary.fileno())
        return Path(temporary.name)


def _create_directories(directory: Path, created: list[Path]) -> None:
    missing: list[Path] = []
    while not directory.exists():
        missing.append(directory)
        directory = directory.parent
    for path in reversed(missing):
        path.mkdir()
        created.append(path)


def write_files_atomically(contents: Sequence[tuple[Path, bytes]]) -> None:
    """把 ``(目标路径, 内容)`` 全部写出，或在失败时尽量保持所有目标的原状。

    已存在的目标必须是普通文件。失败时恢复原内容、删除新建文件，并删除本次新建且已为空的目录。
    """

    pending: list[tuple[Path, bytes, bytes | None]] = []
    for destination, content in contents:
        if destination.exists() and not destination.is_file():
            raise ValueError(f"Output target is not a file: {destination}")
        pending.append((destination, content, destination.read_bytes() if destination.is_file() else None))

    created_directories: list[Path] = []
    temporary_files: list[Path] = []
    replaced: list[tuple[Path, bytes | None]] = []
    try:
        for destination, content, _original in pending:
            _create_directories(destination.parent, created_directories)
            temporary_files.append(_write_temporary(destination, content, ".tmp"))
        for (destination, _content, original), temporary_path in zip(pending, temporary_files):
            os.replace(temporary_path, destination)
            replaced.append((destination, original))
    except Exception:
        for destination, original in reversed(replaced):
            if original is None:
                destination.unlink(missing_ok=True)
            else:
                os.replace(_write_temporary(destination, original, ".restore.tmp"), destination)
        for temporary_path in temporary_files:
            temporary_path.unlink(missing_ok=True)
        for directory in reversed(created_directories):
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
        raise
    finally:
        for temporary_path in temporary_files:
            temporary_path.unlink(missing_ok=True)
