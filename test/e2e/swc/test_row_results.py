"""SWC 运行结果：逐行结果工作簿、差异文件、ComSpecs / CS Operations 明细页、生效清单和接口查找范围。"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from openpyxl import load_workbook

from helpers import NS, SWC, run_tool
from swc_fixtures import run_prepared_twins, run_twins, write_twins

A = {"SW_C": "A", "Component_Path": "/Pkg/A"}
B = {"SW_C": "B", "Component_Path": "/Pkg/B"}
TWO_ELEMENTS = [
    {"Interface_Path": "/Pkg/Two", "Data_Element_Name": name, "Data_Type_Ref": "/Pkg/uint8",
     "Target_ARXML": "twins.arxml"}
    for name in ("e1", "e2")
]


def result_rows(tmp_path, sheet_name: str) -> dict[int, dict[str, object]]:
    [path] = (tmp_path / "log").glob("*_result.xlsx")
    workbook = load_workbook(path)
    try:
        sheet = workbook[sheet_name]
        headers = [cell.value for cell in sheet[1]]
        return {
            row[0].row: dict(zip(headers, (cell.value for cell in row)))
            for row in sheet.iter_rows(min_row=2) if any(cell.value is not None for cell in row)
        }
    finally:
        workbook.close()


def output_port(tmp_path, component: str, port: str) -> ET.Element:
    root = ET.parse(tmp_path / "out" / "twins.arxml").getroot()
    for element in root.iter():
        if element.findtext("ar:SHORT-NAME", namespaces=NS) == component:
            for candidate in element.find("ar:PORTS", NS):
                if candidate.findtext("ar:SHORT-NAME", namespaces=NS) == port:
                    return candidate
    raise AssertionError(f"{component}/{port} not found")


def element_refs(port: ET.Element, tag: str) -> list[str]:
    return [
        spec.findtext("ar:DATA-ELEMENT-REF", namespaces=NS) for spec in port.iter(f"{{{NS['ar']}}}{tag}")
    ]


def test_result_workbook_marks_created_failed_and_dependent_rows(tmp_path):
    result = run_twins(tmp_path, {
        "Components": [A, B],
        "P-Ports": [
            {"SW_C": "A", "Port_Name": "p_Ok", "Port_Interface_Ref": "/Pkg/If"},
            {"SW_C": "B", "Port_Name": "p_Bad", "Port_Interface_Ref": "/Pkg/Missing"},
        ],
        "Data Accesses": [{"SW_C": "B", "Runnable_Name": "Run", "Port_Name": "p_Bad"}],
    })
    assert result.returncode == 1
    assert "RESULT: FAILED - table_to_swc_arxml: 1 error(s)" in result.stdout
    assert "[P-Ports!3]" in result.stderr
    ports = result_rows(tmp_path, "P-Ports")
    assert ports[2]["结果"] == "可新建（本次有错误，未写出）"
    assert ports[2]["对象"] == "/Pkg/A/p_Ok"
    assert ports[3]["结果"] == "错误" and "/Pkg/Missing" in ports[3]["说明"]
    accesses = result_rows(tmp_path, "Data Accesses")
    assert accesses[2]["结果"] == "依赖的对象有错误，未处理"
    [path] = (tmp_path / "log").glob("*_result.xlsx")
    workbook = load_workbook(path)
    try:
        assert workbook.sheetnames[0] == "运行结果"
        assert (workbook["运行结果"]["A1"].value, workbook["运行结果"]["B1"].value) == ("结论", "FAILED")
        objects = [row for row in workbook["ARXML 对象"].iter_rows(min_row=2, values_only=True)]
        assert objects == [("可新建（本次有错误，未写出）", "/Pkg/A/p_Ok", str(tmp_path / "twins.arxml"), "P-Ports!2")]
    finally:
        workbook.close()
    [diff] = (tmp_path / "log").glob("*.diff")
    assert "not written because of errors" in diff.read_text(encoding="utf-8")
    assert not (tmp_path / "out").exists()


def test_success_reports_written_objects_and_diff(tmp_path):
    result = run_twins(tmp_path, {
        "Components": [A],
        "P-Ports": [{"SW_C": "A", "Port_Name": "p_Ok", "Port_Interface_Ref": "/Pkg/If"}],
    })
    assert result.returncode == 0, result.stderr
    assert "RESULT: SUCCESS - table_to_swc_arxml: 0 error(s), 0 warning(s); 1 object(s) created" in result.stdout
    assert result_rows(tmp_path, "P-Ports")[2]["结果"] == "新建"
    [diff] = (tmp_path / "log").glob("*.diff")
    text = diff.read_text(encoding="utf-8")
    assert re.search(r"^\+ +<SHORT-NAME>p_Ok</SHORT-NAME>$", text, flags=re.M)
    assert str(tmp_path / "out" / "twins.arxml") in text

    repeated = run_prepared_twins(tmp_path)
    assert repeated.returncode == 0, repeated.stderr


def test_com_specs_sheet_configures_individual_data_elements(tmp_path):
    result = run_twins(tmp_path, {
        "Components": [A, B],
        "SR-Interfaces": TWO_ELEMENTS,
        "P-Ports": [{"SW_C": "B", "Port_Name": "p_Two", "Port_Interface_Ref": "/Pkg/Two"}],
        "R-Ports": [{"SW_C": "A", "Port_Name": "r_Two", "Port_Interface_Ref": "/Pkg/Two"}],
        "ComSpecs": [
            {"SW_C": "A", "Port_Name": "r_Two", "Data_Element_Name": "e1", "Enable_Update": "Y"},
            {"SW_C": "B", "Port_Name": "p_Two", "Data_Element_Name": "e2",
             "Transmission_Acknowledge_Timeout": "0.5"},
        ],
    })
    assert result.returncode == 0, result.stderr
    receiver = output_port(tmp_path, "A", "r_Two")
    assert element_refs(receiver, "NONQUEUED-RECEIVER-COM-SPEC") == ["/Pkg/Two/e1"]
    sender = output_port(tmp_path, "B", "p_Two")
    assert element_refs(sender, "NONQUEUED-SENDER-COM-SPEC") == ["/Pkg/Two/e2"]
    com_specs = result_rows(tmp_path, "ComSpecs")
    assert com_specs[2]["结果"] == "新建" and com_specs[2]["对象"] == "/Pkg/A/r_Two"


def test_com_specs_rejects_both_port_row_and_detail_rows(tmp_path):
    result = run_twins(tmp_path, {
        "Components": [A],
        "SR-Interfaces": TWO_ELEMENTS,
        "R-Ports": [{"SW_C": "A", "Port_Name": "r_Two", "Port_Interface_Ref": "/Pkg/Two", "Enable_Update": "Y"}],
        "ComSpecs": [{"SW_C": "A", "Port_Name": "r_Two", "Data_Element_Name": "e1", "Enable_Update": "Y"}],
    })
    assert result.returncode == 1
    assert "configure the port's ComSpecs in only one place" in result.stderr
    assert result_rows(tmp_path, "ComSpecs")[2]["结果"] == "错误"


def test_com_specs_rejects_columns_of_the_other_direction(tmp_path):
    result = run_twins(tmp_path, {
        "Components": [A],
        "SR-Interfaces": TWO_ELEMENTS,
        "R-Ports": [{"SW_C": "A", "Port_Name": "r_Two", "Port_Interface_Ref": "/Pkg/Two"}],
        "ComSpecs": [{"SW_C": "A", "Port_Name": "r_Two", "Data_Element_Name": "e1",
                      "Uses_End_To_End_Protection": "Y"}],
    })
    assert result.returncode == 1
    assert "Uses End To End Protection do not apply to R-Port r_Two" in result.stderr
    assert result_rows(tmp_path, "R-Ports")[2]["结果"] == "依赖的对象有错误，未处理"


def test_cs_operations_sheet_configures_each_operation(tmp_path):
    result = run_twins(tmp_path, {
        "Components": [A, B],
        "P-Ports": [{"SW_C": "B", "Port_Name": "p_Srv", "Port_Interface_Ref": "/Pkg/CsIf"}],
        "R-Ports": [{"SW_C": "A", "Port_Name": "r_Cli", "Port_Interface_Ref": "/Pkg/CsIf"}],
        "CS Operations": [
            {"SW_C": "B", "Port_Name": "p_Srv", "Operation_Name": "Op", "Runnable_Name": "RE_Custom",
             "Runnable_Symbol": "Custom_Sym", "Can_Be_Invoked_Concurrently": "Y"},
            {"SW_C": "A", "Port_Name": "r_Cli", "Operation_Name": "Op", "Runnable_Name": "Run"},
        ],
    })
    assert result.returncode == 0, result.stderr
    text = (tmp_path / "out" / "twins.arxml").read_text(encoding="utf-8")
    assert "<SHORT-NAME>RE_Custom</SHORT-NAME>" in text and "<SYMBOL>Custom_Sym</SYMBOL>" in text
    assert "<OPERATION-INVOKED-EVENT>" in text
    assert "<SYNCHRONOUS-SERVER-CALL-POINT>" in text
    operations = result_rows(tmp_path, "CS Operations")
    assert operations[2]["结果"] == "新建" and "/Pkg/B/IB/RE_Custom" in operations[2]["对象"]
    assert operations[3]["结果"] == "新建"


def test_cs_operations_unknown_operation_is_an_error(tmp_path):
    result = run_twins(tmp_path, {
        "Components": [B],
        "P-Ports": [{"SW_C": "B", "Port_Name": "p_Srv", "Port_Interface_Ref": "/Pkg/CsIf"}],
        "CS Operations": [{"SW_C": "B", "Port_Name": "p_Srv", "Operation_Name": "Nope"}],
    })
    assert result.returncode == 1
    assert "Nope" in result.stderr and "[CS Operations!2]" in result.stderr


def test_self_closing_ports_container_is_expanded(tmp_path):
    write_twins(tmp_path, {
        "Components": [A],
        "P-Ports": [{"SW_C": "A", "Port_Name": "p_Ok", "Port_Interface_Ref": "/Pkg/If"}],
    })
    path = tmp_path / "twins.arxml"
    path.write_text(path.read_text(encoding="utf-8").replace("<PORTS></PORTS>", "<PORTS/>", 1), encoding="utf-8")
    result = run_prepared_twins(tmp_path)
    assert result.returncode == 0, result.stderr
    assert output_port(tmp_path, "A", "p_Ok") is not None
    assert "<PORTS/>" not in (tmp_path / "out" / "twins.arxml").read_text(encoding="utf-8")


def test_interface_target_outside_list_is_used_and_listed(tmp_path):
    write_twins(tmp_path, {
        "Components": [A],
        "SR-Interfaces": [{"Interface_Path": "/Ifs/New", "Data_Element_Name": "v", "Data_Type_Ref": "/Pkg/uint8",
                           "Target_ARXML": "extra.arxml"}],
        "P-Ports": [{"SW_C": "A", "Port_Name": "p_New", "Port_Interface_Ref": "/Ifs/New"}],
    })
    (tmp_path / "extra.arxml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<AUTOSAR xmlns="http://autosar.org/schema/r4.0">\n'
        "  <AR-PACKAGES>\n    <AR-PACKAGE>\n      <SHORT-NAME>Ifs</SHORT-NAME>\n"
        "      <ELEMENTS></ELEMENTS>\n    </AR-PACKAGE>\n  </AR-PACKAGES>\n</AUTOSAR>\n",
        encoding="utf-8",
    )
    (tmp_path / "unused.arxml").write_text("<AUTOSAR/>", encoding="utf-8")
    listed = tmp_path / "effective.lst"
    result = run_tool(
        SWC, "--project-root", tmp_path, "--arxml-file-list", tmp_path / "arxml.lst",
        "--workbook", tmp_path / "ports.xlsx", "--log-dir", tmp_path / "log",
        "--output-dir", tmp_path / "out", "--output-arxml-list", listed,
    )
    assert result.returncode == 0, result.stderr
    assert "Interface lookup also uses Target ARXML not in the list" in result.stdout
    entries = [line for line in listed.read_text(encoding="utf-8").splitlines() if not line.startswith("#")]
    assert entries == [str(tmp_path / "out" / "twins.arxml"), str(tmp_path / "out" / "extra.arxml")]


def test_output_arxml_list_must_not_overwrite_inputs(tmp_path):
    write_twins(tmp_path, {
        "Components": [A],
        "P-Ports": [{"SW_C": "A", "Port_Name": "p_Ok", "Port_Interface_Ref": "/Pkg/If"}],
    })
    result = run_tool(
        SWC, "--project-root", tmp_path, "--arxml-file-list", tmp_path / "arxml.lst",
        "--workbook", tmp_path / "ports.xlsx", "--log-dir", tmp_path / "log",
        "--output-dir", tmp_path / "out", "--output-arxml-list", tmp_path / "arxml.lst",
    )
    assert result.returncode == 1
    assert "choose another --output-arxml-list path" in result.stderr
    assert not (tmp_path / "out").exists()
    assert (tmp_path / "arxml.lst").read_text(encoding="utf-8") == "twins.arxml\n"


def test_dry_run_does_not_write_output_arxml_list(tmp_path):
    write_twins(tmp_path, {
        "Components": [A],
        "P-Ports": [{"SW_C": "A", "Port_Name": "p_Ok", "Port_Interface_Ref": "/Pkg/If"}],
    })
    listed = tmp_path / "effective.lst"
    result = run_tool(
        SWC, "--project-root", tmp_path, "--arxml-file-list", tmp_path / "arxml.lst",
        "--workbook", tmp_path / "ports.xlsx", "--log-dir", tmp_path / "log",
        "--dry-run", "--output-arxml-list", listed,
    )
    assert result.returncode == 0, result.stderr
    assert "RESULT: DRY-RUN" in result.stdout
    assert not listed.exists()
