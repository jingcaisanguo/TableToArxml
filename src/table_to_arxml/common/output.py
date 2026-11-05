"""原子写出：先在目标目录写临时文件，再逐个替换；任一步失败时恢复已替换的文件。

替换已有文件时保留其权限位（POSIX）以及隐藏、系统属性（Windows）；新文件按 umask 创建。
属主、ACL 等其他元数据不保留，替换后的文件继承目录的默认设置。
"""

from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path
from typing import Sequence

from .paths import is_file, path_exists, validate_output_paths

# FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM | FILE_ATTRIBUTE_NOT_CONTENT_INDEXED；
# 只读属性不复制，只读目标照常替换失败并回滚。
_KEPT_WINDOWS_ATTRIBUTES = 0x2 | 0x4 | 0x2000
_TEMPORARY_NAME_ATTEMPTS = 100


def _set_windows_attributes(path: Path, attributes: int) -> None:
    import ctypes
    from ctypes import wintypes

    set_attributes = ctypes.WinDLL("kernel32", use_last_error=True).SetFileAttributesW
    set_attributes.argtypes = (wintypes.LPCWSTR, wintypes.DWORD)
    set_attributes.restype = wintypes.BOOL
    if not set_attributes(str(path), attributes):
        raise ctypes.WinError(ctypes.get_last_error())


def _open_temporary(destination: Path, suffix: str) -> tuple[Path, int]:
    # 名称短于常见目标文件名，未启用长路径的 Windows 上不会因临时文件超出 MAX_PATH。
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    for _attempt in range(_TEMPORARY_NAME_ATTEMPTS):
        path = destination.parent / f".{secrets.token_hex(4)}{suffix}"
        try:
            return path, os.open(path, flags, 0o666)
        except FileExistsError:
            continue
    raise FileExistsError(f"Cannot create a unique temporary file next to {destination}")


def _write_temporary(destination: Path, content: bytes, suffix: str, original: os.stat_result | None) -> Path:
    path, descriptor = _open_temporary(destination, suffix)
    try:
        with os.fdopen(descriptor, "wb") as temporary:
            if original is not None and os.name != "nt":
                os.fchmod(temporary.fileno(), stat.S_IMODE(original.st_mode))
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        kept = getattr(original, "st_file_attributes", 0) & _KEPT_WINDOWS_ATTRIBUTES
        if kept:
            _set_windows_attributes(path, os.stat(path).st_file_attributes | kept)
        return path
    except OSError as error:
        try:
            path.unlink(missing_ok=True)
        except OSError as cleanup_error:
            raise OSError(
                f"Temporary write failed: {error}; could not remove temporary file {path}: {cleanup_error}"
            ) from error
        raise


def _create_directories(directory: Path, created: list[Path]) -> None:
    missing: list[Path] = []
    current = directory
    while not path_exists(current):
        if current.parent == current:
            raise FileNotFoundError(
                f"Cannot create directory {directory}: drive or network share {current} is not available")
        missing.append(current)
        current = current.parent
    for path in reversed(missing):
        path.mkdir()
        created.append(path)


def write_files_atomically(contents: Sequence[tuple[Path, bytes]]) -> None:
    """把 ``(目标路径, 内容)`` 全部写出，或在失败时尽量保持所有目标的原状。

    已存在的目标必须是普通文件。失败时尽力恢复所有文件；恢复再次失败时汇总未恢复路径，
    保留最初异常。跨文件操作不是文件系统事务，不能保证断电或进程被终止时全部恢复。
    """

    validate_output_paths(destination for destination, _content in contents)
    pending: list[tuple[Path, bytes, bytes | None, os.stat_result | None]] = []
    for destination, content in contents:
        if path_exists(destination) and not is_file(destination):
            raise ValueError(f"Output target is not a file: {destination}")
        if is_file(destination):
            pending.append((destination, content, destination.read_bytes(), destination.stat()))
        else:
            pending.append((destination, content, None, None))

    created_directories: list[Path] = []
    temporary_files: list[Path] = []
    replaced: list[tuple[Path, bytes | None, os.stat_result | None]] = []
    try:
        for destination, content, _original, metadata in pending:
            _create_directories(destination.parent, created_directories)
            temporary_files.append(_write_temporary(destination, content, ".tmp", metadata))
        for (destination, _content, original, metadata), temporary_path in zip(pending, temporary_files):
            os.replace(temporary_path, destination)
            replaced.append((destination, original, metadata))
    except Exception as error:
        recovery_errors: list[str] = []
        for destination, original, metadata in reversed(replaced):
            try:
                if original is None:
                    destination.unlink(missing_ok=True)
                else:
                    restore_path = _write_temporary(destination, original, ".restore.tmp", metadata)
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
