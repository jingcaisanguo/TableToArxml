"""原子写出修改后的 ARXML：先写临时文件再替换，中途失败时尽量恢复已替换的文件。"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

LOGGER = logging.getLogger(__name__)


def output_path(source_file: Path, output_dir: Path, project_root: Path) -> Path:
    """把工程内的源文件映射到输出目录下的相同相对路径。"""

    try:
        relative_path = source_file.relative_to(project_root)
    except ValueError as error:
        raise ValueError(f"Target ARXML is outside projectRoot: {source_file}") from error
    return output_dir / relative_path


def _write_temporary(destination: Path, content: bytes, suffix: str) -> Path:
    with tempfile.NamedTemporaryFile(
        dir=destination.parent, prefix=f".{destination.name}.", suffix=suffix, delete=False
    ) as temporary:
        temporary.write(content)
        temporary.flush()
        os.fsync(temporary.fileno())
        return Path(temporary.name)


def write_changes(
    modified: dict[Path, bytes],
    in_place: bool,
    output_dir: Path | None,
    project_root: Path,
) -> None:
    """写出字节发生变化的文件；无实际修改时不触碰任何文件。"""

    if not in_place and output_dir is None:
        raise ValueError("Output directory is required when not writing in place")
    pending: list[tuple[Path, bytes, bytes | None]] = []
    for source_file, document in modified.items():
        if document == source_file.read_bytes():
            continue
        destination = source_file if in_place else output_path(source_file, output_dir, project_root)
        pending.append((destination, document, destination.read_bytes() if destination.is_file() else None))

    temporary_files: list[Path] = []
    replaced: list[tuple[Path, bytes | None]] = []
    try:
        for destination, document, _original in pending:
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary_files.append(_write_temporary(destination, document, ".tmp"))
        for (destination, _document, original), temporary_path in zip(pending, temporary_files):
            os.replace(temporary_path, destination)
            replaced.append((destination, original))
            LOGGER.info("ARXML written to: %s", destination)
    except OSError:
        for destination, original in reversed(replaced):
            if original is None:
                destination.unlink(missing_ok=True)
            else:
                os.replace(_write_temporary(destination, original, ".restore.tmp"), destination)
        raise
    finally:
        for temporary_path in temporary_files:
            temporary_path.unlink(missing_ok=True)
