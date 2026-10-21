"""工作簿规范名称和条件必填规则；不读写实际 ARXML 模型。"""

import pytest

from table_to_arxml.swc import workbook
from table_to_arxml.swc.schema import SHEET_HEADERS


@pytest.fixture
def reader():
    return workbook


@pytest.mark.parametrize("header", ["Implementation Data Type Ref", "implementation data type ref"])
def test_enum_type_header_is_case_insensitive(reader, tmp_path, header, caplog):
    target = tmp_path / "types.arxml"
    target.touch()
    specs = reader.read_enumerations({
        "Enumerations": [
            ("Type Path", "Target ARXML", header, "Value", "Label"),
            ("/Pkg/Types/Mode", target.name, "/Pkg/Types/uint8", 0, "Off"),
            ("/Pkg/Types/Mode", target.name, "/Pkg/Types/uint8", 1, "On"),
        ],
    }, tmp_path)
    assert len(specs) == 1
    assert specs[0].base_type == "/Pkg/Types/uint8"
    assert specs[0].values == (("0", "Off"), ("1", "On"))
    assert not caplog.records


def test_cs_argument_columns_can_be_omitted(reader, tmp_path, caplog):
    (tmp_path / "types.arxml").touch()
    specs = reader.read_cs_interfaces({
        "cs-interfaces": [
            ("Interface Path", "Operation Name", "Target ARXML"),
            ("/Pkg/Service", "Reset", "types.arxml"),
            ("/Pkg/Service", "Stop", "types.arxml"),
        ],
    }, tmp_path)
    assert len(specs) == 1
    assert specs[0].operations == (("Reset", ()), ("Stop", ()))
    assert not caplog.records


def test_cs_parameter_grouping_is_preserved(reader, tmp_path, caplog):
    (tmp_path / "types.arxml").touch()
    specs = reader.read_cs_interfaces({
        "CS-Interfaces": [
            ("Interface Path", "Operation Name", "Argument Name", "Direction", "Data Type Ref", "Target ARXML"),
            ("/Pkg/Service", "Set", "input", "in", "/Pkg/Types/uint8", "types.arxml"),
            ("/Pkg/Service", "Set", "result", "OUT", "/Pkg/Types/uint8", "types.arxml"),
            ("/Pkg/Service", "Reset", "", "", "", "types.arxml"),
        ],
    }, tmp_path)
    assert specs[0].operations == (
        ("Set", (("input", "/Pkg/Types/uint8", "IN"), ("result", "/Pkg/Types/uint8", "OUT"))),
        ("Reset", ()),
    )
    assert not caplog.records


def test_duplicate_sheet_names_are_case_insensitive(reader, caplog):
    specs = reader.read_data_accesses({"Data Accesses": [], "data accesses": []}, {})
    assert specs == []
    assert "Multiple worksheets match Data Accesses" in caplog.text


def test_duplicate_column_names_are_case_insensitive(reader, caplog):
    reader.read_workbook({"Components": [
        ("SW-C", "sw-c", "Component Path"), ("A", "A", "/Pkg/A"),
    ]})
    assert "duplicate=['SW-C']" in caplog.text


@pytest.mark.parametrize("sheet, removed", [
    ("P-Ports", "Create Access"),
    ("P-Ports", "Runnable"),
    ("R-Ports", "Runnable Ref"),
    ("Components", "SWC"),
    ("R-Ports", "Receive Mode"),
    ("Enumerations", "Base Type Ref"),
    ("P-Ports", "Transmission Acknowledge"),
    ("R-Ports", "QueueLength"),
    ("Data Accesses", "Data Element"),
    ("CS-Interfaces", "Operation"),
    ("CS-Interfaces", "Argument"),
])
def test_removed_header_aliases_are_unknown(reader, sheet, removed, caplog):
    required, optional = SHEET_HEADERS[sheet]
    records = reader.read_sheet_rows({sheet: [(*required, *optional, removed)]}, sheet)
    assert records == []
    assert f"unknown=['{removed}']" in caplog.text


@pytest.mark.parametrize("kind", ["Argument", "Value"])
def test_removed_access_kind_values_are_rejected(reader, kind, caplog):
    _, ports = reader.read_workbook({
        "Components": [("SW-C", "Component Path"), ("A", "/Pkg/A")],
        "R-Ports": [
            ("SW-C", "Port Name", "Port Interface Ref", "Access Kind", "Create RTE Artifact"),
            ("A", "r_Test", "/Pkg/If", kind, "Y"),
        ],
    })
    assert ports == []
    assert "Access Kind must be one of" in caplog.text


def test_disabled_rte_fields_are_not_validated(reader, caplog):
    _, ports = reader.read_workbook({
        "Components": [("SW-C", "Component Path"), ("A", "/Pkg/A")],
        "P-Ports": [
            (
                "SW-C", "Port Name", "Port Interface Ref", "Create RTE Artifact", "Runnable Name",
                "Runnable Symbol", "Access Kind", "Minimum Start Interval",
                "Can Be Invoked Concurrently",
            ),
            ("A", "p_Test", "/Pkg/If", "N", "bad/name", "not a symbol", "Unknown", "abc", "maybe"),
        ],
    })
    assert len(ports) == 1
    assert not ports[0].create_rte_artifact
    assert ports[0].provided_fields >= {
        "Runnable Name", "Runnable Symbol", "Access Kind",
        "Minimum Start Interval", "Can Be Invoked Concurrently",
    }
    assert not [record for record in caplog.records if record.levelname == "ERROR"]


def test_enabled_rte_fields_are_still_validated(reader, caplog):
    _, ports = reader.read_workbook({
        "Components": [("SW-C", "Component Path"), ("A", "/Pkg/A")],
        "P-Ports": [
            ("SW-C", "Port Name", "Port Interface Ref", "Create RTE Artifact",
             "Runnable Name", "Access Kind"),
            ("A", "p_Test", "/Pkg/If", "Y", "Run", "Unknown"),
        ],
    })
    assert ports == []
    assert "Access Kind must be one of" in caplog.text


def test_explicit_access_runnable_name_is_always_validated(reader, caplog):
    records = reader.read_sheet_rows({
        "Data Accesses": [
            ("SW-C", "Runnable Name", "Port Name", "Data Element Name", "Access Kind"),
            ("A", "bad/name", "p_Test", "Value", "Send"),
        ],
    }, "Data Accesses")
    assert records == []
    assert "Runnable Name" in caplog.text
    assert "AUTOSAR SHORT-NAME" in caplog.text


@pytest.mark.parametrize("queue,expected", [("", False), ("0", False), ("1", True)])
def test_queue_length_generation_boundary(reader, queue, expected, caplog):
    _, ports = reader.read_workbook({
        "Components": [("SW-C", "Component Path"), ("A", "/Pkg/A")],
        "R-Ports": [
            ("SW-C", "Port Name", "Port Interface Ref", "Queue Length"),
            ("A", "r_Test", "/Pkg/If", queue),
        ],
    })
    assert ports[0].needs_com_specs == expected
    assert not caplog.records


@pytest.mark.parametrize("queue", ["-1", "0.5", "nan"])
def test_invalid_queue_length_still_fails(reader, queue, caplog):
    _, ports = reader.read_workbook({
        "Components": [("SW-C", "Component Path"), ("A", "/Pkg/A")],
        "R-Ports": [
            ("SW-C", "Port Name", "Port Interface Ref", "Queue Length"),
            ("A", "r_Test", "/Pkg/If", queue),
        ],
    })
    assert not ports
    assert "Queue Length must be a non-negative integer" in caplog.text


def test_blank_header_does_not_shift_optional_flags(reader, caplog):
    _, ports = reader.read_workbook({
        "Components": [("SW-C", "Component Path"), ("A", "/Pkg/A")],
        "P-Ports": [
            ("SW-C", "Port Name", "Port Interface Ref", None, "Create RTE Artifact"),
            ("A", "p_Test", "/Pkg/If", None, "Y"),
        ],
    })
    assert ports == []
    assert "Skipped worksheet P-Ports" in caplog.text


def test_data_beyond_last_header_is_rejected(reader, caplog):
    records = reader.read_sheet_rows({
        "Components": [("SW-C", "Component Path", None), ("A", "/Pkg/A", "unexpected")],
    }, "Components")
    assert records == []
    assert "data under a blank header" in caplog.text


@pytest.mark.parametrize("name", ["bad/name", "bad name", "1port", "_port", "P" * 129])
def test_invalid_new_port_names_are_rejected(reader, caplog, name):
    _, ports = reader.read_workbook({
        "Components": [("SW-C", "Component Path"), ("A", "/Pkg/A")],
        "P-Ports": [("SW-C", "Port Name", "Port Interface Ref"), ("A", name, "/Pkg/If")],
    })
    assert ports == []
    assert "AUTOSAR SHORT-NAME" in caplog.text


@pytest.mark.parametrize("path", ["/Pkg//If", "/Pkg/If/", "/Pkg/bad-name", "Pkg/If"])
def test_malformed_autosar_references_are_rejected(reader, caplog, path):
    _, ports = reader.read_workbook({
        "Components": [("SW-C", "Component Path"), ("A", "/Pkg/A")],
        "P-Ports": [("SW-C", "Port Name", "Port Interface Ref"), ("A", "port", path)],
    })
    assert ports == []
    assert "Port Interface Ref" in caplog.text
