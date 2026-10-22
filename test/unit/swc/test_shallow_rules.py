"""浅校验的单元回归：输入卫生、仅大小写不同只警告、自动命名、数值比较和 schema 顺序。"""

from __future__ import annotations

import logging

import pytest

from table_to_arxml.swc import workbook
from table_to_arxml.swc.apply import number_text
from table_to_arxml.swc.resolve import generate_access_name
from table_to_arxml.swc.xml_patch import (
    case_variants,
    child_names,
    encoding_problem,
    insert_or_create_child_container,
)

COMPONENTS = [("SW-C", "Component Path"), ("A", "/Pkg/A")]


@pytest.mark.parametrize("value", ["Off\nOn", "\tOff", "Off\r", "Off\x00"])
def test_control_characters_are_rejected_with_location(value, caplog):
    records = workbook.read_sheet_rows({
        "Components": [("SW-C", "Component Path"), (value, "/Pkg/A")],
    }, "Components")
    assert records == []
    assert "Components!SW-C2 contains control characters" in caplog.text


def test_formula_cells_are_rejected(caplog):
    records = workbook.read_sheet_rows({
        "Components": [("SW-C", "Component Path"), ("A", workbook.FormulaCell('="/Pkg/"&"A"'))],
    }, "Components")
    assert records == []
    assert "Components!Component Path2: formula cells are not supported" in caplog.text


def test_corrupt_workbook_is_a_value_error(tmp_path):
    path = tmp_path / "broken.xlsx"
    path.write_bytes(b"PK\x03\x04 not really a zip")
    with pytest.raises(ValueError, match="Cannot read workbook broken.xlsx"):
        workbook.load_workbook_sheets(path)


@pytest.mark.parametrize("second", ["p_test", "P_TEST"])
def test_port_names_differing_only_in_case_are_a_warning(second, caplog):
    _, ports = workbook.read_workbook({
        "Components": COMPONENTS,
        "P-Ports": [("SW-C", "Port Name", "Port Interface Ref"), ("A", "p_Test", "/Pkg/If")],
        "R-Ports": [("SW-C", "Port Name", "Port Interface Ref"), ("A", second, "/Pkg/If"),
                    ("A", "p_Test", "/Pkg/If")],
    })
    assert [port.name for port in ports] == ["p_Test", second]
    assert [record.levelname for record in caplog.records] == ["WARNING", "ERROR"]
    assert "differs only in case from p_Test" in caplog.records[0].getMessage()
    assert "Skipped duplicate port for component A: p_Test" in caplog.records[1].getMessage()


def test_enable_update_and_queue_length_can_be_combined(caplog):
    _, ports = workbook.read_workbook({
        "Components": COMPONENTS,
        "R-Ports": [("SW-C", "Port Name", "Port Interface Ref", "Enable Update", "Queue Length"),
                    ("A", "r_Test", "/Pkg/If", "Y", "4")],
    })
    assert ports[0].enable_update and ports[0].queue_length == 4
    assert not caplog.records


def test_data_access_duplicate_key_is_exact_and_includes_access_kind(caplog):
    specs = workbook.read_data_accesses({
        "Data Accesses": [
            ("SW-C", "Runnable Name", "Port Name", "Access Kind"),
            ("A", "Run", "p_Out", "Send"),
            ("A", "Run", "p_Out", "Write"),
            ("A", "run", "P_OUT", "send"),
            ("A", "Run", "p_Out", "send"),
        ],
    }, {"A": workbook.ComponentSpec("A", "/Pkg/A")})
    assert [(spec.runnable_name, spec.port_name, spec.kind) for spec in specs] == [
        ("Run", "p_Out", "send"), ("Run", "p_Out", "write"), ("run", "P_OUT", "send")]
    assert [record.levelname for record in caplog.records] == ["ERROR"]
    assert "Skipped duplicate data access row: A / Run / p_Out" in caplog.text


@pytest.mark.parametrize("sheet,rows,message,members", [
    ("SR-Interfaces", [("Interface Path", "Data Element Name", "Data Type Ref", "Target ARXML"),
                       ("/Pkg/If", "value", "/Pkg/T", "t.arxml"), ("/Pkg/If", "Value", "/Pkg/T", "t.arxml")],
     "data element Value in SR interface /Pkg/If: SHORT-NAME differs only in case from value",
     ["value", "Value"]),
    ("Structures", [("Type Path", "Member Name", "Member Type Ref", "Target ARXML"),
                    ("/Pkg/S", "x", "/Pkg/T", "t.arxml"), ("/Pkg/S", "X", "/Pkg/T", "t.arxml")],
     "member X in structure /Pkg/S: SHORT-NAME differs only in case from x", ["x", "X"]),
    ("CS-Interfaces", [("Interface Path", "Operation Name", "Target ARXML"),
                       ("/Pkg/Cs", "Reset", "t.arxml"), ("/Pkg/Cs", "RESET", "t.arxml")],
     "operation RESET: SHORT-NAME differs only in case from Reset", ["Reset", "RESET"]),
])
def test_member_names_differing_only_in_case_are_a_warning(tmp_path, sheet, rows, message, members, caplog):
    (tmp_path / "t.arxml").touch()
    reader = {
        "SR-Interfaces": workbook.read_sr_interfaces,
        "Structures": workbook.read_structures,
        "CS-Interfaces": workbook.read_cs_interfaces,
    }[sheet]
    specs = reader({sheet: rows}, tmp_path)
    found = getattr(specs[0], {"SR-Interfaces": "data_elements", "Structures": "members",
                               "CS-Interfaces": "operations"}[sheet])
    assert [member[0] for member in found] == members
    assert [record.levelname for record in caplog.records] == ["WARNING"]
    assert message in caplog.text


@pytest.mark.parametrize("sheet,rows", [
    ("SR-Interfaces", [("Interface Path", "Data Element Name", "Data Type Ref", "Target ARXML"),
                       ("/Pkg/If", "value", "/Pkg/T", "t.arxml"), ("/Pkg/If", "value", "/Pkg/T", "t.arxml")]),
    ("Structures", [("Type Path", "Member Name", "Member Type Ref", "Target ARXML"),
                    ("/Pkg/S", "x", "/Pkg/T", "t.arxml"), ("/Pkg/S", "x", "/Pkg/U", "t.arxml")]),
])
def test_exact_duplicate_member_names_are_errors(tmp_path, sheet, rows, caplog):
    (tmp_path / "t.arxml").touch()
    reader = {"SR-Interfaces": workbook.read_sr_interfaces, "Structures": workbook.read_structures}[sheet]
    reader({sheet: rows}, tmp_path)
    assert [record.levelname for record in caplog.records] == ["ERROR"]
    assert "Skipped duplicate" in caplog.text


def test_new_paths_differing_only_in_case_are_a_warning(caplog):
    workbook.warn_case_variant_paths([
        ("/Pkg/Types/Mode", "enumeration"), ("/Pkg/Types/MODE", "array"), ("/Pkg/Types/Mode", "structure"),
    ])
    assert [record.levelname for record in caplog.records] == ["WARNING"]
    assert "array /Pkg/Types/MODE: SHORT-NAME path differs only in case from enumeration" in caplog.text


def test_enumeration_label_that_is_not_a_c_identifier_is_a_warning(tmp_path, caplog):
    (tmp_path / "t.arxml").touch()
    caplog.set_level(logging.WARNING)
    specs = workbook.read_enumerations({
        "Enumerations": [
            ("Type Path", "Target ARXML", "Implementation Data Type Ref", "Value", "Label"),
            ("/Pkg/Types/Mode", "t.arxml", "/Pkg/Types/uint8", 0, "关闭"),
            ("/Pkg/Types/Mode", "t.arxml", "/Pkg/Types/uint8", 1, "On"),
        ],
    }, tmp_path)
    assert specs[0].values == (("0", "关闭"), ("1", "On"))
    assert [record.levelname for record in caplog.records] == ["WARNING"]
    assert "may need a SYMBOL" in caplog.text


def test_array_size_semantics_keeps_blank_input(tmp_path):
    (tmp_path / "t.arxml").touch()
    specs = workbook.read_arrays({
        "Arrays": [("Type Path", "Element Name", "Element Type Ref", "Array Size", "Target ARXML"),
                   ("/Pkg/A", "e", "/Pkg/T", 2, "t.arxml")],
    }, tmp_path)
    assert specs[0].array_size_semantics == ""
    assert specs[0].effective_semantics == "FIXED-SIZE"


def test_generated_access_name_skips_any_child_case_insensitively():
    taken = {"dsp_p_out": "DSP_P_OUT", "dsp_p_out_value": "dsp_p_Out_value"}
    used = {"dsp_p_out_value2": "dsp_p_Out_value2"}
    assert generate_access_name(taken, used, "send", "p_Out", "value") == "dsp_p_Out_value3"


@pytest.mark.parametrize("left,right,equal", [
    ("1", "1.0", True), ("0", "-0.000", True), ("10", "1E1", True), ("INF", "INF", True),
    ("INF", "inf", False), ("1", "2", False), ("abc", "abc", True),
])
def test_limits_are_compared_numerically(left, right, equal):
    assert (number_text(left) == number_text(right)) == equal


PACKAGE = b"""<AUTOSAR>
  <AR-PACKAGES>
    <AR-PACKAGE>
      <SHORT-NAME>P</SHORT-NAME>
      <ANNOTATIONS></ANNOTATIONS>
      <SHORT-NAME-PATTERN>x</SHORT-NAME-PATTERN>
      <AR-PACKAGES>
        <AR-PACKAGE>
          <SHORT-NAME>Sub</SHORT-NAME>
        </AR-PACKAGE>
      </AR-PACKAGES>
      <VARIATION-POINT></VARIATION-POINT>
    </AR-PACKAGE>
  </AR-PACKAGES>
</AUTOSAR>
"""


def test_package_elements_follow_xsd_order():
    updated = insert_or_create_child_container(PACKAGE, "/P", "ELEMENTS", ["<X/>"]).decode()
    start = updated.index("<SHORT-NAME>P</SHORT-NAME>")
    order = [updated.index(tag, start) for tag in (
        "<SHORT-NAME-PATTERN>", "<ELEMENTS>", "<AR-PACKAGES>", "<VARIATION-POINT>")]
    assert order == sorted(order)


def test_namespace_helpers_use_short_name_paths():
    source = PACKAGE.replace(b"<SHORT-NAME>Sub</SHORT-NAME>", b"<SHORT-NAME>Sub</SHORT-NAME>"
                             b"<ELEMENTS><X><SHORT-NAME>Item</SHORT-NAME></X></ELEMENTS>")
    assert child_names(source, "/P") == ("Sub",)
    assert child_names(source, "/P/Sub") == ("Item",)
    assert case_variants(source, "/p/SUB/item") == ["/P/Sub/Item"]
    assert case_variants(source, "/P/Sub/Item") == []


@pytest.mark.parametrize("declaration,problem", [
    (b'<?xml version="1.0" encoding="UTF-8"?>', False),
    (b"<?xml version='1.0' encoding='utf-8'?>", False),
    (b'\xef\xbb\xbf<?xml version="1.0"?>', False),
    (b"", False),
    (b'<?xml version="1.0" encoding="ISO-8859-1"?>', True),
    (b'<?xml version="1.0" encoding="UTF-16"?>', True),
])
def test_only_utf8_declarations_can_be_modified(declaration, problem):
    assert (encoding_problem(declaration + b"\n<AUTOSAR/>") is not None) == problem
