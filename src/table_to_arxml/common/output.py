"""原子写出：先在目标目录写临时文件，再逐个替换；任一步失败时恢复已替换的文件。"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Sequence

from .paths import validate_output_paths


def _write_temporary(destination: Path, content: bytes, suffix: str) -> Path:
    path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, prefix=f".{destination.name}.", suffix=suffix, delete=False
        ) as temporary:
            path = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        return path
    except OSError as error:
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError as cleanup_error:
                raise OSError(
                    f"Temporary write failed: {error}; could not remove temporary file {path}: {cleanup_error}"
                ) from error
        raise


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

    已存在的目标必须是普通文件。失败时尽力恢复所有文件；恢复再次失败时汇总未恢复路径，
    保留最初异常。跨文件操作不是文件系统事务，不能保证断电或进程被终止时全部恢复。
    """

    validate_output_paths(destination for destination, _content in contents)
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
    except Exception as error:
        recovery_errors: list[str] = []
        for destination, original in reversed(replaced):
            try:
                if original is None:
                    destination.unlink(missing_ok=True)
                else:
                    restore_path = _write_temporary(destination, original, ".restore.tmp")
                    temporary_files.append(restore_path)
                    os.replace(restore_path, destination)
            except OSError as recovery_error:
                recovery_errors.append(f"could not restore {destination}: {recovery_error}")
        for temporary_path in temporary_files:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError as cleanup_error:
                recovery_errors.append(f"could not remove temporary file {temporary_path}: {cleanup_error}")
        for directory in reversed(created_directories):
            try:
                if directory.is_dir() and not any(directory.iterdir()):
                    directory.rmdir()
            except OSError as cleanup_error:
                recovery_errors.append(f"could not remove created directory {directory}: {cleanup_error}")
        if recovery_errors:
            raise RuntimeError(
                f"Output write failed: {error}; recovery incomplete: {'; '.join(recovery_errors)}"
            ) from error
        raise
