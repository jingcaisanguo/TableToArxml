"""源文件缓存的兼容性回归：批量插入时的解析次数、未变化文件不计入修改、写出前检查外部修改。"""

from __future__ import annotations

import logging

import pytest

from table_to_arxml.common.paths import validate_output_paths
from table_to_arxml.swc import store as store_module
from table_to_arxml.swc.apply import apply_composite_types, apply_enumerations, apply_interfaces
from table_to_arxml.swc.store import SourceStore, write_changes
from table_to_arxml.swc.workbook import ArraySpec, EnumerationSpec, InterfaceSpec
from swc_fixtures import TWINS


@pytest.fixture
def parse_count(monkeypatch):
    calls: list[str] = []
    original = store_module.load_document

    def counting(path):
        calls.append(str(path))
        return original(path)

    monkeypatch.setattr(store_module, "load_document", counting)
    return calls


@pytest.fixture
def target(tmp_path):
    path = tmp_path / "twins.arxml"
    path.write_text(TWINS, encoding="utf-8")
    return path


def test_new_types_in_one_file_are_inserted_with_a_single_parse(target, parse_count):
    store = SourceStore([target])
    arrays = [ArraySpec(f"/Pkg/Arr{index}", target, "e", "/Pkg/uint8", 4) for index in range(5)]
    enumerations = [
        EnumerationSpec(f"/Pkg/En{index}", target, "/Pkg/uint8", f"/Pkg/Cm{index}", f"/Pkg/Dc{index}",
                        (("0", "Off"), ("1", "On")))
        for index in range(3)
    ]
    interfaces = [InterfaceSpec(f"/Pkg/SrIf{index}", target, (("v", "/Pkg/uint8", False),)) for index in range(3)]

    assert len(apply_enumerations(enumerations, store).planned) == 3
    assert len(parse_count) == 1
    assert len(apply_composite_types(arrays, [], store).planned) == 5
    assert len(parse_count) == 2
    assert len(apply_interfaces(interfaces, store).planned) == 3
    assert len(parse_count) == 3

    document = store.load(target)
    assert all(document.element_exists(f"/Pkg/Arr{index}") for index in range(5))
    assert all(document.element_exists(f"/Pkg/Dc{index}") for index in range(3))
    assert len(parse_count) == 4


def test_missing_package_is_reported_without_parsing_the_target(target, parse_count, caplog):
    store = SourceStore([target])
    result = apply_composite_types([ArraySpec("/Missing/Arr", target, "e", "/Pkg/uint8", 4)], [], store)
    assert result.skipped == 1 and not result.planned
    assert "package /Missing not found in" in caplog.text
    assert parse_count == []


def test_alternating_target_files_are_parsed_once_each(tmp_path, target, parse_count):
    other = tmp_path / "other.arxml"
    other.write_text(TWINS, encoding="utf-8")
    store = SourceStore([target, other])
    arrays = [
        ArraySpec(f"/Pkg/Arr{index}", (target, other)[index % 2], "e", "/Pkg/uint8", 4) for index in range(6)
    ]
    assert len(apply_composite_types(arrays, [], store).planned) == 6
    assert sorted(parse_count) == sorted([str(target), str(other)])
    assert store.read(target).count(b"<SHORT-NAME>Arr") == 3
    assert store.read(other).count(b"<SHORT-NAME>Arr") == 3


def test_same_file_with_different_spellings_is_one_source(tmp_path, target):
    alias = tmp_path / "sub" / ".." / target.name
    (tmp_path / "sub").mkdir()
    original = target.read_bytes()
    store = SourceStore([target])
    store.write(alias, store.read(alias) + b"\n")
    assert store.source_files == (target,)
    assert list(store.modified()) == [target]
    assert store.read(target) == original + b"\n"


def test_output_overlap_error_names_the_swc_option(tmp_path, target, monkeypatch):
    # 公共检查的缺省提示与工具无关，SWC 须显式说明如何覆盖源文件。
    monkeypatch.setitem(validate_output_paths.__kwdefaults__, "overlap_hint", "tool-neutral hint")
    store = SourceStore([target])
    store.write(target, store.read(target) + b"\n")
    with pytest.raises(ValueError, match="overlaps an input file.*use --in-place explicitly"):
        write_changes(store.modified(), False, tmp_path, tmp_path, store.source_files)
    assert target.read_text(encoding="utf-8") == TWINS


def test_shared_compu_method_planned_in_the_same_step_is_compared(target, caplog):
    caplog.set_level(logging.INFO)
    store = SourceStore([target])
    values = (("0", "Off"), ("1", "On"))
    result = apply_enumerations([
        EnumerationSpec("/Pkg/E1", target, "/Pkg/uint8", "/Pkg/SharedCm", "/Pkg/dc_E1", values),
        EnumerationSpec("/Pkg/E2", target, "/Pkg/uint8", "/Pkg/SharedCm", "/Pkg/dc_E2", values),
        EnumerationSpec("/Pkg/E3", target, "/Pkg/uint8", "/Pkg/SharedCm", "/Pkg/dc_E3", (("0", "Other"),)),
    ], store)
    assert result.planned == ["/Pkg/E1", "/Pkg/E2"] and result.skipped == 1
    assert "COMPU-METHOD /Pkg/SharedCm in" in caplog.text
    assert "already exists with the same definition" in caplog.text
    assert "already exists with a different definition" in caplog.text
    assert store.read(target).count(b"<SHORT-NAME>SharedCm</SHORT-NAME>") == 1


def test_unchanged_buffers_are_not_reported_as_modified(target):
    store = SourceStore([target])
    original = store.read(target)
    store.write(target, original)
    assert store.modified() == {}
    store.write(target, original + b"\n")
    assert list(store.modified()) == [target]
    store.write(target, original)
    assert store.modified() == {}


@pytest.mark.parametrize("in_place", [True, False])
def test_source_changed_on_disk_after_loading_blocks_all_writes(tmp_path, target, in_place):
    other = tmp_path / "other.arxml"
    other.write_text(TWINS, encoding="utf-8")
    store = SourceStore([target, other])
    for path in (target, other):
        store.write(path, store.read(path).replace(b"<SHORT-NAME>If</SHORT-NAME>", b"<SHORT-NAME>If2</SHORT-NAME>"))
    external = TWINS.replace("Pkg", "Changed").encode()
    target.write_bytes(external)

    out = tmp_path / "out"
    with pytest.raises(ValueError, match="changed on disk after it was loaded.*twins.arxml"):
        write_changes(store.modified(), in_place, None if in_place else out, tmp_path, store.source_files)
    assert target.read_bytes() == external
    assert other.read_text(encoding="utf-8") == TWINS
    assert not out.exists()
