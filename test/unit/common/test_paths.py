"""Literal paths and glob expressions share the same base-directory rules."""

from pathlib import Path

import pytest

from table_to_arxml.common.paths import expand_path
from table_to_arxml.swc.options import resolve_config_path
from table_to_arxml.system.green_table import collect_green_workbooks


@pytest.mark.parametrize("absolute", [False, True])
def test_existing_bracket_filename_wins_over_glob(tmp_path, absolute):
    for name in ("Source[1].xlsx", "Source1.xlsx"):
        (tmp_path / name).touch()
    entry = tmp_path / "Source[1].xlsx" if absolute else "Source[1].xlsx"
    assert resolve_config_path(str(entry), "workbook", None, tmp_path) == tmp_path / "Source[1].xlsx"


@pytest.mark.parametrize("absolute", [False, True])
def test_green_table_file_name_wildcard_always_expands(tmp_path, absolute):
    for name in ("Source[1].xlsx", "Source1.xlsx"):
        (tmp_path / name).touch()
    entry = tmp_path / "Source[1].xlsx" if absolute else "Source[1].xlsx"
    assert collect_green_workbooks([str(entry)], "T", tmp_path) == [("Source1", tmp_path / "Source1.xlsx")]


@pytest.mark.parametrize("name", ["Source[1]", "1Lamp", "Lamp-v2", "Lamp v2", "灯光", "A" * 129])
def test_green_table_file_name_must_be_short_name(tmp_path, name):
    (tmp_path / "Valid.xlsx").touch()
    (tmp_path / f"{name}.xlsx").touch()
    with pytest.raises(ValueError, match="Green table file names must be AUTOSAR SHORT-NAMEs") as error:
        collect_green_workbooks(["*.xlsx"], "T", tmp_path)
    assert f"{name}.xlsx" in str(error.value)
    assert "Valid.xlsx" not in str(error.value)


def test_green_table_lock_files_are_ignored_before_name_check(tmp_path):
    (tmp_path / "Lamp.xlsx").touch()
    (tmp_path / "~$Lamp.xlsx").touch()
    assert collect_green_workbooks(["*.xlsx"], "T", tmp_path) == [("Lamp", tmp_path / "Lamp.xlsx")]


def test_bracket_directory_is_not_part_of_glob_expression(tmp_path):
    project = tmp_path / "Project[1]"
    project.mkdir()
    (project / "one.xlsx").touch()
    assert resolve_config_path("*.xlsx", "workbook", None, project) == project / "one.xlsx"
    assert collect_green_workbooks(["*.xlsx"], "T", project) == [("one", project / "one.xlsx")]
    assert expand_path(project / "*.xlsx", tmp_path) == [project / "one.xlsx"]


def test_wildcard_reported_as_existing_by_filesystem_is_still_expanded(tmp_path, monkeypatch):
    tables = tmp_path / "greenTable" / "V"
    tables.mkdir(parents=True)
    (tables / "one.xlsx").touch()
    pattern = tables / "*.xlsx"
    exists, is_dir, is_file = Path.exists, Path.is_dir, Path.is_file
    monkeypatch.setattr(Path, "exists", lambda self, *args, **kwargs: self == pattern or exists(self))
    monkeypatch.setattr(Path, "is_dir", lambda self, *args, **kwargs: self == pattern or is_dir(self))
    monkeypatch.setattr(Path, "is_file", lambda self, *args, **kwargs: self == pattern or is_file(self))
    assert expand_path(pattern, tmp_path) == [tables / "one.xlsx"]
    assert collect_green_workbooks([r"greenTable\${variant}\*.xlsx"], "V", tmp_path) == [
        ("one", tables / "one.xlsx")
    ]
    assert resolve_config_path(r"greenTable\${variant}\*.xlsx", "workbook", "V", tmp_path) == tables / "one.xlsx"
