"""ARXML list compatibility: linked folders, foreign paths, inaccessible entries, encodings and exclusions."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from table_to_arxml.common import arxml_list
from table_to_arxml.common.arxml_list import ArxmlExclusions, read_arxml_list


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


def write_arxml(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("<AUTOSAR/>", encoding="utf-8")
    return path


def write_list(project: Path, *lines: str, encoding: str = "utf-8") -> Path:
    path = project / "files.lst"
    path.write_bytes(("\n".join(lines) + "\n").encode(encoding))
    return path


@pytest.fixture
def linked_project(tmp_path):
    project = tmp_path / "project"
    write_arxml(project / "arxml" / "Local.arxml")
    shared = write_arxml(tmp_path / "shared" / "McArxml" / "Mc.arxml").parent
    link_directory(project / "arxml" / "McArxml", shared)
    return project, shared


def test_linked_folder_keeps_listed_layout_and_deduplicates_by_real_file(linked_project):
    project, shared = linked_project
    list_file = write_list(project, "arxml/Local.arxml", "arxml/McArxml/Mc.arxml", str(shared / "Mc.arxml"))
    files = read_arxml_list(list_file, project, strict=True)
    assert files == [project / "arxml" / "Local.arxml", project / "arxml" / "McArxml" / "Mc.arxml"]


def test_output_dir_uses_the_listed_layout_for_linked_folders(linked_project, tmp_path):
    from table_to_arxml.swc.store import output_path

    project, shared = linked_project
    listed = read_arxml_list(write_list(project, "arxml/McArxml/Mc.arxml"), project)[0]
    out = tmp_path / "out"
    assert output_path(listed, out, project) == out / "arxml" / "McArxml" / "Mc.arxml"
    assert output_path(project / "arxml" / "Local.arxml", out, project) == out / "arxml" / "Local.arxml"
    with pytest.raises(ValueError, match="outside projectRoot"):
        output_path(shared / "Mc.arxml", out, project)


def test_exclusions_by_path_apply_to_linked_entries(linked_project):
    project, _shared = linked_project
    exclusions = ArxmlExclusions(project, ["arxml/McArxml/Mc.arxml"])
    list_file = write_list(project, "arxml/Local.arxml", "arxml/McArxml/Mc.arxml")
    assert read_arxml_list(list_file, project, exclusions=exclusions) == [project / "arxml" / "Local.arxml"]


def test_foreign_platform_absolute_path_is_a_list_problem(tmp_path, caplog):
    write_arxml(tmp_path / "a.arxml")
    foreign = "/opt/shared/Common.arxml" if os.name == "nt" else "D:\\shared\\Common.arxml"
    list_file = write_list(tmp_path, "a.arxml", foreign)
    assert read_arxml_list(list_file, tmp_path) == [tmp_path / "a.arxml"]
    assert "Invalid ARXML list entry" in caplog.text and "skipped" in caplog.text
    with pytest.raises(ValueError, match="Invalid ARXML list entry"):
        read_arxml_list(list_file, tmp_path, strict=True)


def test_unresolved_placeholder_entry_is_a_list_problem(tmp_path, caplog):
    write_arxml(tmp_path / "a.arxml")
    list_file = write_list(tmp_path, "a.arxml", "${variant}/b.arxml")
    assert read_arxml_list(list_file, tmp_path) == [tmp_path / "a.arxml"]
    assert "Unresolved placeholder" in caplog.text


def test_inaccessible_entry_is_skipped_instead_of_crashing(tmp_path, monkeypatch, caplog):
    write_arxml(tmp_path / "a.arxml")
    denied = tmp_path / "locked" / "b.arxml"
    is_file = Path.is_file

    def checked_is_file(self, *args, **kwargs):
        if self == denied:
            raise PermissionError(13, "Permission denied", str(self))
        return is_file(self, *args, **kwargs)

    monkeypatch.setattr(Path, "is_file", checked_is_file)
    list_file = write_list(tmp_path, "a.arxml", "locked/b.arxml")
    assert read_arxml_list(list_file, tmp_path) == [tmp_path / "a.arxml"]
    assert "Listed ARXML not found" in caplog.text
    with pytest.raises(FileNotFoundError, match="Listed ARXML not found"):
        read_arxml_list(list_file, tmp_path, strict=True)


def test_ansi_list_from_eb_importer_is_decoded_with_the_system_code_page(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    model = write_arxml(tmp_path / "模型" / "车身.arxml")
    monkeypatch.setattr(arxml_list, "_legacy_encoding", lambda: "gbk")
    list_file = write_list(tmp_path, "模型/车身.arxml", encoding="gbk")
    assert read_arxml_list(list_file, tmp_path) == [model]
    assert "decoded with the system encoding gbk" in caplog.text


def test_utf16_list_with_bom_is_decoded(tmp_path):
    model = write_arxml(tmp_path / "模型.arxml")
    list_file = write_list(tmp_path, "模型.arxml", encoding="utf-16")
    assert read_arxml_list(list_file, tmp_path) == [model]


def test_undecodable_list_reports_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(arxml_list, "_legacy_encoding", lambda: "utf-8")
    list_file = tmp_path / "files.lst"
    list_file.write_bytes(b"\xfa.arxml\n")
    with pytest.raises(ValueError, match="not valid UTF-8"):
        read_arxml_list(list_file, tmp_path)


@pytest.mark.parametrize("value", ["./Foo.arxml", ".\\Foo.arxml"])
def test_dot_slash_exclusion_is_a_project_relative_path(tmp_path, value):
    top = write_arxml(tmp_path / "Foo.arxml")
    nested = write_arxml(tmp_path / "sub" / "Foo.arxml")
    exclusions = ArxmlExclusions(tmp_path, [value])
    assert exclusions.matches(top)
    assert not exclusions.matches(nested)
    files = read_arxml_list(write_list(tmp_path, "Foo.arxml", "sub/Foo.arxml"), tmp_path, exclusions=exclusions)
    assert files == [nested]
