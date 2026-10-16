"""连接器命名与合并绿表页签名。"""

from __future__ import annotations

from openpyxl import Workbook, load_workbook

from helpers import ROOT
from table_to_arxml.system.arxml_generator import MAX_SHORT_NAME, connector_name
from table_to_arxml.system.green_table import MAX_SHEET_NAME, _merged_sheet_name, merge_green_workbooks


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
    names = [_merged_sheet_name("VeryLongApplicationNameForTest" + suffix, "INPUT", used) for suffix in ("A", "B", "C")]
    assert len(set(name.casefold() for name in names)) == 3
    assert all(len(name) <= MAX_SHEET_NAME for name in names)
    assert names[0] == "VeryLongApplicationNameForTestA"[:MAX_SHEET_NAME]


def test_merge_green_workbooks_with_colliding_names(tmp_path):
    source = tmp_path / "green.xlsx"
    workbook = Workbook()
    for title in ("INPUT", "OUTPUT"):
        sheet = workbook.active if title == "INPUT" else workbook.create_sheet()
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
        assert all(str(ranges) == "A2:B2" for sheet in result for ranges in sheet.merged_cells.ranges)
    finally:
        result.close()


def test_sample_green_tables_merge(tmp_path):
    tables = sorted((ROOT / "test/samples/system/GreenTable/Bcm").glob("*.xlsx"))
    merged = merge_green_workbooks([(path.stem, path) for path in tables], tmp_path / "merged.xlsx")
    result = load_workbook(merged, read_only=True)
    try:
        assert len(result.sheetnames) == 2 * len(tables)
    finally:
        result.close()
