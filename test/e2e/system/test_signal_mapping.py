"""Signal mapping validates references without requiring communication topology."""

import xml.etree.ElementTree as ET

import pytest

from helpers import NS, SYSTEM, run_tool
from system_fixtures import write_config, write_model, write_white_table


@pytest.mark.parametrize("scenario", [
    "signal", "member", "group", "missing-signal", "missing-element", "wrong-port",
    "missing-ecu", "group-as-signal",
])
def test_signal_mapping_shallow_validation(tmp_path, scenario):
    write_model(tmp_path)
    path = tmp_path / "model.arxml"
    tree = ET.parse(path)
    elements = tree.getroot().find("ar:AR-PACKAGES/ar:AR-PACKAGE/ar:ELEMENTS", NS)
    elements.extend(ET.fromstring(f"""
        <ELEMENTS xmlns="{NS['ar']}">
          <ECU-INSTANCE><SHORT-NAME>Ecu</SHORT-NAME></ECU-INSTANCE>
          <SYSTEM-SIGNAL><SHORT-NAME>Signal</SHORT-NAME></SYSTEM-SIGNAL>
          <SYSTEM-SIGNAL-GROUP>
            <SHORT-NAME>Group</SHORT-NAME>
            <SYSTEM-SIGNAL-REFS>
              <SYSTEM-SIGNAL-REF DEST="SYSTEM-SIGNAL">/Pkg/Signal</SYSTEM-SIGNAL-REF>
            </SYSTEM-SIGNAL-REFS>
          </SYSTEM-SIGNAL-GROUP>
        </ELEMENTS>"""))
    if scenario == "missing-ecu":
        elements.remove(next(item for item in elements
                             if item.findtext("ar:SHORT-NAME", namespaces=NS) == "Ecu"))
    if scenario != "member":
        group = next(item for item in elements
                     if item.findtext("ar:SHORT-NAME", namespaces=NS) == "Group")
        group.find("ar:SYSTEM-SIGNAL-REFS", NS).clear()
    ET.register_namespace("", NS["ar"])
    tree.write(path, encoding="utf-8", xml_declaration=True)
    signal = "/Pkg/Missing" if scenario == "missing-signal" else "/Pkg/Signal"
    if scenario == "group-as-signal":
        signal = "/Pkg/Group"
    element = "missing" if scenario == "missing-element" else "a"
    if scenario == "group":
        rows = {"SenderReceiverToSignalGroup": [["/Pkg/Group", "A", "pOut", element]]}
    else:
        direction = "IN" if scenario == "wrong-port" else "OUT"
        rows = {"SenderReceiverToSignal": [[signal, direction, "A", "pOut", element]]}
    write_white_table(tmp_path, {
        "Components": [["A", "A", "/Pkg/A", "Reference"]], **rows,
    })
    result = run_tool(SYSTEM, "--config", write_config(tmp_path, generateSystem=True))
    if scenario in ("signal", "member", "group"):
        assert result.returncode == 0, result.stderr
        root = ET.parse(tmp_path / "out" / "TSystem.arxml").getroot()
        tag = "SIGNAL-GROUP-REF" if scenario == "group" else "SYSTEM-SIGNAL-REF"
        reference = next(root.iter(f"{{{NS['ar']}}}{tag}"))
        assert reference.text == ("/Pkg/Group" if scenario == "group" else "/Pkg/Signal")
        if scenario == "member":
            assert "WARNING" in result.stderr
            assert "mapping the member individually" in result.stderr
    else:
        assert result.returncode == 1
        assert not (tmp_path / "out").exists()
