"""工作簿读取的兼容性回归：远处的格式化空单元格、合并单元格和 Unicode 行分隔符。"""

from __future__ import annotations

import time

import pytest
from openpyxl import Workbook
from openpyxl.styles import Font

from table_to_arxml.swc import workbook


def test_formatted_empty_cells_do_not_extend_the_sheet(tmp_path):
    book = Workbook()
    sheet = book.active
    sheet.title = "Components"
    sheet.append(["SW-C", "Component Path"])
    sheet.append(["A", "/Pkg/A"])
    sheet.append([None, "/Pkg/B"])
    sheet["A3"] = "B"
    sheet.merge_cells("C1:D1")
    sheet["C5"] = "merged"
    sheet.merge_cells("C5:D6")
    # WPS/LibreOffice 常给整列或末行设置格式，单元格存在但没有值。
    sheet.cell(row=200000, column=8).font = Font(bold=True)
    path = tmp_path / "formatted.xlsx"
    book.save(path)

    started = time.perf_counter()
    rows = workbook.load_workbook_sheets(path)["Components"]
    assert time.perf_counter() - started < 10
    assert rows == [
        ("SW-C", "Component Path", None, None),
        ("A", "/Pkg/A", None, None),
        ("B", "/Pkg/B", None, None),
        (None, None, None, None),
        (None, None, "merged", "merged"),
        (None, None, "merged", "merged"),
    ]


def test_formula_cells_are_still_detected_without_scanning_the_dimension(tmp_path):
    book = Workbook()
    sheet = book.active
    sheet.title = "Components"
    sheet.append(["SW-C", "Component Path"])
    sheet.append(["A", '="/Pkg/"&"A"'])
    path = tmp_path / "formula.xlsx"
    book.save(path)
    rows = workbook.load_workbook_sheets(path)["Components"]
    assert isinstance(rows[1][1], workbook.FormulaCell)


@pytest.mark.parametrize("value", ["A\u2028B", "A\u2029B", "A\x85B", "A\x7fB", "A\x9fB"])
def test_unicode_line_separators_and_c1_controls_are_rejected_with_location(value, caplog):
    records = workbook.read_sheet_rows({
        "Components": [("SW-C", "Component Path"), (value, "/Pkg/A")],
    }, "Components")
    assert records == []
    assert [record.levelname for record in caplog.records] == ["ERROR"]
    assert "Skipped Components row 2: Components!SW-C2 contains control characters" in caplog.text


def test_non_ascii_text_is_still_accepted(caplog):
    records = workbook.read_sheet_rows({
        "Components": [("SW-C", "Component Path"), ("\u4e2d\u6587\u00e9", "/Pkg/A")],
    }, "Components")
    assert records == [{"SW-C": "\u4e2d\u6587\u00e9", "Component Path": "/Pkg/A"}]
    assert not caplog.records
