"""工作簿兼容性和条件必填规则；不读写实际 ARXML 模型。"""

from importlib import import_module
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def reader(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "table_to_swc_arxml"))
    return import_module("table_to_swc_arxml.workbook")


@pytest.mark.parametrize("header", ["Implementation Data Type Ref", "Base Type Ref", "base type ref"])
def test_enum_type_header_compatibility(reader, tmp_path, header, caplog):
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


def test_duplicate_sheet_aliases_still_fail(reader, caplog):
    specs = reader.read_data_accesses({"Data Accesses": [], "Data-Accesses": []}, {})
    assert specs == []
    assert "Multiple worksheets match Data Accesses" in caplog.text


def test_duplicate_column_aliases_still_fail(reader, caplog):
    reader.read_workbook({"Components": [
        ("SW-C", "SWC", "Component Path"), ("A", "A", "/Pkg/A"),
    ]})
    assert "duplicate=['SW-C']" in caplog.text


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
