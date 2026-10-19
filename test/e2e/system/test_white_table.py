"""绿表/白表接口映射端到端；浅层映射校验、白表接口策略和连接器命名。"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET

import pytest
from openpyxl import Workbook

from helpers import NS, SYSTEM, run_tool
from system_fixtures import write_config, write_model, write_white_table

COMPONENTS = [["A", "A", "/Pkg/A", "Reference"], ["B", "B", "/Pkg/B", "Reference"]]
MAPPING = {"providerInterface": "/Pkg/IfA", "requesterInterface": "/Pkg/IfB", "dataElements": {"a": "b"}}


def prepare_mapping_input(tmp_path, mode, existing=False, reverse=False, same_names=True):
    write_model(tmp_path)
    path = tmp_path / "model.arxml"
    root = ET.parse(path).getroot()
    elements = root.find("ar:AR-PACKAGES/ar:AR-PACKAGE/ar:ELEMENTS", NS)
    if not existing and same_names:
        interface = next(item for item in elements
                         if item.findtext("ar:SHORT-NAME", namespaces=NS) == "IfB")
        interface.find("ar:DATA-ELEMENTS/ar:VARIABLE-DATA-PROTOTYPE/ar:SHORT-NAME", NS).text = "a"
    elif existing:
        first, second = ("/Pkg/IfA/a", "/Pkg/IfB/b")
        if reverse:
            first, second = second, first
        elements.append(ET.fromstring(f"""
            <PORT-INTERFACE-MAPPING-SET xmlns="{NS['ar']}">
              <SHORT-NAME>ExistingMappings</SHORT-NAME>
              <PORT-INTERFACE-MAPPINGS>
                <VARIABLE-AND-PARAMETER-INTERFACE-MAPPING>
                  <SHORT-NAME>ExistingMap</SHORT-NAME>
                  <DATA-MAPPINGS><DATA-PROTOTYPE-MAPPING>
                    <FIRST-DATA-PROTOTYPE-REF DEST="VARIABLE-DATA-PROTOTYPE">{first}</FIRST-DATA-PROTOTYPE-REF>
                    <SECOND-DATA-PROTOTYPE-REF DEST="VARIABLE-DATA-PROTOTYPE">{second}</SECOND-DATA-PROTOTYPE-REF>
                  </DATA-PROTOTYPE-MAPPING></DATA-MAPPINGS>
                </VARIABLE-AND-PARAMETER-INTERFACE-MAPPING>
              </PORT-INTERFACE-MAPPINGS>
            </PORT-INTERFACE-MAPPING-SET>"""))
    config = {"interfaceMismatch": "map"}
    if mode == "white":
        write_white_table(tmp_path, {
            "Components": COMPONENTS, "AssemblySwConnection": [["A", "pOut", "B", "rIn"]],
        })
    else:
        component = next(item for item in elements
                         if item.findtext("ar:SHORT-NAME", namespaces=NS) == "A")
        component.append(ET.fromstring(f"""
            <INTERNAL-BEHAVIORS xmlns="{NS['ar']}">
              <SWC-INTERNAL-BEHAVIOR><SHORT-NAME>IB</SHORT-NAME></SWC-INTERNAL-BEHAVIOR>
            </INTERNAL-BEHAVIORS>"""))
        elements.append(ET.fromstring(f"""
            <SWC-IMPLEMENTATION xmlns="{NS['ar']}">
              <SHORT-NAME>AImpl</SHORT-NAME>
              <BEHAVIOR-REF DEST="SWC-INTERNAL-BEHAVIOR">/Pkg/A/IB</BEHAVIOR-REF>
            </SWC-IMPLEMENTATION>"""))
        workbook = Workbook()
        workbook.active.title = "INPUT"
        workbook.create_sheet("OUTPUT")
        for sheet in workbook:
            sheet.append(["", "Port", "Module", "", "", "", "", "DataElement", "Component", "Port", "Remark"])
        workbook["OUTPUT"].append(["", "pOut", "SWC", "", "", "", "", "", "B", "rIn", ""])
        workbook.save(tmp_path / "A.xlsx")
        workbook.close()
        config.update(greenTables=["A.xlsx"], componentAliases={"A": "/Pkg/Top/A"}, general={
            "Composition": "/Pkg/Top", "System": "/Pkg/Sys",
            "RootSwCompositionPrototype": "/Pkg/Sys/RootProto", "EcuInstance": "/Pkg/Ecu",
        })
    ET.register_namespace("", NS["ar"])
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    config_path = write_config(tmp_path, **config)
    if mode == "green":
        contents = json.loads(config_path.read_text(encoding="utf-8"))
        del contents["whiteTable"]
        config_path.write_text(json.dumps(contents), encoding="utf-8")
    return config_path


@pytest.mark.parametrize("mode", ["green", "white"])
@pytest.mark.parametrize("mapping", [
    "automatic", "automatic-single", "configured-single",
    "existing", "existing-reversed", "existing-configured",
])
def test_different_interfaces_connect_with_mapping(tmp_path, mode, mapping):
    existing = mapping.startswith("existing")
    different_names = existing or mapping.endswith("-single")
    config = prepare_mapping_input(
        tmp_path, mode, existing, mapping == "existing-reversed", same_names=not different_names)
    if mapping in ("existing-configured", "configured-single"):
        contents = json.loads(config.read_text(encoding="utf-8"))
        contents["interfaceMappings"] = [MAPPING]
        config.write_text(json.dumps(contents), encoding="utf-8")
    result = run_tool(SYSTEM, "--config", config)
    assert result.returncode == 0, result.stderr
    root = ET.parse(tmp_path / "out" / "TSystem.arxml").getroot()
    assert short_names(root, "ASSEMBLY-SW-CONNECTOR") == ["ac_A_pOut_B_rIn"]
    connector = next(root.iter(f"{{{NS['ar']}}}ASSEMBLY-SW-CONNECTOR"))
    assert connector.findtext("ar:PROVIDER-IREF/ar:TARGET-P-PORT-REF", namespaces=NS) == "/Pkg/A/pOut"
    assert connector.findtext("ar:REQUESTER-IREF/ar:TARGET-R-PORT-REF", namespaces=NS) == "/Pkg/B/rIn"
    reference = connector.find("ar:MAPPING-REF", NS)
    assert reference.attrib["DEST"] == "VARIABLE-AND-PARAMETER-INTERFACE-MAPPING"
    mappings = list(root.iter(f"{{{NS['ar']}}}VARIABLE-AND-PARAMETER-INTERFACE-MAPPING"))
    if existing:
        assert reference.text == "/Pkg/ExistingMappings/ExistingMap"
        assert mappings == []
        assert "Reuse interface mapping" in result.stdout
    else:
        assert len(mappings) == 1
        assert reference.text == "/Pkg/Top_InterfaceMappings/" + mappings[0].findtext("ar:SHORT-NAME", namespaces=NS)
        assert [item.text for item in mappings[0].iter() if item.tag.endswith("DATA-PROTOTYPE-REF")] == [
            "/Pkg/IfA/a", "/Pkg/IfB/b" if different_names else "/Pkg/IfB/a"]
    assert (tmp_path / "out" / "TWhiteTable.xlsx").exists() == (mode == "green")


@pytest.mark.parametrize("mode", ["green", "white"])
@pytest.mark.parametrize("policy", ["error", "skip", "map"])
@pytest.mark.parametrize("reverse", [False, True])
def test_existing_mapping_is_reused_before_mismatch_policy(tmp_path, mode, policy, reverse):
    config = prepare_mapping_input(tmp_path, mode, existing=True, reverse=reverse)
    contents = json.loads(config.read_text(encoding="utf-8"))
    contents["interfaceMismatch"] = policy
    config.write_text(json.dumps(contents), encoding="utf-8")
    result = run_tool(SYSTEM, "--config", config)
    assert result.returncode == 0, result.stderr
    root = ET.parse(tmp_path / "out" / "TSystem.arxml").getroot()
    assert short_names(root, "ASSEMBLY-SW-CONNECTOR") == ["ac_A_pOut_B_rIn"]
    assert next(root.iter(f"{{{NS['ar']}}}MAPPING-REF")).text == "/Pkg/ExistingMappings/ExistingMap"
    assert short_names(root, "VARIABLE-AND-PARAMETER-INTERFACE-MAPPING") == []
    assert "Skip AssemblySwConnection" not in result.stderr


@pytest.mark.parametrize("mode", ["green", "white"])
@pytest.mark.parametrize("policy", ["error", "skip"])
def test_missing_mapping_uses_mismatch_policy(tmp_path, mode, policy):
    config = prepare_mapping_input(tmp_path, mode, same_names=False)
    contents = json.loads(config.read_text(encoding="utf-8"))
    contents["interfaceMismatch"] = policy
    config.write_text(json.dumps(contents), encoding="utf-8")
    result = run_tool(SYSTEM, "--config", config)
    if policy == "error":
        assert result.returncode == 1
        assert "interface mismatch" in result.stderr
        assert not (tmp_path / "out").exists()
    else:
        assert result.returncode == 0, result.stderr
        assert "Skip AssemblySwConnection" in result.stderr
        root = ET.parse(tmp_path / "out" / "TSystem.arxml").getroot()
        assert short_names(root, "ASSEMBLY-SW-CONNECTOR") == []
        assert short_names(root, "VARIABLE-AND-PARAMETER-INTERFACE-MAPPING") == []


@pytest.mark.parametrize("mode", ["green", "white"])
@pytest.mark.parametrize("scenario", [
    "partial", "explicit-swap", "existing-partial", "missing-source",
    "missing-target", "duplicate-target", "remaining-different", "occupied-target",
])
def test_explicit_mapping_pairs_are_completed_by_name(tmp_path, mode, scenario):
    existing = scenario == "existing-partial"
    config = prepare_mapping_input(tmp_path, mode, existing=existing, same_names=False)
    path = tmp_path / "model.arxml"
    tree = ET.parse(path)
    root = tree.getroot()
    for interface in root.iter(f"{{{NS['ar']}}}SENDER-RECEIVER-INTERFACE"):
        if interface.findtext("ar:SHORT-NAME", namespaces=NS) not in ("IfA", "IfB"):
            continue
        elements = interface.find("ar:DATA-ELEMENTS", NS)
        element = ET.fromstring(ET.tostring(elements[0]))
        element.find("ar:SHORT-NAME", NS).text = "extra"
        elements.append(element)
    pairs = {"a": "b"}
    if scenario == "explicit-swap":
        pairs = {"a": "extra", "extra": "b"}
    elif scenario == "missing-source":
        pairs = {"missing": "b"}
    elif scenario == "missing-target":
        pairs = {"a": "missing"}
    elif scenario == "duplicate-target":
        pairs = {"a": "b", "extra": "b"}
    elif scenario == "remaining-different":
        pairs = {"extra": "extra"}
    elif scenario == "occupied-target":
        pairs = {"a": "extra"}
    if existing:
        mappings = next(root.iter(f"{{{NS['ar']}}}DATA-MAPPINGS"))
        item = ET.fromstring(ET.tostring(mappings[0]))
        item.find("ar:FIRST-DATA-PROTOTYPE-REF", NS).text = "/Pkg/IfA/extra"
        item.find("ar:SECOND-DATA-PROTOTYPE-REF", NS).text = "/Pkg/IfB/extra"
        mappings.append(item)
    tree.write(path, encoding="utf-8", xml_declaration=True)
    contents = json.loads(config.read_text(encoding="utf-8"))
    contents["interfaceMappings"] = [{**MAPPING, "dataElements": pairs}]
    config.write_text(json.dumps(contents), encoding="utf-8")
    result = run_tool(SYSTEM, "--config", config)
    if scenario not in ("partial", "explicit-swap", "existing-partial"):
        assert result.returncode == 1
        assert "must cover both interfaces exactly once" in result.stderr
        assert not (tmp_path / "out").exists()
        return
    assert result.returncode == 0, result.stderr
    output = ET.parse(tmp_path / "out" / "TSystem.arxml").getroot()
    assert short_names(output, "ASSEMBLY-SW-CONNECTOR") == ["ac_A_pOut_B_rIn"]
    mapping_ref = next(output.iter(f"{{{NS['ar']}}}MAPPING-REF"))
    if existing:
        assert mapping_ref.text == "/Pkg/ExistingMappings/ExistingMap"
        assert short_names(output, "VARIABLE-AND-PARAMETER-INTERFACE-MAPPING") == []
    else:
        expected = pairs if scenario == "explicit-swap" else {"a": "b", "extra": "extra"}
        assert [item.text for item in output.iter() if item.tag.endswith("DATA-PROTOTYPE-REF")] == [
            reference
            for source, target in sorted(expected.items())
            for reference in (f"/Pkg/IfA/{source}", f"/Pkg/IfB/{target}")
        ]


@pytest.mark.parametrize("mode", ["green", "white"])
def test_single_element_mapping_does_not_override_explicit_pairs(tmp_path, mode):
    config = prepare_mapping_input(tmp_path, mode, same_names=False)
    contents = json.loads(config.read_text(encoding="utf-8"))
    contents["interfaceMappings"] = [{**MAPPING, "dataElements": {"missing": "b"}}]
    config.write_text(json.dumps(contents), encoding="utf-8")
    result = run_tool(SYSTEM, "--config", config)
    assert result.returncode == 1
    assert "must cover both interfaces exactly once" in result.stderr
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("mode", ["green", "white"])
@pytest.mark.parametrize("shape", ["empty", "one-to-many", "different-names", "same-names"])
def test_automatic_mapping_requires_unique_complete_pairs(tmp_path, mode, shape):
    config = prepare_mapping_input(tmp_path, mode, same_names=shape == "same-names")
    path = tmp_path / "model.arxml"
    tree = ET.parse(path)
    interfaces = {
        item.findtext("ar:SHORT-NAME", namespaces=NS): item
        for item in tree.getroot().iter(f"{{{NS['ar']}}}SENDER-RECEIVER-INTERFACE")
    }
    if shape == "empty":
        interfaces["IfA"].find("ar:DATA-ELEMENTS", NS).clear()
    else:
        for name in (["IfB"] if shape == "one-to-many" else ["IfA", "IfB"]):
            elements = interfaces[name].find("ar:DATA-ELEMENTS", NS)
            element = ET.fromstring(ET.tostring(elements[0]))
            element.find("ar:SHORT-NAME", NS).text = "extra"
            elements.append(element)
    tree.write(path, encoding="utf-8", xml_declaration=True)
    result = run_tool(SYSTEM, "--config", config)
    if shape == "same-names":
        assert result.returncode == 0, result.stderr
        output = ET.parse(tmp_path / "out" / "TSystem.arxml").getroot()
        assert short_names(output, "ASSEMBLY-SW-CONNECTOR") == ["ac_A_pOut_B_rIn"]
        assert [item.text for item in output.iter() if item.tag.endswith("DATA-PROTOTYPE-REF")] == [
            "/Pkg/IfA/a", "/Pkg/IfB/a", "/Pkg/IfA/extra", "/Pkg/IfB/extra"]
    else:
        assert result.returncode == 1
        assert "Cannot infer interface mapping" in result.stderr
        assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("mode", ["green", "white"])
@pytest.mark.parametrize("policy", ["error", "skip", "map"])
@pytest.mark.parametrize("invalid, message", [
    ("incomplete", "must cover both interfaces exactly once"),
    ("ambiguous", "Ambiguous existing interface mappings"),
])
def test_invalid_existing_mapping_blocks_output(tmp_path, mode, policy, invalid, message):
    config = prepare_mapping_input(tmp_path, mode, existing=True)
    contents = json.loads(config.read_text(encoding="utf-8"))
    contents["interfaceMismatch"] = policy
    config.write_text(json.dumps(contents), encoding="utf-8")
    path = tmp_path / "model.arxml"
    tree = ET.parse(path)
    root = tree.getroot()
    if invalid == "type":
        interface = next(item for item in root.iter(f"{{{NS['ar']}}}SENDER-RECEIVER-INTERFACE")
                         if item.findtext("ar:SHORT-NAME", namespaces=NS) == "IfB")
        interface.find("ar:DATA-ELEMENTS/ar:VARIABLE-DATA-PROTOTYPE/ar:TYPE-TREF", NS).text = "/Pkg/uint16"
    elif invalid == "incomplete":
        reference = next(root.iter(f"{{{NS['ar']}}}SECOND-DATA-PROTOTYPE-REF"))
        reference.text = "/Pkg/IfB/missing"
    else:
        mappings = next(root.iter(f"{{{NS['ar']}}}PORT-INTERFACE-MAPPINGS"))
        duplicate = ET.fromstring(ET.tostring(mappings[0]))
        duplicate.find("ar:SHORT-NAME", NS).text = "OtherMap"
        mappings.append(duplicate)
    tree.write(path, encoding="utf-8", xml_declaration=True)
    result = run_tool(SYSTEM, "--config", config)
    assert result.returncode == 1
    assert message in result.stderr
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("mode", ["green", "white"])
@pytest.mark.parametrize("existing", [False, True])
def test_mapping_does_not_validate_data_types(tmp_path, mode, existing):
    config = prepare_mapping_input(tmp_path, mode, existing=existing)
    path = tmp_path / "model.arxml"
    tree = ET.parse(path)
    interface = next(item for item in tree.getroot().iter(f"{{{NS['ar']}}}SENDER-RECEIVER-INTERFACE")
                     if item.findtext("ar:SHORT-NAME", namespaces=NS) == "IfB")
    interface.find("ar:DATA-ELEMENTS/ar:VARIABLE-DATA-PROTOTYPE/ar:TYPE-TREF", NS).text = "/Pkg/uint16"
    tree.write(path, encoding="utf-8", xml_declaration=True)
    result = run_tool(SYSTEM, "--config", config)
    assert result.returncode == 0, result.stderr
    root = ET.parse(tmp_path / "out" / "TSystem.arxml").getroot()
    assert short_names(root, "ASSEMBLY-SW-CONNECTOR") == ["ac_A_pOut_B_rIn"]


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


def test_interface_mapping_requires_complete_element_pairs(tmp_path):
    elements = {"x": "b"}
    message = "must cover both interfaces exactly once"
    mapping = {"providerInterface": "/Pkg/IfC", "requesterInterface": "/Pkg/IfB", "dataElements": elements}
    result = run_white(tmp_path, ["A", "pWide", "B", "rIn"], interfaceMismatch="map", interfaceMappings=[mapping])
    assert result.returncode == 1
    assert message in result.stderr
    assert not (tmp_path / "out").exists()


def test_same_interface_does_not_resolve_data_element_types(tmp_path):
    write_model(tmp_path)
    path = tmp_path / "model.arxml"
    text = path.read_text(encoding="utf-8").replace(">/Pkg/IfB<", ">/Pkg/IfA<")
    path.write_text(text.replace(">/Pkg/uint8<", ">/Pkg/MissingType<"), encoding="utf-8")
    write_white_table(tmp_path, {
        "Components": COMPONENTS, "AssemblySwConnection": [["A", "pOut", "B", "rIn"]],
    })
    result = run_tool(SYSTEM, "--config", write_config(tmp_path))
    assert result.returncode == 0, result.stderr
    root = ET.parse(tmp_path / "out" / "TSystem.arxml").getroot()
    assert short_names(root, "ASSEMBLY-SW-CONNECTOR") == ["ac_A_pOut_B_rIn"]


def test_invalid_outer_port_name_blocks_output(tmp_path):
    write_model(tmp_path)
    write_white_table(tmp_path, {
        "Components": COMPONENTS, "DelegationSwConnection": [["A", "pOut", "bad-name"]],
    })
    result = run_tool(SYSTEM, "--config", write_config(tmp_path))
    assert result.returncode == 1
    assert "Outer Port must be an AUTOSAR SHORT-NAME" in result.stderr
    assert not (tmp_path / "out").exists()
