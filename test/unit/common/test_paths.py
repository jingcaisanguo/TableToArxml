"""Literal paths and glob expressions share the same base-directory rules."""

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
    assert collect_green_workbooks([str(entry)], "T", tmp_path) == [
        ("Source[1]", tmp_path / "Source[1].xlsx")
    ]


def test_bracket_directory_is_not_part_of_glob_expression(tmp_path):
    project = tmp_path / "Project[1]"
    project.mkdir()
    (project / "one.xlsx").touch()
    assert resolve_config_path("*.xlsx", "workbook", None, project) == project / "one.xlsx"
    assert collect_green_workbooks(["*.xlsx"], "T", project) == [("one", project / "one.xlsx")]
    assert expand_path(project / "*.xlsx", tmp_path) == [project / "one.xlsx"]
