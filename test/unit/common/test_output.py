"""单文件原子替换、批量失败恢复，以及恢复再次失败时的诊断。"""

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


def test_fsync_failure_removes_temporary_file(tmp_path, monkeypatch):
    target = tmp_path / "target"
    target.write_bytes(b"original")

    def fail(_descriptor):
        raise OSError("fsync failed")

    monkeypatch.setattr(output.os, "fsync", fail)
    with pytest.raises(OSError, match="fsync failed"):
        write_files_atomically([(target, b"new")])
    assert target.read_bytes() == b"original"
    assert list(tmp_path.iterdir()) == [target]


def test_failed_restore_does_not_stop_other_restores(tmp_path, monkeypatch):
    first, second, third = [tmp_path / name for name in ("first", "second", "third")]
    first.write_bytes(b"first original")
    second.write_bytes(b"second original")
    real_replace = os.replace

    def failing_replace(source, destination):
        if destination == third:
            raise OSError("write failed")
        if destination == second and str(source).endswith(".restore.tmp"):
            raise OSError("restore failed")
        real_replace(source, destination)

    monkeypatch.setattr(output.os, "replace", failing_replace)
    with pytest.raises(RuntimeError, match="recovery incomplete") as caught:
        write_files_atomically([(first, b"new"), (second, b"new"), (third, b"new")])
    assert "write failed" in str(caught.value)
    assert str(second) in str(caught.value)
    assert "restore failed" in str(caught.value)
    assert isinstance(caught.value.__cause__, OSError)
    assert first.read_bytes() == b"first original"
    assert second.read_bytes() == b"new"
    assert not third.exists()
    assert not list(tmp_path.glob(".*.tmp"))


@pytest.mark.parametrize("names", [("same.txt", "same.txt"), ("Pair.txt", "pair.txt")])
def test_duplicate_output_paths_are_rejected_before_writing(tmp_path, names):
    with pytest.raises(ValueError, match="Conflicting output path"):
        write_files_atomically([(tmp_path / name, b"new") for name in names])
    assert list(tmp_path.iterdir()) == []
