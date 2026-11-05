"""SWC options: unresolvable paths, long-path prefixes and Excel lock files."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from table_to_arxml.swc import options as swc_options
from table_to_arxml.swc.options import parse_args, resolve_cli_path, resolve_config_path

REQUIRED = ["--project-root", ".", "--workbook", "w.xlsx", "--arxml-file-list", "a.lst", "--log-dir", "log"]


def test_paths_are_absolute_when_the_drive_cannot_report_final_paths(tmp_path, monkeypatch):
    def unsupported(*args, **kwargs):
        raise OSError(1, "Incorrect function")  # RAM disks and some network redirectors

    monkeypatch.chdir(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "resolve", unsupported)
        patch.setattr(os.path, "realpath", unsupported)
        options = parse_args([*REQUIRED, "--output-dir", "out"])
        workbook = resolve_config_path("missing/x.xlsx", "workbook", None, tmp_path)
    assert options.project_root == tmp_path
    assert options.output_dir == tmp_path / "out"
    assert workbook == tmp_path / "missing" / "x.xlsx"


def test_path_errors_are_usage_errors(tmp_path, monkeypatch, capsys):
    def unsupported(*args, **kwargs):
        raise OSError(1, "Incorrect function")

    monkeypatch.setattr(swc_options, "absolute_path", unsupported)
    with pytest.raises(SystemExit) as caught:
        parse_args(REQUIRED)
    assert caught.value.code == 2
    assert "Incorrect function" in capsys.readouterr().err


@pytest.mark.skipif(os.name != "nt", reason="Windows long-path prefix")
def test_long_path_prefix_is_accepted_on_the_command_line(tmp_path):
    path = resolve_cli_path("\\\\?\\" + str(tmp_path), "--project-root")
    assert path.is_dir()
    assert str(path).endswith(str(tmp_path)[2:])


@pytest.mark.parametrize("value", ["out*", "out?", "${variant}/out"])
def test_wildcards_are_still_rejected_on_the_command_line(value):
    with pytest.raises(Exception, match="must be a literal path"):
        resolve_cli_path(value, "--output-dir")


def test_excel_lock_file_does_not_make_the_workbook_pattern_ambiguous(tmp_path):
    (tmp_path / "Ports.xlsx").touch()
    (tmp_path / "~$Ports.xlsx").touch()
    assert resolve_config_path("*.xlsx", "workbook", None, tmp_path) == tmp_path / "Ports.xlsx"
