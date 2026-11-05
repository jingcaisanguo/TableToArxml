"""Merge reuses table requests despite base-only details and keeps the base schema order and formatting."""

import codecs
import shutil
import time
import xml.etree.ElementTree as ET
from copy import deepcopy

import pytest

from e2e.system.test_base_merge import ASSEMBLY, COMPONENTS, SIGNAL, prepare_base
from helpers import NS, SYSTEM, run_tool
from system_fixtures import write_config, write_white_table

AR = NS["ar"]
GROUP = ["/Pkg/Group", "A", "pOut", "a"]
ADMIN_DATA = """<ADMIN-DATA><SDGS><SDG GID="Origin">
  <SDX-REF DEST="SW-SYSTEMCONST">/Nowhere/Origin</SDX-REF></SDG></SDGS></ADMIN-DATA>"""
VARIATION_POINT = """<VARIATION-POINT><SHORT-LABEL>{label}</SHORT-LABEL>
  <SW-SYSCOND BINDING-TIME="PRE-COMPILE-TIME"><SYSC-REF DEST="SW-SYSTEMCONST">/Pkg/{label}</SYSC-REF> == 1</SW-SYSCOND>
</VARIATION-POINT>"""


def nodes(text):
    return list(ET.fromstring(f'<W xmlns="{AR}">{text}</W>'))


def kinds(element):
    return [child.tag.rsplit("}", 1)[-1] for child in element]


def edit(path, change):
    tree = ET.parse(path)
    change(tree.getroot())
    ET.register_namespace("", AR)
    tree.write(path, encoding="utf-8", xml_declaration=True)


def merge(directory, **options):
    return run_tool(SYSTEM, "--config", write_config(directory, outputMode="merge", generateSystem=True, **options))


def output(directory):
    return ET.parse(directory / "out" / "TSystem.arxml").getroot()


def named(root, tag, name):
    return next(item for item in root.iter(f"{{{AR}}}{tag}") if item.findtext("ar:SHORT-NAME", namespaces=NS) == name)


class _Document:
    """Stands in for the armodel document of the fixture model."""

    CLASSES = {
        "/Pkg/Top/A": "SwComponentPrototype", "/Pkg/Top/B": "SwComponentPrototype",
        "/Pkg/A/pOut": "PPortPrototype", "/Pkg/B/rIn": "RPortPrototype",
    }

    def find(self, path):
        name = self.CLASSES.get(path)
        return type(name, (), {})() if name else None


def test_merge_reuses_group_mapping_with_base_type_mapping(tmp_path):
    base = prepare_base(tmp_path)
    edit(base, lambda root: root.find(".//ar:SENDER-RECEIVER-TO-SIGNAL-GROUP-MAPPING", NS).extend(nodes("""
        <TYPE-MAPPING><SENDER-REC-RECORD-TYPE-MAPPING><RECORD-ELEMENT-MAPPINGS><SENDER-REC-RECORD-ELEMENT-MAPPING>
          <IMPLEMENTATION-RECORD-ELEMENT-REF DEST="IMPLEMENTATION-DATA-TYPE-ELEMENT">/Pkg/Record/field
          </IMPLEMENTATION-RECORD-ELEMENT-REF>
          <SYSTEM-SIGNAL-REF DEST="SYSTEM-SIGNAL">/Pkg/Signal</SYSTEM-SIGNAL-REF>
        </SENDER-REC-RECORD-ELEMENT-MAPPING></RECORD-ELEMENT-MAPPINGS></SENDER-REC-RECORD-TYPE-MAPPING></TYPE-MAPPING>
    """)))
    write_white_table(tmp_path, {"Components": COMPONENTS, "SenderReceiverToSignalGroup": [GROUP]})
    result = merge(tmp_path)
    assert result.returncode == 0, result.stderr
    mappings = output(tmp_path).findall(".//ar:SENDER-RECEIVER-TO-SIGNAL-GROUP-MAPPING", NS)
    assert len(mappings) == 1
    assert mappings[0].find("ar:TYPE-MAPPING", NS) is not None


@pytest.mark.parametrize("variant", ["same-name", "renamed", "variants"])
def test_merge_reuses_connector_with_base_only_details(tmp_path, variant):
    base = prepare_base(tmp_path)

    def change(root):
        connectors = root.find(".//ar:CONNECTORS", NS)
        connector = connectors[0]
        if variant == "renamed":
            connector.find("ar:SHORT-NAME", NS).text = "Manual"
        connector[1:1] = nodes(ADMIN_DATA + VARIATION_POINT.format(label="SysConstA"))
        if variant == "variants":
            twin = deepcopy(connector)
            twin.remove(twin.find("ar:VARIATION-POINT", NS))
            twin.insert(2, nodes(VARIATION_POINT.format(label="SysConstB"))[0])
            connectors.append(twin)

    edit(base, change)
    write_white_table(tmp_path, {"Components": COMPONENTS, "AssemblySwConnection": [ASSEMBLY]})
    result = merge(tmp_path)
    assert result.returncode == 0, result.stderr
    connectors = output(tmp_path).findall(".//ar:ASSEMBLY-SW-CONNECTOR", NS)
    assert len(connectors) == (2 if variant == "variants" else 1)
    assert connectors[0].findtext("ar:SHORT-NAME", namespaces=NS) == (
        "Manual" if variant == "renamed" else "ac_A_pOut_B_rIn")
    assert connectors[0].find("ar:ADMIN-DATA", NS) is not None


@pytest.mark.parametrize("direction", [None, "IN"])
def test_merge_compares_only_present_communication_direction(tmp_path, direction):
    base = prepare_base(tmp_path)

    def change(root):
        mapping = root.find(".//ar:SENDER-RECEIVER-TO-SIGNAL-MAPPING", NS)
        element = mapping.find("ar:COMMUNICATION-DIRECTION", NS)
        if direction is None:
            mapping.remove(element)
        else:
            element.text = direction

    edit(base, change)
    write_white_table(tmp_path, {"Components": COMPONENTS, "SenderReceiverToSignal": [SIGNAL]})
    result = merge(tmp_path)
    if direction is None:
        assert result.returncode == 0, result.stderr
        mappings = output(tmp_path).findall(".//ar:SENDER-RECEIVER-TO-SIGNAL-MAPPING", NS)
        assert len(mappings) == 1
        assert mappings[0].find("ar:COMMUNICATION-DIRECTION", NS) is None
    else:
        assert result.returncode == 1
        assert "/Pkg/IfA/a" in result.stderr
        assert "/Pkg/Signal" in result.stderr
        assert "COMMUNICATION-DIRECTION IN" in result.stderr


def test_merge_validates_base_reference_to_pr_port(tmp_path):
    base = prepare_base(tmp_path)
    edit(tmp_path / "model.arxml", lambda root: named(root, "APPLICATION-SW-COMPONENT-TYPE", "A").find(
        "ar:PORTS", NS).extend(nodes("""<PR-PORT-PROTOTYPE><SHORT-NAME>prBoth</SHORT-NAME>
          <PROVIDED-REQUIRED-INTERFACE-TREF DEST="SENDER-RECEIVER-INTERFACE">/Pkg/IfA</PROVIDED-REQUIRED-INTERFACE-TREF>
        </PR-PORT-PROTOTYPE>""")))

    def change(root):
        connectors = root.find(".//ar:CONNECTORS", NS)
        connector = deepcopy(connectors[0])
        connector.find("ar:SHORT-NAME", NS).text = "ac_pr"
        port = connector.find("ar:PROVIDER-IREF/ar:TARGET-P-PORT-REF", NS)
        port.set("DEST", "PR-PORT-PROTOTYPE")
        port.text = "/Pkg/A/prBoth"
        connectors.append(connector)

    edit(base, change)
    write_white_table(tmp_path, {"Components": COMPONENTS})
    result = merge(tmp_path)
    assert result.returncode == 0, result.stderr
    assert len(output(tmp_path).findall(".//ar:ASSEMBLY-SW-CONNECTOR", NS)) == 2


@pytest.mark.parametrize("parent", ["composition", "system", "system-mapping"])
def test_merge_inserts_new_containers_in_schema_order(tmp_path, parent):
    base = prepare_base(tmp_path)
    rows = {"Components": COMPONENTS}

    def change(root):
        if parent == "composition":
            composition = named(root, "COMPOSITION-SW-COMPONENT-TYPE", "Top")
            composition.remove(composition.find("ar:CONNECTORS", NS))
            composition.extend(nodes("""<CONSTANT-VALUE-MAPPING-REFS>
              <CONSTANT-VALUE-MAPPING-REF DEST="CONSTANT-SPECIFICATION-MAPPING-SET">/Pkg/Constants
              </CONSTANT-VALUE-MAPPING-REF></CONSTANT-VALUE-MAPPING-REFS>"""))
        elif parent == "system":
            system = root.find(".//ar:SYSTEM", NS)
            system.remove(system.find("ar:MAPPINGS", NS))
            system[3:3] = nodes("<PNC-VECTOR-LENGTH>8</PNC-VECTOR-LENGTH><PNC-VECTOR-OFFSET>0</PNC-VECTOR-OFFSET>")
        else:
            root.find(".//ar:SYSTEM-MAPPING", NS).extend(nodes("""<SWC-TO-APPLICATION-PARTITION-MAPPINGS>
              <SWC-TO-APPLICATION-PARTITION-MAPPING><SHORT-NAME>Partition</SHORT-NAME>
              </SWC-TO-APPLICATION-PARTITION-MAPPING></SWC-TO-APPLICATION-PARTITION-MAPPINGS>"""))

    edit(base, change)
    if parent == "composition":
        rows["AssemblySwConnection"] = [ASSEMBLY]
    elif parent == "system":
        rows["SenderReceiverToSignal"] = [SIGNAL]
    else:
        rows["Components"] = COMPONENTS + [["New", "New", "/Pkg/A", "Add"]]
    write_white_table(tmp_path, rows)
    result = merge(tmp_path)
    assert result.returncode == 0, result.stderr
    root = output(tmp_path)
    if parent == "composition":
        assert kinds(named(root, "COMPOSITION-SW-COMPONENT-TYPE", "Top")) == [
            "SHORT-NAME", "CONNECTORS", "CONSTANT-VALUE-MAPPING-REFS"]
    elif parent == "system":
        assert kinds(root.find(".//ar:SYSTEM", NS)) == [
            "SHORT-NAME", "CATEGORY", "FIBEX-ELEMENTS", "MAPPINGS", "PNC-VECTOR-LENGTH", "PNC-VECTOR-OFFSET",
            "ROOT-SOFTWARE-COMPOSITIONS"]
    else:
        assert kinds(root.find(".//ar:SYSTEM-MAPPING", NS)) == [
            "SHORT-NAME", "DATA-MAPPINGS", "SW-MAPPINGS", "SWC-TO-APPLICATION-PARTITION-MAPPINGS"]


@pytest.mark.parametrize("fibex", ["missing", "other"])
def test_merge_writes_ecu_only_to_base_system_without_fibex_elements(tmp_path, fibex):
    base = prepare_base(tmp_path)

    def change(root):
        system = root.find(".//ar:SYSTEM", NS)
        elements = system.find("ar:FIBEX-ELEMENTS", NS)
        if fibex == "missing":
            system.remove(elements)
        else:
            reference = elements.find(".//ar:FIBEX-ELEMENT-REF", NS)
            reference.set("DEST", "SYSTEM-SIGNAL")
            reference.text = "/Pkg/Signal"

    edit(base, change)
    result = merge(tmp_path)
    assert result.returncode == 0, result.stderr
    system = output(tmp_path).find(".//ar:SYSTEM", NS)
    assert kinds(system) == ["SHORT-NAME", "CATEGORY", "FIBEX-ELEMENTS", "MAPPINGS", "ROOT-SOFTWARE-COMPOSITIONS"]
    references = [item.text for item in system.findall(".//ar:FIBEX-ELEMENT-REF", NS)]
    assert references == (["/Pkg/Ecu"] if fibex == "missing" else ["/Pkg/Signal"])
    if fibex == "other":
        assert "FIBEX-ELEMENTS do not reference /Pkg/Ecu" in result.stderr


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_merge_preserves_base_prolog_whitespace_and_newlines(tmp_path, newline):
    base = prepare_base(tmp_path)
    mixed = '<DESC><L-2 L="EN"><TT>a</TT> <TT>b</TT></L-2></DESC>'
    text = base.read_text(encoding="utf-8").replace(
        "<?xml version='1.0' encoding='utf-8'?>\n",
        '<?xml version="1.0" encoding="UTF-8"?>\n<!--Created By INTEWORK-VDE-->\n',
    ).replace("<SHORT-NAME>UnrelatedBaseSignal</SHORT-NAME>", f"<SHORT-NAME>UnrelatedBaseSignal</SHORT-NAME>{mixed}")
    base.write_bytes((text + "\n").replace("\n", newline).encode("utf-8"))
    write_white_table(tmp_path, {"Components": COMPONENTS + [["New", "New", "/Pkg/A", "Add"]]})
    result = merge(tmp_path)
    assert result.returncode == 0, result.stderr
    data = (tmp_path / "out" / base.name).read_bytes()
    if newline == "\r\n":
        assert data.count(b"\r\n") == data.count(b"\n")
    else:
        assert b"\r" not in data
    merged = data.decode("utf-8").replace(newline, "\n")
    assert merged.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<!--Created By INTEWORK-VDE-->\n<AUTOSAR ')
    assert merged.endswith("</AUTOSAR>\n")
    assert f"<SYSTEM-SIGNAL><SHORT-NAME>UnrelatedBaseSignal</SHORT-NAME>{mixed}</SYSTEM-SIGNAL></ELEMENTS>" in merged
    indent = "\n" + " " * 10
    assert (f"{indent}<COMPONENTS>{indent}  <SW-COMPONENT-PROTOTYPE>{indent}    <SHORT-NAME>New</SHORT-NAME>"
            in merged)
    assert f"</COMPONENTS>{indent}<CONNECTORS>" in merged


@pytest.mark.parametrize("codec", ["utf-16-le", "utf-16-be"])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_utf16_base_round_trip_keeps_bytes(tmp_path, codec, newline):
    from table_to_arxml.system.base_merge import _read, _write

    bom = codecs.BOM_UTF16_LE if codec == "utf-16-le" else codecs.BOM_UTF16_BE
    text = ('<?xml version="1.0" encoding="UTF-16"?>\n<AUTOSAR xmlns="http://autosar.org/schema/r4.0">\n'
            '  <AR-PACKAGES>\n    <AR-PACKAGE>\n      <SHORT-NAME>Pkg</SHORT-NAME>\n    </AR-PACKAGE>\n'
            '  </AR-PACKAGES>\n</AUTOSAR>\n').replace("\n", newline)
    source = tmp_path / "base.arxml"
    source.write_bytes(bom + text.encode(codec))
    target = tmp_path / "copy.arxml"
    _write(_read(source), target)
    assert target.read_bytes() == source.read_bytes()

@pytest.mark.parametrize("name", ["ac_A_pOut_B_rIn", "Manual"])
def test_merge_reuses_connector_without_interface_mapping(tmp_path, name):
    base = prepare_base(tmp_path, mapped=True)

    def change(root):
        elements = root.find("ar:AR-PACKAGES/ar:AR-PACKAGE/ar:ELEMENTS", NS)
        elements.remove(elements.find("ar:PORT-INTERFACE-MAPPING-SET", NS))
        connector = root.find(".//ar:ASSEMBLY-SW-CONNECTOR", NS)
        connector.find("ar:SHORT-NAME", NS).text = name
        connector.remove(connector.find("ar:MAPPING-REF", NS))

    edit(base, change)
    write_white_table(tmp_path, {"Components": COMPONENTS, "AssemblySwConnection": [ASSEMBLY]})
    result = merge(tmp_path, interfaceMismatch="map")
    assert result.returncode == 0, result.stderr
    root = output(tmp_path)
    connectors = root.findall(".//ar:ASSEMBLY-SW-CONNECTOR", NS)
    assert [item.findtext("ar:SHORT-NAME", namespaces=NS) for item in connectors] == [name]
    assert connectors[0].find("ar:MAPPING-REF", NS) is None
    assert not root.findall(".//ar:PORT-INTERFACE-MAPPING-SET", NS)
    assert "references interface mapping (none)" in result.stderr


def test_merge_resolves_relative_base_references(tmp_path):
    base = prepare_base(tmp_path)

    def change(root):
        package = root.find("ar:AR-PACKAGES/ar:AR-PACKAGE", NS)
        package[1:1] = nodes("""<REFERENCE-BASES><REFERENCE-BASE><SHORT-LABEL>pkg</SHORT-LABEL>
          <IS-DEFAULT>false</IS-DEFAULT><IS-GLOBAL>false</IS-GLOBAL><BASE-IS-THIS-PACKAGE>true</BASE-IS-THIS-PACKAGE>
          <PACKAGE-REF DEST="AR-PACKAGE">/Pkg</PACKAGE-REF></REFERENCE-BASE></REFERENCE-BASES>""")
        for reference in root.find(".//ar:ASSEMBLY-SW-CONNECTOR", NS).iter():
            if reference.tag.endswith("-REF"):
                reference.set("BASE", "pkg")
                reference.text = reference.text[len("/Pkg/"):]

    edit(base, change)
    write_white_table(tmp_path, {"Components": COMPONENTS, "AssemblySwConnection": [ASSEMBLY]})
    result = merge(tmp_path)
    assert result.returncode == 0, result.stderr
    connectors = output(tmp_path).findall(".//ar:ASSEMBLY-SW-CONNECTOR", NS)
    assert len(connectors) == 1
    assert connectors[0].findtext("ar:PROVIDER-IREF/ar:TARGET-P-PORT-REF", namespaces=NS) == "A/pOut"


def test_merge_without_system_path_ignores_system_content(tmp_path):
    from table_to_arxml.system.base_merge import merge_base_arxml

    base = prepare_base(tmp_path)
    generated = tmp_path / "generated.arxml"
    shutil.copyfile(base, generated)
    edit(base, lambda root: setattr(root.find(".//ar:SYSTEM-SIGNAL-REF", NS), "text", "/Pkg/Missing"))
    merge_base_arxml(generated, base, _Document(), "/Pkg/Top", "")
    root = ET.parse(generated).getroot()
    assert len(root.findall(".//ar:ASSEMBLY-SW-CONNECTOR", NS)) == 1
    assert [item.text for item in root.findall(".//ar:SYSTEM-SIGNAL-REF", NS)] == ["/Pkg/Missing"]
    assert len(root.findall(".//ar:SENDER-RECEIVER-TO-SIGNAL-GROUP-MAPPING", NS)) == 1


def test_merge_deduplicates_large_connector_sets_quickly(tmp_path):
    from table_to_arxml.system.base_merge import merge_base_arxml

    count = 1500

    def connector(index, prefix):
        return f"""<ASSEMBLY-SW-CONNECTOR><SHORT-NAME>{prefix}{index}</SHORT-NAME>
          <PROVIDER-IREF><CONTEXT-COMPONENT-REF DEST="SW-COMPONENT-PROTOTYPE">/Pkg/Top/P{index}</CONTEXT-COMPONENT-REF>
            <TARGET-P-PORT-REF DEST="P-PORT-PROTOTYPE">/Pkg/A/pOut</TARGET-P-PORT-REF></PROVIDER-IREF>
          <REQUESTER-IREF><CONTEXT-COMPONENT-REF DEST="SW-COMPONENT-PROTOTYPE">/Pkg/Top/R{index}</CONTEXT-COMPONENT-REF>
            <TARGET-R-PORT-REF DEST="R-PORT-PROTOTYPE">/Pkg/B/rIn</TARGET-R-PORT-REF></REQUESTER-IREF>
        </ASSEMBLY-SW-CONNECTOR>"""

    def document(elements):
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<AUTOSAR xmlns="{AR}"><AR-PACKAGES><AR-PACKAGE><SHORT-NAME>Pkg</SHORT-NAME><ELEMENTS>{elements}</ELEMENTS>
</AR-PACKAGE></AR-PACKAGES></AUTOSAR>"""

    prototypes = "".join(
        f"<SW-COMPONENT-PROTOTYPE><SHORT-NAME>{side}{index}</SHORT-NAME></SW-COMPONENT-PROTOTYPE>"
        for index in range(2 * count) for side in "PR")
    base = tmp_path / "TSystem.arxml"
    base.write_text(document(
        "<APPLICATION-SW-COMPONENT-TYPE><SHORT-NAME>A</SHORT-NAME><PORTS><P-PORT-PROTOTYPE><SHORT-NAME>pOut"
        "</SHORT-NAME></P-PORT-PROTOTYPE></PORTS></APPLICATION-SW-COMPONENT-TYPE>"
        "<APPLICATION-SW-COMPONENT-TYPE><SHORT-NAME>B</SHORT-NAME><PORTS><R-PORT-PROTOTYPE><SHORT-NAME>rIn"
        "</SHORT-NAME></R-PORT-PROTOTYPE></PORTS></APPLICATION-SW-COMPONENT-TYPE>"
        f"<COMPOSITION-SW-COMPONENT-TYPE><SHORT-NAME>Top</SHORT-NAME><COMPONENTS>{prototypes}</COMPONENTS>"
        f"<CONNECTORS>{''.join(connector(index, 'base') for index in range(count))}</CONNECTORS>"
        "</COMPOSITION-SW-COMPONENT-TYPE>"), encoding="utf-8")
    generated = tmp_path / "generated.arxml"
    generated.write_text(document(
        "<COMPOSITION-SW-COMPONENT-TYPE><SHORT-NAME>Top</SHORT-NAME><CONNECTORS>"
        f"{''.join(connector(index, 'ac') for index in range(2 * count))}</CONNECTORS>"
        "</COMPOSITION-SW-COMPONENT-TYPE>"), encoding="utf-8")
    started = time.perf_counter()
    merge_base_arxml(generated, base, _Document(), "/Pkg/Top", "/Pkg/Sys")
    elapsed = time.perf_counter() - started
    names = [item.text for item in ET.parse(generated).getroot().iter(f"{{{AR}}}SHORT-NAME")
             if item.text.startswith(("base", "ac"))]
    assert names == [f"base{index}" for index in range(count)] + [f"ac{index}" for index in range(count, 2 * count)]
    assert elapsed < 5, f"merge took {elapsed:.1f}s"


def test_merge_reports_base_file_that_cannot_be_parsed(tmp_path):
    from table_to_arxml.system.base_merge import prepare_base_model

    base = tmp_path / "TSystem.arxml"
    base.write_bytes(b'<?xml version="1.0" encoding="GB2312"?>\n<AUTOSAR xmlns="http://autosar.org/schema/r4.0"/>')
    with pytest.raises(ValueError, match="TSystem.arxml"):
        prepare_base_model(base, tmp_path / "model.arxml", _Document())
