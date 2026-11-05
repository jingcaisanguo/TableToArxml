"""SWC 兼容性回归：Unicode 行分隔符、未变化文件不计入修改、CS 自动名称重合、命名空间前缀、
其他工具省略的 runnable 属性、C 关键字 SYMBOL、清单通配符包含输出目录、链接目录中的目标文件。"""

from __future__ import annotations

import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from helpers import NS, SWC, run_tool
from swc_fixtures import run_prepared_twins, run_twins, write_twins

B = {"SW_C": "B", "Component_Path": "/Pkg/B"}
SR_PORT = {"SW_C": "B", "Port_Name": "p_Out", "Port_Interface_Ref": "/Pkg/If"}


def cs_interface(path: str, operation: str) -> dict[str, str]:
    return {"Interface_Path": path, "Operation_Name": operation, "Target_ARXML": "twins.arxml"}


def patch_twins(tmp_path, old: str, new: str) -> None:
    path = tmp_path / "twins.arxml"
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def to_prefixed(text: str) -> str:
    text = text.replace('xmlns="http://autosar.org/schema/r4.0"', 'xmlns:ar="http://autosar.org/schema/r4.0"')
    return re.sub(r"<(/?)([A-Z])", r"<\1ar:\2", text)


def test_unicode_line_separator_in_label_is_an_error_with_location(tmp_path):
    result = run_twins(tmp_path, {
        "Enumerations": [{"Type_Path": "/Pkg/Mode", "Value": "0", "Label": "A\u2028B",
                          "Implementation_Data_Type_Ref": "/Pkg/uint8", "Compu_Method_Ref": "/Pkg/CmMode",
                          "Data_Constr_Ref": "/Pkg/dc_Mode", "Target_ARXML": "twins.arxml"}],
    }, minimal=True)
    assert result.returncode == 1
    assert "Skipped Enumerations row 2: Enumerations!Label2 contains control characters" in result.stderr
    assert not (tmp_path / "out").exists()


def test_rerun_on_output_reports_no_affected_files(tmp_path):
    first = run_twins(tmp_path, {"Components": [B], "P-Ports": [SR_PORT]}, minimal=True)
    assert first.returncode == 0, first.stderr
    assert "1 ARXML file(s) affected" in first.stdout
    shutil.copyfile(tmp_path / "out" / "twins.arxml", tmp_path / "twins.arxml")
    shutil.rmtree(tmp_path / "out")
    second = run_prepared_twins(tmp_path)
    assert second.returncode == 0, second.stderr
    assert "0 ARXML file(s) affected" in second.stdout
    assert "Successfully wrote 0 ARXML file(s)" in second.stdout


@pytest.mark.parametrize("name", [
    "<SHORT-NAME >B</SHORT-NAME >",
    "<SHORT-NAME><![CDATA[B]]></SHORT-NAME>",
    "<SHORT-NAME>&#66;</SHORT-NAME>",
])
def test_equivalent_component_names_allow_insertion_and_rerun(tmp_path, name):
    write_twins(tmp_path, {
        "Components": [B],
        "P-Ports": [{**SR_PORT, "Create_RTE_Artifact": "Y", "Runnable_Name": "Run"}],
    }, minimal=True)
    patch_twins(tmp_path, "<SHORT-NAME>B</SHORT-NAME>", name)
    original = (tmp_path / "twins.arxml").read_bytes()
    result = run_prepared_twins(tmp_path)
    assert result.returncode == 0, result.stderr
    output = (tmp_path / "out" / "twins.arxml").read_bytes()
    assert name.encode() in output
    root = ET.fromstring(output)
    assert root.find(".//ar:P-PORT-PROTOTYPE/ar:SHORT-NAME", NS).text == "p_Out"
    assert root.find(".//ar:DATA-SEND-POINTS/ar:VARIABLE-ACCESS", NS) is not None
    assert (tmp_path / "twins.arxml").read_bytes() == original
    (tmp_path / "twins.arxml").write_bytes(output)
    second = run_prepared_twins(tmp_path)
    assert second.returncode == 0, second.stderr
    assert "0 ARXML file(s) affected" in second.stdout


@pytest.mark.parametrize("name", [
    "<SHORT-NAME >AppT</SHORT-NAME >",
    "<SHORT-NAME><![CDATA[AppT]]></SHORT-NAME>",
    "<SHORT-NAME>App&#84;</SHORT-NAME>",
])
def test_equivalent_type_names_preserve_reference_dest(tmp_path, name):
    write_twins(tmp_path, {
        "SR-Interfaces": [{"Interface_Path": "/Pkg/NewIf", "Data_Element_Name": "Value",
                           "Data_Type_Ref": "/Pkg/AppT", "Target_ARXML": "twins.arxml"}],
    }, minimal=True)
    patch_twins(tmp_path, "<ELEMENTS>", "<ELEMENTS><APPLICATION-PRIMITIVE-DATA-TYPE>"
                f"{name}<CATEGORY>VALUE</CATEGORY></APPLICATION-PRIMITIVE-DATA-TYPE>")
    result = run_prepared_twins(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "/Pkg/AppT not found" not in result.stderr
    root = ET.parse(tmp_path / "out" / "twins.arxml").getroot()
    interface = next(item for item in root.findall(".//ar:SENDER-RECEIVER-INTERFACE", NS)
                     if item.findtext("ar:SHORT-NAME", namespaces=NS) == "NewIf")
    reference = interface.find(".//ar:TYPE-TREF", NS)
    assert reference.text == "/Pkg/AppT"
    assert reference.get("DEST") == "APPLICATION-PRIMITIVE-DATA-TYPE"


def test_colliding_generated_cs_runnable_names_are_errors(tmp_path):
    result = run_twins(tmp_path, {
        "Components": [B],
        "CS-Interfaces": [cs_interface("/Pkg/CsBC", "B_C"), cs_interface("/Pkg/CsC", "C")],
        "P-Ports": [
            {"SW_C": "B", "Port_Name": "p_A", "Port_Interface_Ref": "/Pkg/CsBC", "Create_RTE_Artifact": "Y"},
            {"SW_C": "B", "Port_Name": "p_A_B", "Port_Interface_Ref": "/Pkg/CsC", "Create_RTE_Artifact": "Y"},
        ],
    }, minimal=True)
    assert result.returncode == 1
    assert (
        "Runnable /Pkg/B/IB/RE_p_A_B_C for /Pkg/B/p_A_B operation C collides with the runnable planned in "
        "this run for /Pkg/B/p_A operation B_C"
    ) in result.stderr
    assert "already exists" not in result.stderr
    assert "reused" not in result.stdout


def test_colliding_generated_cs_event_names_are_errors(tmp_path):
    result = run_twins(tmp_path, {
        "Components": [B],
        "CS-Interfaces": [cs_interface("/Pkg/CsBC", "B_C"), cs_interface("/Pkg/CsC", "C")],
        "P-Ports": [
            {"SW_C": "B", "Port_Name": "p_A", "Port_Interface_Ref": "/Pkg/CsBC", "Create_RTE_Artifact": "Y",
             "Runnable_Name": "First"},
            {"SW_C": "B", "Port_Name": "p_A_B", "Port_Interface_Ref": "/Pkg/CsC", "Create_RTE_Artifact": "Y",
             "Runnable_Name": "Second"},
        ],
    }, minimal=True)
    assert result.returncode == 1
    assert (
        "Operation event /Pkg/B/IB/oie_p_A_B_C for /Pkg/B/p_A_B operation C collides with the event planned in "
        "this run for /Pkg/B/p_A operation B_C"
    ) in result.stderr
    assert "already exists" not in result.stderr


def test_colliding_generated_server_call_names_are_errors(tmp_path):
    result = run_twins(tmp_path, {
        "Components": [B],
        "CS-Interfaces": [cs_interface("/Pkg/CsBC", "B_C"), cs_interface("/Pkg/CsC", "C")],
        "R-Ports": [
            {"SW_C": "B", "Port_Name": "r_A", "Port_Interface_Ref": "/Pkg/CsBC", "Create_RTE_Artifact": "Y",
             "Runnable_Name": "Run"},
            {"SW_C": "B", "Port_Name": "r_A_B", "Port_Interface_Ref": "/Pkg/CsC", "Create_RTE_Artifact": "Y",
             "Runnable_Name": "Run"},
        ],
    }, minimal=True)
    assert result.returncode == 1
    assert (
        "Server call /Pkg/B/IB/Run/scp_r_A_B_C for /Pkg/B/r_A_B operation C collides with the server call "
        "planned in this run for /Pkg/B/r_A operation B_C"
    ) in result.stderr
    assert "already exists" not in result.stderr


def test_namespace_prefixed_target_is_a_clear_error(tmp_path):
    write_twins(tmp_path, {"Components": [B], "P-Ports": [SR_PORT]}, minimal=True)
    path = tmp_path / "twins.arxml"
    path.write_text(to_prefixed(path.read_text(encoding="utf-8")), encoding="utf-8")
    result = run_prepared_twins(tmp_path)
    assert result.returncode == 1
    assert "namespace-prefixed ARXML is not supported" in result.stderr
    assert str(path) in result.stderr
    assert "Expected one element" not in result.stderr
    assert not (tmp_path / "out").exists()


def test_types_in_namespace_prefixed_file_are_found(tmp_path):
    write_twins(tmp_path, {
        "SR-Interfaces": [{"Interface_Path": "/Pkg/AppIf", "Data_Element_Name": "v",
                           "Data_Type_Ref": "/Ext/AppT", "Target_ARXML": "twins.arxml"}],
    }, minimal=True)
    (tmp_path / "ext.arxml").write_text(to_prefixed(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<AUTOSAR xmlns="http://autosar.org/schema/r4.0">\n'
        "  <AR-PACKAGES>\n    <AR-PACKAGE>\n      <SHORT-NAME>Ext</SHORT-NAME>\n      <ELEMENTS>\n"
        "        <APPLICATION-PRIMITIVE-DATA-TYPE>\n          <SHORT-NAME>AppT</SHORT-NAME>\n"
        "          <CATEGORY>VALUE</CATEGORY>\n        </APPLICATION-PRIMITIVE-DATA-TYPE>\n"
        "      </ELEMENTS>\n    </AR-PACKAGE>\n  </AR-PACKAGES>\n</AUTOSAR>\n"
    ), encoding="utf-8")
    (tmp_path / "arxml.lst").write_text("twins.arxml\next.arxml\n", encoding="utf-8")
    result = run_prepared_twins(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "/Ext/AppT not found" not in result.stderr
    root = ET.parse(tmp_path / "out" / "twins.arxml").getroot()
    interface = next(
        element for element in root.iter(f"{{{NS['ar']}}}SENDER-RECEIVER-INTERFACE")
        if element.findtext("ar:SHORT-NAME", namespaces=NS) == "AppIf"
    )
    reference = interface.find(".//ar:TYPE-TREF", NS)
    assert reference is not None and reference.text == "/Ext/AppT"
    assert reference.get("DEST") == "APPLICATION-PRIMITIVE-DATA-TYPE"


def test_existing_runnable_without_optional_elements_is_reused(tmp_path):
    write_twins(tmp_path, {
        "Components": [B],
        "P-Ports": [{"SW_C": "B", "Port_Name": "p_Srv", "Port_Interface_Ref": "/Pkg/CsIf",
                     "Create_RTE_Artifact": "Y"}],
    }, minimal=True)
    # 其他工具生成的 runnable 可能省略 SYMBOL、MINIMUM-START-INTERVAL 和 CAN-BE-INVOKED-CONCURRENTLY。
    patch_twins(
        tmp_path,
        "<SYMBOL>B_Run</SYMBOL>\n                </RUNNABLE-ENTITY>",
        "<SYMBOL>B_Run</SYMBOL>\n                </RUNNABLE-ENTITY>\n                <RUNNABLE-ENTITY>\n"
        "                  <SHORT-NAME>RE_p_Srv_Op</SHORT-NAME>\n                </RUNNABLE-ENTITY>",
    )
    result = run_prepared_twins(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "Runnable /Pkg/B/IB/RE_p_Srv_Op already exists with the same definition; skipped" in result.stderr
    assert "CS artifacts: 1 to create, 0 skipped" in result.stdout


def test_c_keyword_runnable_symbol_is_a_warning(tmp_path):
    result = run_twins(tmp_path, {
        "Components": [B],
        "P-Ports": [{"SW_C": "B", "Port_Name": "p_Srv", "Port_Interface_Ref": "/Pkg/CsIf",
                     "Create_RTE_Artifact": "Y", "Runnable_Symbol": "return"}],
    }, minimal=True)
    assert result.returncode == 0, result.stderr
    assert "Runnable /Pkg/B/IB/RE_p_Srv_Op uses SYMBOL return, which is a C keyword" in result.stderr
    assert "ERROR" not in result.stderr


def test_recursive_list_ignores_previous_output_inside_project_root(tmp_path):
    write_twins(tmp_path, {"Components": [B], "P-Ports": [SR_PORT]}, minimal=True)
    (tmp_path / "arxml.lst").write_text("**/*.arxml\n", encoding="utf-8")
    first = run_prepared_twins(tmp_path)
    assert first.returncode == 0, first.stderr
    expected = (tmp_path / "out" / "twins.arxml").read_bytes()

    second = run_prepared_twins(tmp_path)
    assert second.returncode == 0, second.stderr
    assert f"Exclude ARXML file under outputDir: {tmp_path / 'out' / 'twins.arxml'}" in second.stdout
    assert "Multiple definitions" not in second.stderr
    assert "Validated 1 of 1 requested ports: 1 to create" in second.stdout
    assert (tmp_path / "out" / "twins.arxml").read_bytes() == expected


def test_output_directory_equal_to_project_root_is_still_rejected(tmp_path):
    write_twins(tmp_path, {"Components": [B], "P-Ports": [SR_PORT]}, minimal=True)
    (tmp_path / "arxml.lst").write_text("**/*.arxml\n", encoding="utf-8")
    original = (tmp_path / "twins.arxml").read_bytes()
    result = run_tool(
        SWC, "--project-root", tmp_path, "--arxml-file-list", tmp_path / "arxml.lst",
        "--workbook", tmp_path / "ports.xlsx", "--log-dir", tmp_path / "log", "--output-dir", tmp_path,
    )
    assert result.returncode == 1
    assert "overlaps an input file" in result.stderr
    assert "Exclude ARXML file under outputDir" not in result.stdout
    assert (tmp_path / "twins.arxml").read_bytes() == original

def make_directory_link(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        pass
    try:
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    except (ImportError, AttributeError, OSError) as error:
        pytest.skip(f"Directory links unavailable: {error}")


LINKED_ROWS = {
    "Components": [B],
    "SR-Interfaces": [{"Interface_Path": "/Pkg/NewIf", "Data_Element_Name": "v", "Data_Type_Ref": "/Pkg/uint8",
                       "Target_ARXML": "linked/twins.arxml"}],
    "P-Ports": [{"SW_C": "B", "Port_Name": "p_New", "Port_Interface_Ref": "/Pkg/NewIf"}],
}


def run_project(project: Path, log_dir: Path, *output: object):
    return run_tool(
        SWC, "--project-root", project, "--arxml-file-list", project / "arxml.lst",
        "--workbook", project / "ports.xlsx", "--log-dir", log_dir, *output,
    )


@pytest.mark.parametrize("listed", [False, True])
def test_target_in_linked_directory_outside_project_is_written_below_output_dir(tmp_path, listed):
    project, external = tmp_path / "project", tmp_path / "external"
    project.mkdir()
    external.mkdir()
    # 未列入清单时只能按 Target ARXML 的写法确定输出路径；列入清单时与清单条目视为同一文件。
    rows = LINKED_ROWS if listed else {"SR-Interfaces": LINKED_ROWS["SR-Interfaces"]}
    write_twins(project, rows, minimal=True)
    shutil.copyfile(project / "twins.arxml", external / "twins.arxml")
    original = (external / "twins.arxml").read_bytes()
    make_directory_link(project / "linked", external)
    (project / "arxml.lst").write_text(
        "linked/twins.arxml\n" if listed else "twins.arxml\n", encoding="utf-8"
    )

    result = run_project(project, tmp_path / "log", "--output-dir", tmp_path / "out")
    assert result.returncode == 0, result.stderr
    assert "1 ARXML file(s) affected" in result.stdout
    output = (tmp_path / "out" / "linked" / "twins.arxml").read_text(encoding="utf-8")
    assert "<SHORT-NAME>NewIf</SHORT-NAME>" in output
    assert ("<SHORT-NAME>p_New</SHORT-NAME>" in output) == listed
    assert (external / "twins.arxml").read_bytes() == original


def test_file_listed_and_targeted_through_different_paths_is_one_file(tmp_path):
    project = tmp_path / "project"
    (project / "real").mkdir(parents=True)
    write_twins(project, LINKED_ROWS, minimal=True)
    shutil.move(str(project / "twins.arxml"), str(project / "real" / "twins.arxml"))
    make_directory_link(project / "linked", project / "real")
    (project / "arxml.lst").write_text("real/twins.arxml\n", encoding="utf-8")

    result = run_project(project, tmp_path / "log", "--in-place")
    assert result.returncode == 0, result.stderr
    assert "1 ARXML file(s) affected" in result.stdout
    assert "Successfully wrote 1 ARXML file(s)" in result.stdout
    updated = (project / "real" / "twins.arxml").read_text(encoding="utf-8")
    assert "<SHORT-NAME>NewIf</SHORT-NAME>" in updated and "<SHORT-NAME>p_New</SHORT-NAME>" in updated
