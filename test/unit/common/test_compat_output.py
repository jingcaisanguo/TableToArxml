"""Atomic output: unavailable drives, preserved file metadata and short temporary names."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from table_to_arxml.common import output
from table_to_arxml.common.output import write_files_atomically

FILE_ATTRIBUTE_HIDDEN = 0x2
FILE_ATTRIBUTE_SYSTEM = 0x4


def test_missing_drive_or_share_fails_instead_of_looping(tmp_path, monkeypatch):
    calls = []

    def missing(self, *args, **kwargs):
        calls.append(self)
        if len(calls) > 100:
            raise RuntimeError("directory search does not stop at the root")
        return False

    # A missing drive (Z:\) or UNC share is its own parent and never exists.
    with monkeypatch.context() as patch:
        patch.setattr(Path, "exists", missing)
        with pytest.raises(FileNotFoundError, match="drive or network share"):
            write_files_atomically([(tmp_path / "out" / "x.arxml", b"x")])


@pytest.mark.skipif(os.name != "nt", reason="Windows drive letters")
def test_unmapped_windows_drive_fails_with_message():
    letter = next((letter for letter in "ZYXWVUTSRQPONMLKJIHGFED" if not os.path.exists(f"{letter}:\\")), None)
    if letter is None:
        pytest.skip("No unused drive letter")
    with pytest.raises(FileNotFoundError, match="drive or network share"):
        write_files_atomically([(Path(f"{letter}:\\out\\x.arxml"), b"x")])


def test_temporary_names_are_short(tmp_path, monkeypatch):
    target = tmp_path / "Long_Component_Name_With_Many_Characters.arxml"
    target.write_bytes(b"old")
    real_replace = os.replace
    sources = []

    def replace(source, destination):
        sources.append(Path(source))
        real_replace(source, destination)

    monkeypatch.setattr(output.os, "replace", replace)
    write_files_atomically([(target, b"new")])
    assert target.read_bytes() == b"new"
    assert [len(source.name) for source in sources] == [len(".0123abcd.tmp")]


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_posix_permissions_are_preserved_and_new_files_follow_umask(tmp_path):
    existing = tmp_path / "existing.arxml"
    existing.write_bytes(b"old")
    existing.chmod(0o640)
    created = tmp_path / "created.arxml"
    umask = os.umask(0o022)
    try:
        write_files_atomically([(existing, b"new"), (created, b"new")])
    finally:
        os.umask(umask)
    assert stat.S_IMODE(existing.stat().st_mode) == 0o640
    assert stat.S_IMODE(created.stat().st_mode) == 0o644


@pytest.mark.skipif(os.name != "nt", reason="Windows file attributes")
def test_windows_hidden_and_system_attributes_are_preserved(tmp_path):
    target = tmp_path / "hidden.arxml"
    target.write_bytes(b"old")
    output._set_windows_attributes(target, FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM)
    try:
        write_files_atomically([(target, b"new")])
        attributes = os.stat(target).st_file_attributes
        assert target.read_bytes() == b"new"
        assert attributes & FILE_ATTRIBUTE_HIDDEN and attributes & FILE_ATTRIBUTE_SYSTEM
    finally:
        output._set_windows_attributes(target, stat.FILE_ATTRIBUTE_NORMAL)


@pytest.mark.skipif(os.name != "nt", reason="Windows read-only files cannot be replaced")
def test_windows_read_only_target_still_fails_and_rolls_back(tmp_path):
    first = tmp_path / "first.arxml"
    first.write_bytes(b"first")
    locked = tmp_path / "locked.arxml"
    locked.write_bytes(b"locked")
    locked.chmod(stat.S_IREAD)
    try:
        with pytest.raises(PermissionError):
            write_files_atomically([(first, b"new"), (locked, b"new")])
        assert first.read_bytes() == b"first"
        assert locked.read_bytes() == b"locked"
        assert sorted(path.name for path in tmp_path.iterdir()) == ["first.arxml", "locked.arxml"]
    finally:
        locked.chmod(stat.S_IREAD | stat.S_IWRITE)
