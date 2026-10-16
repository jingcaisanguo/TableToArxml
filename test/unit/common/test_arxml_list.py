"""ARXML 清单读取规则，两个工具共用。"""

from __future__ import annotations

import logging

import pytest

from table_to_arxml.common.arxml_list import DEFAULT_EXCLUDE_PATTERNS, ArxmlExclusions, read_arxml_list


@pytest.fixture
def project(tmp_path):
    for relative in ("a.arxml", "sub/b.arxml", "sub/deep/c.arxml", "notes.txt", "tresos/t.arxml",
                     "x/dem/swcd_calibration/cal.arxml"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("<AUTOSAR/>", encoding="utf-8")
    return tmp_path


def write_list(project, *lines: str):
    path = project / "files.lst"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def names(paths) -> list[str]:
    return [path.name for path in paths]


def test_entries_comments_quotes_backslashes_and_duplicates(project, caplog):
    caplog.set_level(logging.INFO)
    list_file = write_list(project, "# comment", "", '"a.arxml"', "sub\\b.arxml", str(project / "a.arxml"))
    assert names(read_arxml_list(list_file, project)) == ["a.arxml", "b.arxml"]
    assert "Skip duplicate ARXML entry" in caplog.text


def test_glob_entries_include_only_arxml(project):
    list_file = write_list(project, "sub/**/*.arxml", "*.*")
    assert names(read_arxml_list(list_file, project)) == ["b.arxml", "c.arxml", "a.arxml"]


@pytest.mark.parametrize("entry, message", [
    ("missing.arxml", "Listed ARXML not found"),
    ("notes.txt", "Non-ARXML record"),
    ("nothing/*.arxml", "No ARXML matches pattern"),
])
def test_problem_entries_warn_or_fail_in_strict_mode(project, caplog, entry, message):
    list_file = write_list(project, "a.arxml", entry)
    assert names(read_arxml_list(list_file, project)) == ["a.arxml"]
    assert message in caplog.text
    with pytest.raises((FileNotFoundError, ValueError), match=message):
        read_arxml_list(list_file, project, strict=True)


def test_search_dirs_and_entry_normalization(project):
    list_file = write_list(project, "t.arxml", "BUILD/a.arxml")
    files = read_arxml_list(
        list_file, project, search_dirs=(project / "tresos",),
        normalize_entry=lambda entry: entry.replace("BUILD/", ""),
    )
    assert names(files) == ["t.arxml", "a.arxml"]


def test_relative_entry_found_in_several_bases_is_collected_from_each(project):
    (project / "tresos" / "a.arxml").write_text("<AUTOSAR/>", encoding="utf-8")
    files = read_arxml_list(write_list(project, "a.arxml"), project, search_dirs=(project / "tresos",))
    assert files == [(project / "a.arxml").resolve(), (project / "tresos" / "a.arxml").resolve()]


def test_exclusions_by_name_path_and_pattern(project, caplog):
    caplog.set_level(logging.INFO)
    list_file = write_list(project, "a.arxml", "sub/b.arxml", "sub/deep/c.arxml", "x/dem/swcd_calibration/cal.arxml")
    exclusions = ArxmlExclusions(project, ["A.ARXML", "sub\\deep\\c.arxml"], DEFAULT_EXCLUDE_PATTERNS)
    assert names(read_arxml_list(list_file, project, exclusions=exclusions)) == ["b.arxml"]
    assert caplog.text.count("Exclude ARXML file") == 3


def test_empty_result_and_missing_list(project):
    with pytest.raises(ValueError, match="No usable ARXML files"):
        read_arxml_list(write_list(project, "# nothing"), project)
    with pytest.raises(FileNotFoundError, match="ARXML list not found"):
        read_arxml_list(project / "missing.lst", project)


def test_literal_file_name_with_glob_characters(project):
    (project / "Foo[1].arxml").write_text("<AUTOSAR/>", encoding="utf-8")
    assert names(read_arxml_list(write_list(project, "Foo[1].arxml"), project, strict=True)) == ["Foo[1].arxml"]
