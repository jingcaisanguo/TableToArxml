"""白表模式端到端：接口不一致的三种策略、接口映射、类型校验和连接器命名。"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from helpers import NS, SYSTEM, run_tool
from system_fixtures import write_config, write_model, write_white_table

COMPONENTS = [["A", "A", "/Pkg/A", "Reference"], ["B", "B", "/Pkg/B", "Reference"]]
MAPPING = {"providerInterface": "/Pkg/IfA", "requesterInterface": "/Pkg/IfB", "dataElements": {"a": "b"}}


def run_white(tmp_path, assembly: list[str], **config):
    write_model(tmp_path)
    write_white_table(tmp_path, {
        "Components": COMPONENTS,
        "AssemblySwConnection": [assembly],
        "DelegationSwConnection": [["A", "pOut", "pTop"]],
    })
    return run_tool(SYSTEM, "--config", write_config(tmp_path, **config))


def short_names(root: ET.Element, tag: str) -> list[str]:
    return [element.findtext("ar:SHORT-NAME", namespaces=NS) for element in root.iter(f"{{{NS['ar']}}}{tag}")]


def test_interface_mapping_and_connector_names(tmp_path):
    result = run_white(tmp_path, ["A", "pOut", "B", "rIn"], interfaceMismatch="map", interfaceMappings=[MAPPING])
    assert result.returncode == 0, result.stderr
    root = ET.parse(tmp_path / "out" / "TSystem.arxml").getroot()
    assert short_names(root, "ASSEMBLY-SW-CONNECTOR") == ["ac_A_pOut_B_rIn"]
    assert short_names(root, "DELEGATION-SW-CONNECTOR") == ["dlc_pTop_A_pOut"]
    mappings = list(root.iter(f"{{{NS['ar']}}}VARIABLE-AND-PARAMETER-INTERFACE-MAPPING"))
    assert len(mappings) == 1
    assert [element.text for element in mappings[0].iter() if element.tag.endswith("DATA-PROTOTYPE-REF")] == [
        "/Pkg/IfA/a", "/Pkg/IfB/b"]
    mapping_ref = next(root.iter(f"{{{NS['ar']}}}MAPPING-REF"))
    assert mapping_ref.text.startswith("/Pkg/Top_InterfaceMappings/im_")


def test_interface_mismatch_error_blocks_output(tmp_path):
    result = run_white(tmp_path, ["A", "pOut", "B", "rIn"], interfaceMismatch="error")
    assert result.returncode == 1
    assert "interface mismatch" in result.stderr
    assert not (tmp_path / "out").exists()


def test_interface_mismatch_skip_drops_connector(tmp_path):
    result = run_white(tmp_path, ["A", "pOut", "B", "rIn"], interfaceMismatch="skip")
    assert result.returncode == 0, result.stderr
    assert "Skip AssemblySwConnection row 2" in result.stderr
    root = ET.parse(tmp_path / "out" / "TSystem.arxml").getroot()
    assert short_names(root, "ASSEMBLY-SW-CONNECTOR") == []


@pytest.mark.parametrize("elements, message", [
    ({"c": "b"}, "Interface final data type mismatch"),
    ({"x": "b"}, "must cover both interfaces exactly once"),
])
def test_interface_mapping_is_validated(tmp_path, elements, message):
    mapping = {"providerInterface": "/Pkg/IfC", "requesterInterface": "/Pkg/IfB", "dataElements": elements}
    result = run_white(tmp_path, ["A", "pWide", "B", "rIn"], interfaceMismatch="map", interfaceMappings=[mapping])
    assert result.returncode == 1
    assert message in result.stderr
    assert not (tmp_path / "out").exists()
