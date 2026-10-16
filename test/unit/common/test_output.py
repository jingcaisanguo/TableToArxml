"""原子写出：全部成功或保持原状。"""

from __future__ import annotations

import os

import pytest

from table_to_arxml.common import output
from table_to_arxml.common.output import write_files_atomically


def test_writes_new_and_existing_files(tmp_path):
    existing = tmp_path / "existing.txt"
    existing.write_bytes(b"old")
    write_files_atomically([(existing, b"new"), (tmp_path / "a" / "b" / "created.txt", b"created")])
    assert existing.read_bytes() == b"new"
    assert (tmp_path / "a" / "b" / "created.txt").read_bytes() == b"created"
    assert not [path for path in tmp_path.rglob(".*.tmp")]


def test_failure_restores_originals_and_removes_created_directories(tmp_path, monkeypatch):
    first = tmp_path / "first.txt"
    first.write_bytes(b"original")
    second = tmp_path / "new" / "second.txt"
    real_replace = os.replace
    calls = []

    def failing_replace(source, destination):
        calls.append(destination)
        if len(calls) == 2:
            raise OSError("disk full")
        real_replace(source, destination)

    monkeypatch.setattr(output.os, "replace", failing_replace)
    with pytest.raises(OSError, match="disk full"):
        write_files_atomically([(first, b"changed"), (second, b"content")])
    monkeypatch.setattr(output.os, "replace", real_replace)
    assert first.read_bytes() == b"original"
    assert not (tmp_path / "new").exists()
    assert sorted(path.name for path in tmp_path.iterdir()) == ["first.txt"]


def test_directory_target_is_rejected(tmp_path):
    (tmp_path / "target").mkdir()
    with pytest.raises(ValueError, match="not a file"):
        write_files_atomically([(tmp_path / "target", b"x")])
