"""Generator compatibility: signal group types, implementation choice and reused connectors without mappings."""

import xml.etree.ElementTree as ET

import pytest

from helpers import NS, SYSTEM, run_tool
from system_fixtures import write_config, write_model, write_white_table

AR = NS["ar"]
COMPONENTS = [["A", "A", "/Pkg/A", "Reference"], ["B", "B", "/Pkg/B", "Reference"]]


def extend_model(directory, elements="", top=""):
    write_model(directory)
    path = directory / "model.arxml"
    tree = ET.parse(path)
    root = tree.getroot()
    package = root.find("ar:AR-PACKAGES/ar:AR-PACKAGE/ar:ELEMENTS", NS)
    package.extend(ET.fromstring(f"""<ELEMENTS xmlns="{AR}">
        <ECU-INSTANCE><SHORT-NAME>Ecu</SHORT-NAME></ECU-INSTANCE>
        <SYSTEM-SIGNAL><SHORT-NAME>Signal</SHORT-NAME></SYSTEM-SIGNAL>{elements}</ELEMENTS>"""))
    if top:
        composition = next(item for item in package.findall("ar:COMPOSITION-SW-COMPONENT-TYPE", NS)
                           if item.findtext("ar:SHORT-NAME", namespaces=NS) == "Top")
        composition.extend(ET.fromstring(f'<W xmlns="{AR}">{top}</W>'))
    return tree, path


def save(tree, path):
    ET.register_namespace("", AR)
    tree.write(path, encoding="utf-8", xml_declaration=True)


def test_signal_group_sheet_rejects_objects_that_are_not_system_signal_groups(tmp_path):
    save(*extend_model(tmp_path))
    write_white_table(tmp_path, {
        "Components": [["A", "A", "/Pkg/A", "Reference"]],
        "SenderReceiverToSignalGroup": [["/Pkg/Signal", "A", "pOut", "a"]],
    })
    result = run_tool(SYSTEM, "--config", write_config(tmp_path, generateSystem=True))
    assert result.returncode == 1
    assert "'/Pkg/Signal' is a SYSTEM-SIGNAL, not a SYSTEM-SIGNAL-GROUP" in result.stderr
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("order", [("ZImpl", "AImpl"), ("AImpl", "ZImpl")])
def test_implementation_choice_does_not_depend_on_file_order(tmp_path, order):
    implementations = "".join(
        f"""<SWC-IMPLEMENTATION><SHORT-NAME>{name}</SHORT-NAME>
          <BEHAVIOR-REF DEST="SWC-INTERNAL-BEHAVIOR">/Pkg/A/Behavior</BEHAVIOR-REF></SWC-IMPLEMENTATION>"""
        for name in order)
    tree, path = extend_model(tmp_path, implementations)
    component = next(item for item in tree.getroot().iter(f"{{{AR}}}APPLICATION-SW-COMPONENT-TYPE")
                     if item.findtext("ar:SHORT-NAME", namespaces=NS) == "A")
    component.append(ET.fromstring(f"""<INTERNAL-BEHAVIORS xmlns="{AR}"><SWC-INTERNAL-BEHAVIOR>
        <SHORT-NAME>Behavior</SHORT-NAME></SWC-INTERNAL-BEHAVIOR></INTERNAL-BEHAVIORS>"""))
    save(tree, path)
    write_white_table(tmp_path, {"Components": [["New", "New", "/Pkg/A", "Add"]]})
    result = run_tool(SYSTEM, "--config", write_config(tmp_path, generateSystem=True))
    assert result.returncode == 0, result.stderr
    assert "Multiple SWC-IMPLEMENTATIONs reference /Pkg/A/Behavior" in result.stderr
    root = ET.parse(tmp_path / "out" / "TSystem.arxml").getroot()
    assert root.findtext(".//ar:COMPONENT-IMPLEMENTATION-REF", namespaces=NS) == "/Pkg/AImpl"


@pytest.mark.parametrize("name", ["ac_A_pOut_B_rIn", "Existing"])
def test_reused_connector_without_mapping_leaves_no_orphan_interface_mapping(tmp_path, name):
    save(*extend_model(tmp_path, top=f"""<CONNECTORS><ASSEMBLY-SW-CONNECTOR><SHORT-NAME>{name}</SHORT-NAME>
        <PROVIDER-IREF><CONTEXT-COMPONENT-REF DEST="SW-COMPONENT-PROTOTYPE">/Pkg/Top/A</CONTEXT-COMPONENT-REF>
          <TARGET-P-PORT-REF DEST="P-PORT-PROTOTYPE">/Pkg/A/pOut</TARGET-P-PORT-REF></PROVIDER-IREF>
        <REQUESTER-IREF><CONTEXT-COMPONENT-REF DEST="SW-COMPONENT-PROTOTYPE">/Pkg/Top/B</CONTEXT-COMPONENT-REF>
          <TARGET-R-PORT-REF DEST="R-PORT-PROTOTYPE">/Pkg/B/rIn</TARGET-R-PORT-REF></REQUESTER-IREF>
        </ASSEMBLY-SW-CONNECTOR></CONNECTORS>"""))
    write_white_table(tmp_path, {"Components": COMPONENTS, "AssemblySwConnection": [["A", "pOut", "B", "rIn"]]})
    result = run_tool(SYSTEM, "--config", write_config(tmp_path, interfaceMismatch="map"))
    assert result.returncode == 0, result.stderr
    root = ET.parse(tmp_path / "out" / "TSystem.arxml").getroot()
    assert not root.findall(".//ar:ASSEMBLY-SW-CONNECTOR", NS)
    assert not root.findall(".//ar:VARIABLE-AND-PARAMETER-INTERFACE-MAPPING", NS)
    assert not root.findall(".//ar:PORT-INTERFACE-MAPPING-SET", NS)
    assert f"existing connector {name} is reused but references interface mapping (none)" in result.stderr
