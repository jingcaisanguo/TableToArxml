"""连接器命名与合并绿表页签名。"""

from __future__ import annotations

from openpyxl import Workbook, load_workbook

from helpers import ROOT
from table_to_arxml.system.arxml_generator import MAX_SHORT_NAME, connector_name
from table_to_arxml.system.green_table import (
    MAX_SHEET_NAME,
    RECEIVE_SHEET,
    SEND_SHEET,
    _merged_sheet_name,
    merge_green_workbooks,
)


def test_connector_name_separates_parts():
    assert connector_name("dlc_", "O", "AB", "C") == "dlc_O_AB_C"
    assert connector_name("dlc_", "O", "AB", "C") != connector_name("dlc_", "O", "A", "BC")


def test_long_connector_names_are_truncated_with_stable_hash():
    first = connector_name("ac_", "P" * 80, "port", "R" * 80, "port")
    second = connector_name("ac_", "P" * 80, "port", "R" * 80, "other")
    assert len(first) == len(second) == MAX_SHORT_NAME
    assert first != second
    assert first == connector_name("ac_", "P" * 80, "port", "R" * 80, "port")


def test_merged_sheet_names_are_unique_within_excel_limit():
    used: set[str] = set()
    names = [
        _merged_sheet_name("VeryLongApplicationNameForTest" + suffix, RECEIVE_SHEET, used)
        for suffix in ("A", "B", "C")
    ]
    assert len(set(name.casefold() for name in names)) == 3
    assert all(len(name) <= MAX_SHEET_NAME for name in names)
    assert names[0] == "VeryLongApplicationNameForTestA"[:MAX_SHEET_NAME]


def test_merge_green_workbooks_with_colliding_names(tmp_path):
    source = tmp_path / "green.xlsx"
    workbook = Workbook()
    for title in (RECEIVE_SHEET, SEND_SHEET):
        sheet = workbook.active if title == RECEIVE_SHEET else workbook.create_sheet()
        sheet.title = title
        sheet.append(["No", "port", "module"])
        sheet.merge_cells("A2:B2")
    workbook.save(source)
    workbook.close()

    merged = merge_green_workbooks(
        [("SameLongPrefixForApplication1", source), ("SameLongPrefixForApplication2", source)],
        tmp_path / "merged.xlsx",
    )
    result = load_workbook(merged)
    try:
        assert len(result.sheetnames) == len({name.casefold() for name in result.sheetnames}) == 4
        used_names: set[str] = set()
        expected_names = [
            _merged_sheet_name(app_name, sheet_name, used_names)
            for app_name in ("SameLongPrefixForApplication1", "SameLongPrefixForApplication2")
            for sheet_name in (RECEIVE_SHEET, SEND_SHEET)
        ]
        assert result.sheetnames == expected_names
        assert all(str(ranges) == "A2:B2" for sheet in result for ranges in sheet.merged_cells.ranges)
    finally:
        result.close()


def test_sample_green_tables_merge(tmp_path):
    tables = sorted((ROOT / "test/samples/system/GreenTable/Bcm").glob("*.xlsx"))
    merged = merge_green_workbooks([(path.stem, path) for path in tables], tmp_path / "merged.xlsx")
    result = load_workbook(merged, read_only=True)
    try:
        assert len(result.sheetnames) == 2 * len(tables)
        assert result.sheetnames == [
            name for app_name, _ in [(path.stem, path) for path in tables]
            for name in (f"{app_name}_{RECEIVE_SHEET}", f"{app_name}_{SEND_SHEET}")
        ]
    finally:
        result.close()


def test_parse_green_workbook_accepts_legacy_names_but_uses_directional_rows(tmp_path):
    from table_to_arxml.system.green_table import parse_green_workbook

    source = ROOT / "test/samples/system/GreenTable/Bcm/LIT.xlsx"
    canonical = parse_green_workbook(source, "LIT")
    legacy_path = tmp_path / "legacy.xlsx"
    workbook = load_workbook(source)
    workbook[RECEIVE_SHEET].title = "INPUT"
    workbook[SEND_SHEET].title = "OUTPUT"
    workbook.save(legacy_path)
    workbook.close()

    legacy = parse_green_workbook(legacy_path, "LIT")
    assert legacy.receive_rows == canonical.receive_rows
    assert legacy.send_rows == canonical.send_rows
    merged_path = merge_green_workbooks([("LIT", legacy_path)], tmp_path / "merged.xlsx")
    merged = load_workbook(merged_path, read_only=True)
    try:
        assert merged.sheetnames == ["LIT_RECEIVE", "LIT_SEND"]
    finally:
        merged.close()


def test_parse_green_workbook_rejects_mixed_sheet_pairs(tmp_path):
    import pytest
    from table_to_arxml.system.green_table import parse_green_workbook

    source = ROOT / "test/samples/system/GreenTable/Bcm/LIT.xlsx"
    workbook = load_workbook(source)
    workbook.create_sheet("INPUT")
    mixed_path = tmp_path / "mixed.xlsx"
    workbook.save(mixed_path)
    workbook.close()

    with pytest.raises(ValueError, match="exactly one complete"):
        parse_green_workbook(mixed_path, "LIT")
