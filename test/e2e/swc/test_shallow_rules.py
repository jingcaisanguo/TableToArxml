"""SWC 浅校验回归：ComSpec 种类、访问点约束与去重、命名空间、CS 对象顺序、类型引用和输入卫生。"""

from __future__ import annotations

import shutil
import xml.etree.ElementTree as ET

import pytest

from helpers import NS, ROOT, SWC, run_tool
from swc_fixtures import run_prepared_twins, run_twins, write_twins

B = {"SW_C": "B", "Component_Path": "/Pkg/B"}
MIXED_INTERFACE = [
    {"Interface_Path": "/Pkg/QIf", "Data_Element_Name": "q", "Data_Type_Ref": "/Pkg/uint8",
     "Is_Queued": "Y", "Target_ARXML": "twins.arxml"},
    {"Interface_Path": "/Pkg/QIf", "Data_Element_Name": "v", "Data_Type_Ref": "/Pkg/uint8",
     "Target_ARXML": "twins.arxml"},
]
QUEUED_INTERFACE = [{**MIXED_INTERFACE[0], "Interface_Path": "/Pkg/OnlyQ"}]


def output_root(tmp_path) -> ET.Element:
    return ET.parse(tmp_path / "out" / "twins.arxml").getroot()


def named(root: ET.Element, tag: str, name: str) -> ET.Element:
    return next(
        element for element in root.iter(f"{{{NS['ar']}}}{tag}")
        if element.findtext("ar:SHORT-NAME", namespaces=NS) == name
    )


def names(element: ET.Element, path: str) -> list[str]:
    return [child.findtext("ar:SHORT-NAME", namespaces=NS) for child in element.findall(path, NS)]


def run_b_runnable(tmp_path) -> ET.Element:
    component = named(output_root(tmp_path), "APPLICATION-SW-COMPONENT-TYPE", "B")
    return named(component, "RUNNABLE-ENTITY", "Run")


def patch_twins(tmp_path, old: str, new: str, count: int = 1) -> None:
    path = tmp_path / "twins.arxml"
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, count), encoding="utf-8")


def rerun_on_output(tmp_path):
    shutil.copyfile(tmp_path / "out" / "twins.arxml", tmp_path / "twins.arxml")
    shutil.rmtree(tmp_path / "out")
    return run_prepared_twins(tmp_path)


# A：ComSpec 种类由数据元素的 SW-IMPL-POLICY 决定
def test_receiver_com_spec_kind_follows_data_element_policy(tmp_path):
    result = run_twins(tmp_path, {
        "Components": [B],
        "SR-Interfaces": MIXED_INTERFACE,
        "R-Ports": [{"SW_C": "B", "Port_Name": "r_Mixed", "Port_Interface_Ref": "/Pkg/QIf",
                     "Enable_Update": "Y", "Queue_Length": "4"}],
    }, minimal=True)
    assert result.returncode == 0, result.stderr
    port = named(output_root(tmp_path), "R-PORT-PROTOTYPE", "r_Mixed")
    queued = port.findall("ar:REQUIRED-COM-SPECS/ar:QUEUED-RECEIVER-COM-SPEC", NS)
    plain = port.findall("ar:REQUIRED-COM-SPECS/ar:NONQUEUED-RECEIVER-COM-SPEC", NS)
    assert [spec.findtext("ar:DATA-ELEMENT-REF", namespaces=NS) for spec in queued] == ["/Pkg/QIf/q"]
    assert queued[0].findtext("ar:QUEUE-LENGTH", namespaces=NS) == "4"
    assert [spec.findtext("ar:DATA-ELEMENT-REF", namespaces=NS) for spec in plain] == ["/Pkg/QIf/v"]
    assert plain[0].findtext("ar:ENABLE-UPDATE", namespaces=NS) == "true"

    repeated = rerun_on_output(tmp_path)
    assert repeated.returncode == 0, repeated.stderr
    assert "already exists with the same definition" in repeated.stderr


def test_sender_com_spec_kind_follows_data_element_policy(tmp_path):
    result = run_twins(tmp_path, {
        "Components": [B],
        "SR-Interfaces": MIXED_INTERFACE,
        "P-Ports": [{"SW_C": "B", "Port_Name": "p_Mixed", "Port_Interface_Ref": "/Pkg/QIf",
                     "Transmission_Acknowledge_Timeout": "0.5", "Uses_End_To_End_Protection": "Y"}],
    }, minimal=True)
    assert result.returncode == 0, result.stderr
    port = named(output_root(tmp_path), "P-PORT-PROTOTYPE", "p_Mixed")
    specs = list(port.find("ar:PROVIDED-COM-SPECS", NS))
    assert [spec.tag.rsplit("}", 1)[-1] for spec in specs] == [
        "QUEUED-SENDER-COM-SPEC", "NONQUEUED-SENDER-COM-SPEC"]
    for spec in specs:
        assert spec.findtext("ar:TRANSMISSION-ACKNOWLEDGE/ar:TIMEOUT", namespaces=NS) == "0.5"
        assert spec.findtext("ar:USES-END-TO-END-PROTECTION", namespaces=NS) == "true"

    repeated = rerun_on_output(tmp_path)
    assert repeated.returncode == 0, repeated.stderr


@pytest.mark.parametrize("interface,update,queue,message,expected", [
    ("/Pkg/If", "Y", "4", "Queue Length applies only to queued data elements",
     [("NONQUEUED-RECEIVER-COM-SPEC", "/Pkg/If/If")]),
    ("/Pkg/OnlyQ", "Y", "2", "Enable Update applies only to non-queued data elements",
     [("QUEUED-RECEIVER-COM-SPEC", "/Pkg/OnlyQ/q")]),
    ("/Pkg/If", None, "4", "Queue Length applies only to queued data elements", []),
])
def test_inapplicable_receiver_com_spec_column_is_a_warning(tmp_path, interface, update, queue, message, expected):
    result = run_twins(tmp_path, {
        "Components": [B],
        "SR-Interfaces": QUEUED_INTERFACE,
        "R-Ports": [{"SW_C": "B", "Port_Name": "r_Test", "Port_Interface_Ref": interface,
                     "Enable_Update": update, "Queue_Length": queue}],
    }, minimal=True)
    assert result.returncode == 0, result.stderr
    assert "WARNING" in result.stderr and message in result.stderr and "column ignored" in result.stderr
    port = named(output_root(tmp_path), "R-PORT-PROTOTYPE", "r_Test")
    container = port.find("ar:REQUIRED-COM-SPECS", NS)
    specs = [] if container is None else list(container)
    assert [(spec.tag.rsplit("}", 1)[-1], spec.findtext("ar:DATA-ELEMENT-REF", namespaces=NS))
            for spec in specs] == expected

    repeated = rerun_on_output(tmp_path)
    assert repeated.returncode == 0, repeated.stderr


# B：访问类别与队列策略、ENABLE-UPDATE 的 AUTOSAR 组合规则只警告，访问点照常生成
@pytest.mark.parametrize("interface,kind,update,message,container,name", [
    ("/Pkg/OnlyQ", "Read", None, "DataReadAccess for queued data elements", "DATA-READ-ACCESSS", "dra_r_Test"),
    ("/Pkg/OnlyQ", "ReceiveByValue", None, "ReceiveByValue for queued data elements",
     "DATA-RECEIVE-POINT-BY-VALUES", "drpval_r_Test"),
    ("/Pkg/If", "Read", "Y", "DataReadAccess when the receiver ComSpec has ENABLE-UPDATE=true",
     "DATA-READ-ACCESSS", "dra_r_Test"),
])
def test_access_kind_constraints_are_warnings(tmp_path, interface, kind, update, message, container, name):
    result = run_twins(tmp_path, {
        "Components": [B],
        "SR-Interfaces": QUEUED_INTERFACE,
        "R-Ports": [{"SW_C": "B", "Port_Name": "r_Test", "Port_Interface_Ref": interface,
                     "Create_RTE_Artifact": "Y", "Runnable_Name": "Run", "Access_Kind": kind,
                     "Enable_Update": update}],
    }, minimal=True)
    assert result.returncode == 0, result.stderr
    assert "WARNING" in result.stderr and message in result.stderr
    assert "left to EB validation" in result.stderr
    assert names(run_b_runnable(tmp_path), f"ar:{container}/*") == [name]


def test_queued_element_allows_receive_by_argument(tmp_path):
    result = run_twins(tmp_path, {
        "Components": [B],
        "SR-Interfaces": QUEUED_INTERFACE,
        "R-Ports": [{"SW_C": "B", "Port_Name": "r_Test", "Port_Interface_Ref": "/Pkg/OnlyQ",
                     "Create_RTE_Artifact": "Y", "Runnable_Name": "Run", "Queue_Length": "2"}],
    }, minimal=True)
    assert result.returncode == 0, result.stderr
    assert names(run_b_runnable(tmp_path), "ar:DATA-RECEIVE-POINT-BY-ARGUMENTS/*") == ["drparg_r_Test"]


# C：解析数据元素后按 (runnable, 类别, 端口, 数据元素) 去重
def test_duplicate_access_requests_are_merged_after_resolution(tmp_path):
    result = run_twins(tmp_path, {
        "Components": [B],
        "P-Ports": [{"SW_C": "B", "Port_Name": "p_Out", "Port_Interface_Ref": "/Pkg/If",
                     "Create_RTE_Artifact": "Y", "Runnable_Name": "Run"}],
        "Data Accesses": [
            {"SW_C": "B", "Runnable_Name": "Run", "Port_Name": "p_Out"},
            {"SW_C": "B", "Runnable_Name": "Run", "Port_Name": "p_Out", "Access_Kind": "Write"},
        ],
    }, minimal=True)
    assert result.returncode == 0, result.stderr
    assert "Merged 1 duplicate data access request(s)" in result.stdout
    assert "Data accesses: 2 of 2 to create, 0 skipped" in result.stdout
    runnable = run_b_runnable(tmp_path)
    assert names(runnable, "ar:DATA-SEND-POINTS/*") == ["dsp_p_Out"]
    assert names(runnable, "ar:DATA-WRITE-ACCESSS/*") == ["dwa_p_Out"]


@pytest.mark.parametrize("first,second", [("first", "second"), ("acc", "ACC")])
def test_same_access_with_different_explicit_names_is_an_error(tmp_path, first, second):
    result = run_twins(tmp_path, {
        "Components": [B],
        "P-Ports": [{"SW_C": "B", "Port_Name": "p_Out", "Port_Interface_Ref": "/Pkg/If"}],
        "Data Accesses": [
            {"SW_C": "B", "Runnable_Name": "Run", "Port_Name": "p_Out", "Access_Name": first},
            {"SW_C": "B", "Runnable_Name": "Run", "Port_Name": "p_Out", "Data_Element_Name": "If",
             "Access_Name": second},
        ],
    }, minimal=True)
    assert result.returncode == 1
    assert f"different Access Names {first}, {second}" in result.stderr


def test_existing_access_with_other_name_is_reused(tmp_path):
    rows = {
        "Components": [B],
        "P-Ports": [{"SW_C": "B", "Port_Name": "p_Out", "Port_Interface_Ref": "/Pkg/If"}],
        "Data Accesses": [{"SW_C": "B", "Runnable_Name": "Run", "Port_Name": "p_Out",
                           "Access_Name": "custom"}],
    }
    assert run_twins(tmp_path, rows, minimal=True).returncode == 0
    shutil.copyfile(tmp_path / "out" / "twins.arxml", tmp_path / "twins.arxml")
    shutil.rmtree(tmp_path / "out")
    rows["Data Accesses"][0].pop("Access_Name")
    result = run_twins_keep_model(tmp_path, rows)
    assert result.returncode == 0, result.stderr
    assert "already exists as custom; skipped" in result.stderr
    assert "Data accesses: 0 of 1 to create, 0 skipped" in result.stdout


def run_twins_keep_model(tmp_path, rows):
    model = (tmp_path / "twins.arxml").read_bytes()
    write_twins(tmp_path, rows, minimal=True)
    (tmp_path / "twins.arxml").write_bytes(model)
    return run_prepared_twins(tmp_path)


# D：runnable 是全部子对象的命名空间；自动命名避开仅大小写不同的名称，显式名称仅大小写不同只警告
def test_auto_access_name_avoids_any_runnable_child(tmp_path):
    write_twins(tmp_path, {
        "Components": [B],
        "P-Ports": [{"SW_C": "B", "Port_Name": "p_Out", "Port_Interface_Ref": "/Pkg/If",
                     "Create_RTE_Artifact": "Y", "Runnable_Name": "Run"}],
    }, minimal=True)
    patch_twins(
        tmp_path,
        "<DATA-READ-ACCESSS></DATA-READ-ACCESSS>\n                  <DATA-WRITE-ACCESSS></DATA-WRITE-ACCESSS>\n"
        "                  <SYMBOL>B_Run</SYMBOL>",
        "<DATA-READ-ACCESSS><VARIABLE-ACCESS><SHORT-NAME>DSP_P_OUT</SHORT-NAME></VARIABLE-ACCESS>"
        "</DATA-READ-ACCESSS>\n                  <DATA-WRITE-ACCESSS></DATA-WRITE-ACCESSS>\n"
        "                  <SYMBOL>B_Run</SYMBOL>",
    )
    result = run_prepared_twins(tmp_path)
    assert result.returncode == 0, result.stderr
    assert names(run_b_runnable(tmp_path), "ar:DATA-SEND-POINTS/*") == ["dsp_p_Out_If"]


def write_explicit_access_names(tmp_path, names_by_row) -> None:
    write_twins(tmp_path, {
        "Components": [B],
        "P-Ports": [{"SW_C": "B", "Port_Name": name, "Port_Interface_Ref": "/Pkg/If"} for name in ("p_A", "p_B")],
        "Data Accesses": [
            {"SW_C": "B", "Runnable_Name": "Run", "Port_Name": "p_A", "Access_Name": names_by_row[0]},
            {"SW_C": "B", "Runnable_Name": "Run", "Port_Name": "p_B", "Access_Name": names_by_row[1]},
            {"SW_C": "B", "Runnable_Name": "Run", "Port_Name": "p_B", "Access_Kind": "Write",
             "Access_Name": names_by_row[2]},
        ],
    }, minimal=True)
    patch_twins(tmp_path, "<DATA-READ-ACCESSS></DATA-READ-ACCESSS>",
                "<DATA-READ-ACCESSS><VARIABLE-ACCESS><SHORT-NAME>OLD</SHORT-NAME></VARIABLE-ACCESS>"
                "</DATA-READ-ACCESSS>", 2)


def test_explicit_access_names_differing_only_in_case_are_warnings(tmp_path):
    write_explicit_access_names(tmp_path, ("acc", "ACC", "old"))
    result = run_prepared_twins(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "/Pkg/B/IB/Run/ACC: SHORT-NAME differs only in case from acc" in result.stderr
    assert "/Pkg/B/IB/Run/old: SHORT-NAME differs only in case from OLD" in result.stderr
    runnable = run_b_runnable(tmp_path)
    assert names(runnable, "ar:DATA-SEND-POINTS/*") == ["acc", "ACC"]
    assert names(runnable, "ar:DATA-WRITE-ACCESSS/*") == ["old"]
    assert names(runnable, "ar:DATA-READ-ACCESSS/*") == ["OLD"]


def test_explicit_access_name_exact_conflicts_are_errors(tmp_path):
    write_explicit_access_names(tmp_path, ("acc", "acc", "OLD"))
    result = run_prepared_twins(tmp_path)
    assert result.returncode == 1
    assert "/Pkg/B/IB/Run/acc: the name is already used in this run" in result.stderr
    assert "SHORT-NAME OLD is already used by /Pkg/B/IB/Run/OLD" in result.stderr


# E：CS 对象顺序、同次运行的 runnable、失败端口
def test_cs_runnable_created_in_same_run_hosts_calls_and_accesses(tmp_path):
    result = run_twins(tmp_path, {
        "Components": [B],
        "P-Ports": [
            {"SW_C": "B", "Port_Name": "p_Srv", "Port_Interface_Ref": "/Pkg/CsIf",
             "Create_RTE_Artifact": "Y", "Runnable_Name": "RE_Srv"},
            {"SW_C": "B", "Port_Name": "p_Out", "Port_Interface_Ref": "/Pkg/If",
             "Create_RTE_Artifact": "Y", "Runnable_Name": "RE_Srv"},
        ],
        "R-Ports": [{"SW_C": "B", "Port_Name": "r_Cli", "Port_Interface_Ref": "/Pkg/CsIf",
                     "Create_RTE_Artifact": "Y", "Runnable_Name": "RE_Srv"}],
    }, minimal=True)
    assert result.returncode == 0, result.stderr
    component = named(output_root(tmp_path), "APPLICATION-SW-COMPONENT-TYPE", "B")
    behavior = component.find("ar:INTERNAL-BEHAVIORS/ar:SWC-INTERNAL-BEHAVIOR", NS)
    assert [child.tag.rsplit("}", 1)[-1] for child in behavior] == ["SHORT-NAME", "EVENTS", "RUNNABLES"]
    runnable = named(behavior, "RUNNABLE-ENTITY", "RE_Srv")
    assert names(runnable, "ar:DATA-SEND-POINTS/*") == ["dsp_p_Out"]
    assert names(runnable, "ar:SERVER-CALL-POINTS/*") == ["scp_r_Cli_Op"]
    assert [child.tag.rsplit("}", 1)[-1] for child in runnable][-2:] == ["SERVER-CALL-POINTS", "SYMBOL"]
    assert names(behavior, "ar:EVENTS/*") == ["oie_p_Srv_Op"]

    repeated = rerun_on_output(tmp_path)
    assert repeated.returncode == 0, repeated.stderr
    assert "CS artifacts: 0 to create, 0 skipped" in repeated.stdout


@pytest.mark.parametrize("symbol,expected", [("", 0), ("Other", 1)])
def test_shared_cs_runnable_is_planned_once(tmp_path, symbol, expected):
    result = run_twins(tmp_path, {
        "Components": [B],
        "P-Ports": [
            {"SW_C": "B", "Port_Name": "p_One", "Port_Interface_Ref": "/Pkg/CsIf",
             "Create_RTE_Artifact": "Y", "Runnable_Name": "Shared"},
            {"SW_C": "B", "Port_Name": "p_Two", "Port_Interface_Ref": "/Pkg/CsIf",
             "Create_RTE_Artifact": "Y", "Runnable_Name": "Shared", "Runnable_Symbol": symbol or None},
        ],
    }, minimal=True)
    assert result.returncode == expected, result.stderr
    if expected:
        assert "already planned in this run with a different definition" in result.stderr
        return
    assert "already planned in this run with the same definition; reused" in result.stdout
    component = named(output_root(tmp_path), "APPLICATION-SW-COMPONENT-TYPE", "B")
    behavior = named(component, "SWC-INTERNAL-BEHAVIOR", "IB")
    assert names(behavior, "ar:RUNNABLES/*").count("Shared") == 1
    assert names(behavior, "ar:EVENTS/*") == ["oie_p_One_Op", "oie_p_Two_Op"]


def test_duplicate_runnable_symbol_is_a_warning(tmp_path):
    result = run_twins(tmp_path, {
        "Components": [B],
        "P-Ports": [{"SW_C": "B", "Port_Name": "p_Srv", "Port_Interface_Ref": "/Pkg/CsIf",
                     "Create_RTE_Artifact": "Y", "Runnable_Symbol": "B_Run"}],
    }, minimal=True)
    assert result.returncode == 0, result.stderr
    assert "uses SYMBOL B_Run, which is already used by runnable /Pkg/B/IB/Run" in result.stderr


def test_failed_port_does_not_create_cs_artifacts(tmp_path):
    result = run_twins(tmp_path, {
        "Components": [B],
        "P-Ports": [{"SW_C": "B", "Port_Name": "IB", "Port_Interface_Ref": "/Pkg/CsIf",
                     "Create_RTE_Artifact": "Y"}],
    }, minimal=True)
    assert result.returncode == 1
    assert "/Pkg/B/IB" in result.stderr
    assert "CS artifacts: 0 to create, 0 skipped" in result.stdout
    assert "Validated 0 of 1 requested ports" in result.stdout


# H：类型引用的 DEST 取被引用对象的实际元类
TYPES = (
    "<SW-BASE-TYPE><SHORT-NAME>base8</SHORT-NAME></SW-BASE-TYPE>\n"
    "        <APPLICATION-PRIMITIVE-DATA-TYPE><SHORT-NAME>AppT</SHORT-NAME>"
    "<CATEGORY>VALUE</CATEGORY></APPLICATION-PRIMITIVE-DATA-TYPE>\n"
    "        <SENDER-RECEIVER-INTERFACE>"
)


@pytest.mark.parametrize("sheet,record,returncode,message", [
    ("SR-Interfaces", {"Interface_Path": "/Pkg/NewIf", "Data_Element_Name": "v",
                       "Data_Type_Ref": "/Pkg/base8"}, 1, "refers to SW-BASE-TYPE"),
    ("SR-Interfaces", {"Interface_Path": "/Pkg/NewIf", "Data_Element_Name": "v",
                       "Data_Type_Ref": "/Pkg/AppT"}, 0, ""),
    ("SR-Interfaces", {"Interface_Path": "/Pkg/NewIf", "Data_Element_Name": "v",
                       "Data_Type_Ref": "/Pkg/Missing"}, 0, "not found in loaded ARXML"),
    ("Arrays", {"Type_Path": "/Pkg/Arr", "Element_Name": "e", "Element_Type_Ref": "/Pkg/AppT",
                "Array_Size": "2"}, 1, "refers to APPLICATION-PRIMITIVE-DATA-TYPE"),
    ("Enumerations", {"Type_Path": "/Pkg/E", "Value": "0", "Label": "Off",
                      "Implementation_Data_Type_Ref": "/Pkg/base8", "Compu_Method_Ref": "/Pkg/E_cm",
                      "Data_Constr_Ref": "/Pkg/E_dc"}, 1, "refers to SW-BASE-TYPE"),
])
def test_type_reference_meta_class(tmp_path, sheet, record, returncode, message):
    write_twins(tmp_path, {sheet: [{**record, "Target_ARXML": "twins.arxml"}]}, minimal=True)
    patch_twins(tmp_path, "<SENDER-RECEIVER-INTERFACE>", TYPES)
    result = run_prepared_twins(tmp_path)
    assert result.returncode == returncode, result.stderr
    assert message in result.stderr
    if record.get("Data_Type_Ref") == "/Pkg/AppT":
        assert b'<TYPE-TREF DEST="APPLICATION-PRIMITIVE-DATA-TYPE">/Pkg/AppT</TYPE-TREF>' in (
            tmp_path / "out" / "twins.arxml").read_bytes()


def test_forward_type_reference_within_the_workbook(tmp_path):
    result = run_twins(tmp_path, {
        "Structures": [{"Type_Path": "/Pkg/Outer", "Member_Name": "inner", "Member_Type_Ref": "/Pkg/Inner",
                        "Target_ARXML": "twins.arxml"},
                       {"Type_Path": "/Pkg/Inner", "Member_Name": "value", "Member_Type_Ref": "/Pkg/Arr",
                        "Target_ARXML": "twins.arxml"}],
        "Arrays": [{"Type_Path": "/Pkg/Arr", "Element_Name": "e", "Element_Type_Ref": "/Pkg/Inner",
                    "Array_Size": "2", "Target_ARXML": "twins.arxml"}],
    }, minimal=True)
    assert result.returncode == 0, result.stderr
    assert "/Pkg/Inner not found" not in result.stderr


# I：SHORT-NAME 仅大小写不同只警告，按不同对象生成（AUTOSAR 规则由 EB 校验）
@pytest.mark.parametrize("rows,message,created", [
    ({"SR-Interfaces": [{"Interface_Path": "/Pkg/IF", "Data_Element_Name": "v", "Data_Type_Ref": "/Pkg/uint8",
                         "Target_ARXML": "twins.arxml"}]}, "differs only in case from existing /Pkg/If",
     [("SENDER-RECEIVER-INTERFACE", "IF"), ("SENDER-RECEIVER-INTERFACE", "If")]),
    ({"Components": [B], "P-Ports": [{"SW_C": "B", "Port_Name": "ib", "Port_Interface_Ref": "/Pkg/If"}]},
     "differs only in case from existing /Pkg/B/IB", [("P-PORT-PROTOTYPE", "ib")]),
    ({"Arrays": [{"Type_Path": "/Pkg/Arr", "Element_Name": "e", "Element_Type_Ref": "/Pkg/uint8",
                  "Array_Size": "2", "Target_ARXML": "twins.arxml"}],
      "Structures": [{"Type_Path": "/Pkg/ARR", "Member_Name": "m", "Member_Type_Ref": "/Pkg/uint8",
                      "Target_ARXML": "twins.arxml"}]}, "differs only in case from array /Pkg/Arr",
     [("IMPLEMENTATION-DATA-TYPE", "Arr"), ("IMPLEMENTATION-DATA-TYPE", "ARR")]),
    ({"SR-Interfaces": [{"Interface_Path": "/Pkg/New", "Data_Element_Name": name, "Data_Type_Ref": "/Pkg/uint8",
                         "Target_ARXML": "twins.arxml"} for name in ("value", "VALUE")]},
     "differs only in case from value", [("VARIABLE-DATA-PROTOTYPE", "value"), ("VARIABLE-DATA-PROTOTYPE", "VALUE")]),
])
def test_short_names_differing_only_in_case_are_warnings(tmp_path, rows, message, created):
    result = run_twins(tmp_path, rows, minimal=True)
    assert result.returncode == 0, result.stderr
    assert "WARNING" in result.stderr and message in result.stderr
    root = output_root(tmp_path)
    for tag, name in created:
        assert named(root, tag, name) is not None


def test_case_variant_ports_create_distinct_rte_objects(tmp_path):
    result = run_twins(tmp_path, {
        "Components": [B],
        "P-Ports": [
            *({"SW_C": "B", "Port_Name": name, "Port_Interface_Ref": "/Pkg/CsIf", "Create_RTE_Artifact": "Y"}
              for name in ("p_srv", "P_SRV")),
            {"SW_C": "B", "Port_Name": "p_cs", "Port_Interface_Ref": "/Pkg/CsIf", "Create_RTE_Artifact": "Y",
             "Runnable_Name": "RUN"},
            *({"SW_C": "B", "Port_Name": name, "Port_Interface_Ref": "/Pkg/If", "Create_RTE_Artifact": "Y",
               "Runnable_Name": "Run"} for name in ("p_out", "P_OUT")),
        ],
        "R-Ports": [{"SW_C": "B", "Port_Name": name, "Port_Interface_Ref": "/Pkg/CsIf",
                     "Create_RTE_Artifact": "Y", "Runnable_Name": "Run"} for name in ("r_cli", "R_CLI")],
    }, minimal=True)
    assert result.returncode == 0, result.stderr
    assert "ERROR" not in result.stderr
    assert "Port P_SRV for component B: SHORT-NAME differs only in case from p_srv" in result.stderr
    assert "SHORT-NAME RUN differs only in case from Run" in result.stderr
    assert "SHORT-NAME oie_P_SRV_Op differs only in case from oie_p_srv_Op" in result.stderr
    assert "SHORT-NAME scp_R_CLI_Op differs only in case from scp_r_cli_Op" in result.stderr
    component = named(output_root(tmp_path), "APPLICATION-SW-COMPONENT-TYPE", "B")
    assert names(component, "ar:PORTS/*") == ["p_srv", "P_SRV", "p_cs", "p_out", "P_OUT", "r_cli", "R_CLI"]
    behavior = named(component, "SWC-INTERNAL-BEHAVIOR", "IB")
    assert names(behavior, "ar:RUNNABLES/*") == ["Run", "RE_p_srv_Op", "RE_P_SRV_Op", "RUN"]
    assert names(behavior, "ar:EVENTS/*") == ["oie_p_srv_Op", "oie_P_SRV_Op", "oie_p_cs_Op"]
    runnable = run_b_runnable(tmp_path)
    assert names(runnable, "ar:SERVER-CALL-POINTS/*") == ["scp_r_cli_Op", "scp_R_CLI_Op"]
    # 自动命名仍主动避开仅大小写不同的名称。
    assert names(runnable, "ar:DATA-SEND-POINTS/*") == ["dsp_p_out", "dsp_P_OUT_If"]

    repeated = rerun_on_output(tmp_path)
    assert repeated.returncode == 0, repeated.stderr
    assert "CS artifacts: 0 to create, 0 skipped" in repeated.stdout
    assert "Data accesses: 0 of 2 to create, 0 skipped" in repeated.stdout


# J：已有对象的数值与未填写属性
def test_existing_limits_are_compared_numerically(tmp_path):
    rows = {"Enumerations": [{"Type_Path": "/Pkg/E", "Value": str(value), "Label": label,
                              "Implementation_Data_Type_Ref": "/Pkg/uint8", "Compu_Method_Ref": "/Pkg/E_cm",
                              "Data_Constr_Ref": "/Pkg/E_dc", "Target_ARXML": "twins.arxml"}
                             for value, label in ((0, "Off"), (1, "On"))]}
    assert run_twins(tmp_path, rows, minimal=True).returncode == 0
    shutil.copyfile(tmp_path / "out" / "twins.arxml", tmp_path / "twins.arxml")
    shutil.rmtree(tmp_path / "out")
    patch_twins(tmp_path, "<LOWER-LIMIT>0</LOWER-LIMIT>", "<LOWER-LIMIT>0.0</LOWER-LIMIT>", 2)
    patch_twins(tmp_path, "<UPPER-LIMIT>1</UPPER-LIMIT>", "<UPPER-LIMIT>1.000</UPPER-LIMIT>", 2)
    result = run_prepared_twins(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "different definition" not in result.stderr


@pytest.mark.parametrize("semantics,returncode", [(None, 0), ("FIXED-SIZE", 1)])
def test_missing_existing_array_size_semantics(tmp_path, semantics, returncode):
    record = {"Type_Path": "/Pkg/Arr", "Element_Name": "e", "Element_Type_Ref": "/Pkg/uint8",
              "Array_Size": "2", "Target_ARXML": "twins.arxml"}
    assert run_twins(tmp_path, {"Arrays": [record]}, minimal=True).returncode == 0
    shutil.copyfile(tmp_path / "out" / "twins.arxml", tmp_path / "twins.arxml")
    shutil.rmtree(tmp_path / "out")
    patch_twins(tmp_path, "<ARRAY-SIZE-SEMANTICS>FIXED-SIZE</ARRAY-SIZE-SEMANTICS>", "")
    result = run_twins_keep_model(tmp_path, {"Arrays": [{**record, "Array_Size_Semantics": semantics}]})
    assert result.returncode == returncode, result.stderr


# K：PR-Port 访问点和 AR-PACKAGE 容器顺序
def test_pr_port_access_is_rejected(tmp_path):
    write_twins(tmp_path, {
        "Components": [B],
        "Data Accesses": [{"SW_C": "B", "Runnable_Name": "Run", "Port_Name": "pr_X"}],
    }, minimal=True)
    text = (tmp_path / "twins.arxml").read_text(encoding="utf-8")
    index = text.index("<PORTS></PORTS>", text.index("<SHORT-NAME>B</SHORT-NAME>"))
    text = text[:index] + (
        "<PORTS><PR-PORT-PROTOTYPE><SHORT-NAME>pr_X</SHORT-NAME>"
        '<PROVIDED-REQUIRED-INTERFACE-TREF DEST="SENDER-RECEIVER-INTERFACE">/Pkg/If'
        "</PROVIDED-REQUIRED-INTERFACE-TREF></PR-PORT-PROTOTYPE></PORTS>"
    ) + text[index + len("<PORTS></PORTS>"):]
    (tmp_path / "twins.arxml").write_text(text, encoding="utf-8")
    result = run_prepared_twins(tmp_path)
    assert result.returncode == 1
    assert "PR-Port /Pkg/B/pr_X is not supported" in result.stderr


# G：输入卫生
def test_formula_cell_is_rejected(tmp_path):
    write_twins(tmp_path, {
        "Components": [B],
        "P-Ports": [{"SW_C": "B", "Port_Name": '="p_" & "Out"', "Port_Interface_Ref": "/Pkg/If"}],
    }, minimal=True)
    result = run_prepared_twins(tmp_path)
    assert result.returncode == 1
    assert "P-Ports!Port Name2: formula cells are not supported; paste values instead" in result.stderr


def test_control_characters_are_rejected(tmp_path):
    result = run_twins(tmp_path, {
        "Enumerations": [{"Type_Path": "/Pkg/E", "Value": "0", "Label": "Off\nState",
                          "Implementation_Data_Type_Ref": "/Pkg/uint8", "Target_ARXML": "twins.arxml"}],
    }, minimal=True)
    assert result.returncode == 1
    assert "Enumerations!Label2 contains control characters" in result.stderr


def test_non_utf8_target_is_not_modified(tmp_path):
    write_twins(tmp_path, {
        "Components": [B],
        "P-Ports": [{"SW_C": "B", "Port_Name": "p_Out", "Port_Interface_Ref": "/Pkg/If"}],
    }, minimal=True)
    patch_twins(tmp_path, 'encoding="UTF-8"', 'encoding="ISO-8859-1"')
    result = run_prepared_twins(tmp_path)
    assert result.returncode == 1
    assert "XML declaration encoding 'ISO-8859-1' is not supported" in result.stderr
    assert not (tmp_path / "out").exists()


def test_corrupt_workbook_is_reported_without_traceback(tmp_path):
    write_twins(tmp_path, {"Components": [B]}, minimal=True)
    (tmp_path / "ports.xlsx").write_bytes(b"not a workbook")
    result = run_prepared_twins(tmp_path)
    assert result.returncode == 1
    assert "Cannot read workbook ports.xlsx" in result.stderr
    assert "Traceback" not in result.stderr


def test_sample_output_is_idempotent(tmp_path):
    first = run_tool(
        SWC, "--project-root", ROOT, "--config", ROOT / "test/samples/swc/TableToSwcArxml.toml",
        "--output-dir", tmp_path / "out", "--log-dir", tmp_path / "log",
    )
    assert first.returncode == 0, first.stderr
    project = tmp_path / "project"
    shutil.copytree(ROOT / "test/data/common", project / "test/data/common")
    shutil.copytree(tmp_path / "out", project, dirs_exist_ok=True)
    second = run_tool(
        SWC, "--project-root", project, "--arxml-file-list", project / "test/data/common/arxml.lst",
        "--workbook", ROOT / "test/samples/swc/SwcTestPorts.xlsx", "--log-dir", tmp_path / "log",
        "--output-dir", tmp_path / "out2",
    )
    assert second.returncode == 0, second.stderr
    assert "ERROR" not in second.stderr
    assert "Validated 4 of 4 requested ports: 0 to create, 4 already valid, 0 skipped" in second.stdout
    assert "Successfully wrote 0 ARXML file(s)" in second.stdout
    assert not list((tmp_path / "out2").rglob("*.arxml"))
