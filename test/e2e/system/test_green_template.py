"""绿表空白模板：可被工具读取、表头满足读取校验、由生成脚本可复现。"""

from zipfile import ZipFile

from openpyxl import load_workbook

from helpers import ROOT, run_python
from table_to_arxml.system.green_table import (
    GREEN_COLUMN_COUNT, GREEN_REQUIRED_HEADERS, P_PORT_SHEET, R_PORT_SHEET, parse_green_workbook,
)

TEMPLATE = ROOT / "docs" / "user" / "system" / "GreenTableTemplate.xlsx"


def test_template_layout_matches_reader():
    info = parse_green_workbook(TEMPLATE, "Template")
    assert info.r_port_rows == [] and info.p_port_rows == []
    workbook = load_workbook(TEMPLATE)
    try:
        assert workbook.sheetnames == ["README", R_PORT_SHEET, P_PORT_SHEET]
        for name in (R_PORT_SHEET, P_PORT_SHEET):
            headers = [cell.value for cell in workbook[name][1]]
            assert len(headers) == GREEN_COLUMN_COUNT
            for index, keyword in GREEN_REQUIRED_HEADERS.items():
                assert headers[index] and keyword in headers[index].lower()
            assert all(cell.comment for cell in workbook[name][1])
    finally:
        workbook.close()


def test_template_is_reproducible(tmp_path):
    result = run_python(
        f"import runpy, sys; sys.argv = ['generate_green_table_template', '--output-dir', {str(tmp_path)!r}]; "
        f"runpy.run_path({str(ROOT / 'tools' / 'generate_green_table_template.py')!r}, run_name='__main__')"
    )
    assert result.returncode == 0, result.stderr
    with ZipFile(TEMPLATE) as expected, ZipFile(tmp_path / TEMPLATE.name) as actual:
        names = [name for name in expected.namelist() if name.startswith("xl/")]
        assert names == [name for name in actual.namelist() if name.startswith("xl/")]
        for name in names:
            assert expected.read(name) == actual.read(name), name
