"""System output modes preserve only selected base content without modifying the source."""

import shutil
import json
import xml.etree.ElementTree as ET

import pytest

from helpers import NS, ROOT, SYSTEM, run_tool
from system_fixtures import write_config, write_model, write_white_table

COMPONENTS = [["A", "A", "/Pkg/A", "Reference"], ["B", "B", "/Pkg/B", "Reference"]]
ASSEMBLY = ["A", "pOut", "B", "rIn"]
SIGNAL = ["/Pkg/Signal", "OUT", "A", "pOut", "a"]


def prepare_base(directory, mapped=False):
    list_file = write_model(directory)
    model = directory / "model.arxml"
    text = model.read_text(encoding="utf-8")
    if not mapped:
        text = text.replace(">/Pkg/IfB<", ">/Pkg/IfA<")
    tree = ET.ElementTree(ET.fromstring(text))
    elements = tree.getroot().find("ar:AR-PACKAGES/ar:AR-PACKAGE/ar:ELEMENTS", NS)
    elements.extend(ET.fromstring(f"""
        <ELEMENTS xmlns="{NS['ar']}">
          <ECU-INSTANCE><SHORT-NAME>Ecu</SHORT-NAME></ECU-INSTANCE>
          <SYSTEM-SIGNAL><SHORT-NAME>Signal</SHORT-NAME></SYSTEM-SIGNAL>
          <SYSTEM-SIGNAL><SHORT-NAME>Signal2</SHORT-NAME></SYSTEM-SIGNAL>
          <SYSTEM-SIGNAL-GROUP><SHORT-NAME>Group</SHORT-NAME></SYSTEM-SIGNAL-GROUP>
        </ELEMENTS>"""))
    ET.register_namespace("", NS["ar"])
    tree.write(model, encoding="utf-8", xml_declaration=True)
    write_white_table(directory, {
        "Components": COMPONENTS,
        "AssemblySwConnection": [ASSEMBLY],
        "SenderReceiverToSignal": [SIGNAL],
        "SenderReceiverToSignalGroup": [["/Pkg/Group", "A", "pOut", "a"]],
    })
    result = run_tool(SYSTEM, "--config", write_config(directory, generateSystem=True, interfaceMismatch="map"))
    assert result.returncode == 0, result.stderr
    base = directory / "TSystem.arxml"
    shutil.copyfile(directory / "out" / base.name, base)
    tree = ET.parse(base)
    elements = tree.getroot().find("ar:AR-PACKAGES/ar:AR-PACKAGE/ar:ELEMENTS", NS)
    elements.append(ET.fromstring(f"""
        <SYSTEM-SIGNAL xmlns="{NS['ar']}"><SHORT-NAME>UnrelatedBaseSignal</SHORT-NAME></SYSTEM-SIGNAL>
    """))
    tree.write(base, encoding="utf-8", xml_declaration=True)
    list_file.write_text("model.arxml\nTSystem.arxml\nTSystem.arxml\n", encoding="utf-8")
    write_white_table(directory, {
        "Components": COMPONENTS,
        "SenderReceiverToSignal": [["/Pkg/Signal2", "OUT", "A", "pOut", "a"]],
    })
    return base


@pytest.mark.parametrize("mode", ["default", "replace", "merge"])
def test_output_mode_retains_only_connections_and_signal_mappings(tmp_path, mode):
    base = prepare_base(tmp_path)
    before = base.read_bytes()
    config = write_config(tmp_path, generateSystem=True, **({} if mode == "default" else {"outputMode": mode}))
    result = run_tool(SYSTEM, "--config", config)
    assert result.returncode == 0, result.stderr
    root = ET.parse(tmp_path / "out" / base.name).getroot()
    assert len(root.findall(".//ar:ASSEMBLY-SW-CONNECTOR", NS)) == (1 if mode == "merge" else 0)
    paths = [item.text for item in root.findall(".//ar:SYSTEM-SIGNAL-REF", NS)]
    assert set(paths) == ({"/Pkg/Signal", "/Pkg/Signal2"} if mode == "merge" else {"/Pkg/Signal2"})
    assert len(root.findall(".//ar:SENDER-RECEIVER-TO-SIGNAL-GROUP-MAPPING", NS)) == (1 if mode == "merge" else 0)
    assert "UnrelatedBaseSignal" not in (tmp_path / "out" / base.name).read_text(encoding="utf-8")
    assert base.read_bytes() == before


def test_merge_deduplicates_and_cli_overrides_configuration(tmp_path):
    base = prepare_base(tmp_path)
    write_white_table(tmp_path, {
        "Components": COMPONENTS, "AssemblySwConnection": [ASSEMBLY],
        "SenderReceiverToSignal": [SIGNAL],
        "SenderReceiverToSignalGroup": [["/Pkg/Group", "A", "pOut", "a"]],
    })
    result = run_tool(
        SYSTEM, "--config", write_config(tmp_path, generateSystem=True, outputMode="invalid"),
        "--output-mode", "merge",
    )
    assert result.returncode == 0, result.stderr
    root = ET.parse(tmp_path / "out" / base.name).getroot()
    assert len(root.findall(".//ar:ASSEMBLY-SW-CONNECTOR", NS)) == 1
    assert len(root.findall(".//ar:SENDER-RECEIVER-TO-SIGNAL-MAPPING", NS)) == 1
    assert len(root.findall(".//ar:SENDER-RECEIVER-TO-SIGNAL-GROUP-MAPPING", NS)) == 1


@pytest.mark.parametrize("problem", [
    "missing", "ambiguous", "excluded", "broken", "missing-reference", "wrong-dest",
    "conflict", "wrong-composition", "system-disabled", "overlap",
])
def test_merge_errors_do_not_publish_or_modify_base(tmp_path, problem):
    base = prepare_base(tmp_path)
    options = {"generateSystem": True, "outputMode": "merge", "outputDir": "new-output"}
    if problem == "missing":
        (tmp_path / "model.lst").write_text("model.arxml\n", encoding="utf-8")
    elif problem == "ambiguous":
        other = tmp_path / "other"
        other.mkdir()
        shutil.copyfile(base, other / base.name)
        (tmp_path / "model.lst").write_text("model.arxml\nTSystem.arxml\nother/TSystem.arxml\n", encoding="utf-8")
    elif problem == "excluded":
        options["excludeArxml"] = ["TSystem.arxml"]
    elif problem == "broken":
        base.write_text("<AUTOSAR>", encoding="utf-8")
    elif problem == "missing-reference":
        base.write_text(base.read_text(encoding="utf-8").replace("/Pkg/A/pOut", "/Pkg/A/Missing"), encoding="utf-8")
    elif problem == "wrong-dest":
        base.write_text(base.read_text(encoding="utf-8").replace('DEST="P-PORT-PROTOTYPE"', 'DEST="R-PORT-PROTOTYPE"'),
                        encoding="utf-8")
    elif problem == "conflict":
        write_white_table(tmp_path, {
            "Components": COMPONENTS, "AssemblySwConnection": [ASSEMBLY],
        })
        base.write_text(base.read_text(encoding="utf-8").replace("/Pkg/A/pOut", "/Pkg/A/pWide"), encoding="utf-8")
    elif problem == "wrong-composition":
        base.write_text(base.read_text(encoding="utf-8").replace("<SHORT-NAME>Top</SHORT-NAME>",
                                                              "<SHORT-NAME>OtherTop</SHORT-NAME>"), encoding="utf-8")
    elif problem == "system-disabled":
        options["generateSystem"] = False
        write_white_table(tmp_path, {"Components": COMPONENTS})
    else:
        options["outputDir"] = "."
    before = base.read_bytes()
    result = run_tool(SYSTEM, "--config", write_config(tmp_path, **options))
    assert result.returncode == 1, result.stderr
    assert "Traceback" not in result.stderr
    assert base.read_bytes() == before
    assert not (tmp_path / "new-output").exists()


def test_merge_creates_signal_mapping_container_when_table_has_no_signals(tmp_path):
    base = prepare_base(tmp_path)
    write_white_table(tmp_path, {"Components": COMPONENTS})
    result = run_tool(SYSTEM, "--config", write_config(tmp_path, generateSystem=True, outputMode="merge"))
    assert result.returncode == 0, result.stderr
    root = ET.parse(tmp_path / "out" / base.name).getroot()
    assert len(root.findall(".//ar:SYSTEM-MAPPING", NS)) == 1
    assert len(root.findall(".//ar:SENDER-RECEIVER-TO-SIGNAL-MAPPING", NS)) == 1


def test_merge_preserves_only_required_interface_mapping_dependencies(tmp_path):
    base = prepare_base(tmp_path, mapped=True)
    result = run_tool(SYSTEM, "--config", write_config(tmp_path, generateSystem=True, outputMode="merge"))
    assert result.returncode == 0, result.stderr
    root = ET.parse(tmp_path / "out" / base.name).getroot()
    assert len(root.findall(".//ar:VARIABLE-AND-PARAMETER-INTERFACE-MAPPING", NS)) == 1
    reference = root.find(".//ar:ASSEMBLY-SW-CONNECTOR/ar:MAPPING-REF", NS)
    assert reference is not None
    assert "/Pkg/Top_InterfaceMappings/" in reference.text
    assert not root.findall(".//ar:AR-PACKAGE/ar:AR-PACKAGES", NS)


def test_merge_preserves_delegation_and_excludes_old_ecu_mappings(tmp_path):
    base = prepare_base(tmp_path)
    write_white_table(tmp_path, {
        "Components": COMPONENTS, "DelegationSwConnection": [["A", "pOut", "outer"]],
    })
    config = write_config(tmp_path, generateSystem=True)
    result = run_tool(SYSTEM, "--config", config)
    assert result.returncode == 0, result.stderr
    tree = ET.parse(tmp_path / "out" / base.name)
    mapping = ET.fromstring(f"""
        <MAPPINGS xmlns="{NS['ar']}"><SYSTEM-MAPPING><SHORT-NAME>OldEcuMapping</SHORT-NAME>
          <SW-MAPPINGS><SWC-TO-ECU-MAPPING><SHORT-NAME>OldEcu</SHORT-NAME></SWC-TO-ECU-MAPPING></SW-MAPPINGS>
        </SYSTEM-MAPPING></MAPPINGS>""")
    tree.getroot().find(".//ar:SYSTEM", NS).append(mapping)
    tree.write(base, encoding="utf-8", xml_declaration=True)
    model = tmp_path / "model.arxml"
    text = model.read_text(encoding="utf-8").replace(
        "<SHORT-NAME>Top</SHORT-NAME>", """<SHORT-NAME>Top</SHORT-NAME>
          <PORTS><P-PORT-PROTOTYPE><SHORT-NAME>outer</SHORT-NAME>
            <PROVIDED-INTERFACE-TREF DEST="SENDER-RECEIVER-INTERFACE">/Pkg/IfA</PROVIDED-INTERFACE-TREF>
          </P-PORT-PROTOTYPE></PORTS>""",
    )
    model.write_text(text, encoding="utf-8")
    write_white_table(tmp_path, {"Components": COMPONENTS})
    result = run_tool(SYSTEM, "--config", write_config(tmp_path, generateSystem=False, outputMode="merge"))
    assert result.returncode == 0, result.stderr
    root = ET.parse(tmp_path / "out" / base.name).getroot()
    assert len(root.findall(".//ar:DELEGATION-SW-CONNECTOR", NS)) == 1
    assert not root.findall(".//ar:PORTS", NS)
    assert not root.findall(".//ar:SYSTEM-MAPPING", NS)


def test_merge_works_with_multiple_configurations_and_base_names(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    first_base = prepare_base(first)
    second_base = prepare_base(second)
    second_base.rename(second / "USystem.arxml")
    (second / "model.lst").write_text("model.arxml\nUSystem.arxml\n", encoding="utf-8")
    config_a = write_config(first, outputMode="merge", generateSystem=True)
    config_b = write_config(second, outputPrefix="U", generateSystem=True)
    result = run_tool(
        SYSTEM, "--config", config_a, "--config", config_b,
        "--output-dir", tmp_path / "combined", "--log-dir", tmp_path / "combined-log",
    )
    assert result.returncode == 0, result.stderr
    root = ET.parse(tmp_path / "combined" / first_base.name).getroot()
    assert len(root.findall(".//ar:ASSEMBLY-SW-CONNECTOR", NS)) == 1
    root = ET.parse(tmp_path / "combined" / "USystem.arxml").getroot()
    assert not root.findall(".//ar:ASSEMBLY-SW-CONNECTOR", NS)


def test_merge_deduplicates_signals_across_mapping_names(tmp_path):
    base = prepare_base(tmp_path)
    write_white_table(tmp_path, {
        "Components": COMPONENTS, "SenderReceiverToSignal": [SIGNAL],
    })
    result = run_tool(SYSTEM, "--config", write_config(
        tmp_path, generateSystem=True, outputMode="merge", general={"systemMapping": "NewMapping"},
    ))
    assert result.returncode == 0, result.stderr
    root = ET.parse(tmp_path / "out" / base.name).getroot()
    assert len(root.findall(".//ar:SENDER-RECEIVER-TO-SIGNAL-MAPPING", NS)) == 1
    assert len(root.findall(".//ar:SENDER-RECEIVER-TO-SIGNAL-GROUP-MAPPING", NS)) == 1


def test_green_mode_merges_same_named_base_from_list(tmp_path):
    from table_to_arxml.common.config_file import read_config_file

    sample = ROOT / "test" / "samples" / "system"
    config = read_config_file(sample / "BcmTableToSystemArxmlConfig.toml")
    config.update(
        projectRoot=str(ROOT),
        greenTables=[str(sample / "GreenTable" / "Bcm" / "*.xlsx")],
        arxmlFileList=str(tmp_path / "inputs.lst"),
        outputDir=str(tmp_path / "out"), logDir=str(tmp_path / "log"), outputMode="merge",
    )
    base = tmp_path / "BcmSystem.arxml"
    shutil.copyfile(sample / "expected" / "BcmTest" / base.name, base)
    before = base.read_bytes()
    original_list = ROOT / "test" / "data" / "common" / "arxml.lst"
    (tmp_path / "inputs.lst").write_text(
        original_list.read_text(encoding="utf-8") + f"\n{base}\n", encoding="utf-8",
    )
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    result = run_tool(SYSTEM, "--config", path)
    assert result.returncode == 0, result.stderr
    root = ET.parse(tmp_path / "out" / base.name).getroot()
    assert len(root.findall(".//ar:ASSEMBLY-SW-CONNECTOR", NS)) == 2
    assert (tmp_path / "out" / "BcmWhiteTable.xlsx").is_file()
    assert (tmp_path / "out" / "BcmGreenTables.xlsx").is_file()
    assert base.read_bytes() == before


def test_merge_rejects_splitting_an_external_system_mapping(tmp_path):
    base = prepare_base(tmp_path)
    tree = ET.parse(tmp_path / "model.arxml")
    tree.getroot().find(".//ar:SYSTEM", NS).append(ET.fromstring(f"""
        <MAPPINGS xmlns="{NS['ar']}"><SYSTEM-MAPPING>
          <SHORT-NAME>SystemMapping_TSystemImpl</SHORT-NAME>
        </SYSTEM-MAPPING></MAPPINGS>"""))
    tree.write(tmp_path / "model.arxml", encoding="utf-8", xml_declaration=True)
    before = base.read_bytes()
    config = write_config(
        tmp_path, generateSystem=True, outputMode="merge", outputDir="new-output",
        general={"systemMapping": "NewMapping"},
    )
    result = run_tool(SYSTEM, "--config", config)
    assert result.returncode == 1, result.stderr
    assert "Cannot split existing SYSTEM-MAPPING" in result.stderr
    assert not (tmp_path / "new-output").exists()
    assert base.read_bytes() == before
