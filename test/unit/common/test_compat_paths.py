"""Path helpers stay absolute and never raise for odd but valid inputs on every supported Python and OS."""

from __future__ import annotations

import os
import types
from pathlib import Path, PurePosixPath

import pytest

from table_to_arxml.common import paths
from table_to_arxml.common.paths import absolute_path, path_key, strip_long_path_prefix, validate_output_paths


def test_absolute_path_is_absolute_for_missing_relative_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert absolute_path("missing/out") == tmp_path / "missing" / "out"
    assert absolute_path("missing", tmp_path / "base") == tmp_path / "base" / "missing"
    assert absolute_path(tmp_path / "a" / ".." / "b") == tmp_path / "b"


def test_absolute_path_does_not_raise_for_wildcards_or_unresolvable_drives(tmp_path, monkeypatch):
    assert absolute_path("wh*.xlsx", tmp_path) == tmp_path / "wh*.xlsx"
    assert absolute_path("w?.xlsx", tmp_path) == tmp_path / "w?.xlsx"

    def unsupported(_path, *args, **kwargs):
        raise OSError(1, "Incorrect function")  # GetFinalPathNameByHandle on some RAM disks

    with monkeypatch.context() as patch:
        patch.setattr(paths.os.path, "realpath", unsupported)
        result = absolute_path("file.arxml", tmp_path)
    assert result == tmp_path / "file.arxml"


def link_directory(link: Path, target: Path) -> None:
    """Create a directory symlink, or a junction on Windows without symlink privileges."""

    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except OSError as error:
        if os.name != "nt":
            pytest.skip(f"Symlink creation unavailable: {error}")
    import _winapi

    _winapi.CreateJunction(str(target), str(link))


def test_absolute_path_keeps_links_only_on_request(tmp_path):
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "link"
    link_directory(link, target)
    assert absolute_path(link / "x.arxml", resolve_links=False) == link / "x.arxml"
    assert absolute_path(link / "x.arxml") == target / "x.arxml"
    assert path_key(link / "x.arxml") == path_key(target / "x.arxml")


@pytest.mark.parametrize("value", ["//opt/proj/x.arxml", "a:b/x.arxml", "/opt/proj/x.arxml"])
def test_posix_accepts_paths_that_only_look_like_windows_paths(monkeypatch, value):
    monkeypatch.setattr(paths, "_WINDOWS", False)
    monkeypatch.setattr(paths, "os", types.SimpleNamespace(name="posix", sep="/"))
    monkeypatch.setattr(paths, "Path", PurePosixPath)
    assert paths.filesystem_path(value) == PurePosixPath(value)


@pytest.mark.parametrize("value", ["D:\\x.arxml", "d:/x.arxml", "\\\\server\\share\\x.arxml"])
def test_posix_rejects_windows_drive_and_unc_paths(monkeypatch, value):
    monkeypatch.setattr(paths, "_WINDOWS", False)
    monkeypatch.setattr(paths, "os", types.SimpleNamespace(name="posix", sep="/"))
    monkeypatch.setattr(paths, "Path", PurePosixPath)
    with pytest.raises(ValueError, match="Windows drive/UNC path"):
        paths.filesystem_path(value)


@pytest.mark.parametrize("value, expected", [
    ("\\\\?\\D:\\work\\out", "D:\\work\\out"),
    ("\\\\?\\UNC\\server\\share\\out", "\\\\server\\share\\out"),
    ("//?/D:/work/out", "D:/work/out"),
    ("D:\\work\\out?", "D:\\work\\out?"),
])
def test_long_path_prefix_is_not_a_wildcard_on_windows(monkeypatch, value, expected):
    monkeypatch.setattr(paths, "_WINDOWS", True)
    assert strip_long_path_prefix(value) == expected


def test_case_only_output_names_conflict_only_where_file_names_ignore_case(tmp_path, monkeypatch):
    names = (tmp_path / "Pair.txt", tmp_path / "pair.txt")
    if os.name == "nt":
        with pytest.raises(ValueError, match="Conflicting output path"):
            validate_output_paths(names)
    monkeypatch.setattr(paths.os.path, "normcase", lambda value: value)  # POSIX normcase
    validate_output_paths(names)


def test_overlap_message_uses_the_callers_hint(tmp_path):
    source = tmp_path / "source.arxml"
    source.write_bytes(b"x")
    with pytest.raises(ValueError, match="overlaps an input file.*choose another directory"):
        validate_output_paths([source], [source], overlap_hint="choose another directory")


def test_inaccessible_paths_are_reported_as_missing(tmp_path, monkeypatch):
    def denied(self, *args, **kwargs):
        raise PermissionError(13, "Permission denied", str(self))

    with monkeypatch.context() as patch:
        for name in ("exists", "is_file", "is_dir"):
            patch.setattr(Path, name, denied)
        results = (paths.path_exists(tmp_path), paths.is_file(tmp_path), paths.is_dir(tmp_path))
    assert results == (False, False, False)
