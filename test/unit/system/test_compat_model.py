"""Model compatibility: DEST names, unreadable ARXML, cyclic compositions and cached interface mappings."""

from __future__ import annotations

import logging
import time

import pytest

from table_to_arxml.system.arxml_model import ArxmlModel
from table_to_arxml.system.values import class_destination

NAMESPACE = "http://autosar.org/schema/r4.0"


@pytest.mark.parametrize(("class_name", "destination"), [
    ("PRPortPrototype", "PR-PORT-PROTOTYPE"),
    ("PPortPrototype", "P-PORT-PROTOTYPE"),
    ("ISignalIPdu", "I-SIGNAL-I-PDU"),
    ("ISignalGroup", "I-SIGNAL-GROUP"),
    ("EcuInstance", "ECU-INSTANCE"),
    ("SwcImplementation", "SWC-IMPLEMENTATION"),
    ("CanCluster", "CAN-CLUSTER"),
    ("LinUnconditionalFrame", "LIN-UNCONDITIONAL-FRAME"),
    ("J1939Cluster", "J-1939-CLUSTER"),
    ("Ipv6Configuration", "IPV-6-CONFIGURATION"),
    ("SOMEIPTransformationDescription", "SOMEIP-TRANSFORMATION-DESCRIPTION"),
    ("SomeipSdClientServiceInstanceConfig", "SOME-IP-SD-CLIENT-SERVICE-INSTANCE-CONFIG"),
    ("VariableAndParameterInterfaceMapping", "VARIABLE-AND-PARAMETER-INTERFACE-MAPPING"),
])
def test_class_destination_matches_autosar_xml_names(class_name, destination):
    assert class_destination(type(class_name, (), {})()) == destination


def test_class_destination_of_armodel_pr_port():
    from armodel import PRPortPrototype

    assert class_destination(PRPortPrototype(None, "port")) == "PR-PORT-PROTOTYPE"


@pytest.mark.parametrize("content", [
    b'<?xml version="1.0" encoding="UTF-8"?>\n<AUTOSAR xmlns="http://autosar.org/schema/r4.0"><AR-PACKAGES>',
    b'<?xml version="1.0" encoding="GB2312"?>\n<AUTOSAR xmlns="http://autosar.org/schema/r4.0"/>',
])
def test_unreadable_arxml_is_reported_with_its_path(tmp_path, content):
    broken = tmp_path / "Broken.arxml"
    broken.write_bytes(content)
    with pytest.raises(ValueError, match=r"Cannot parse ARXML .*Broken\.arxml"):
        ArxmlModel.load([broken])


def _composition(name: str, prototypes: list[tuple[str, str]]) -> str:
    children = "".join(
        f"""<SW-COMPONENT-PROTOTYPE><SHORT-NAME>{prototype}</SHORT-NAME>
          <TYPE-TREF DEST="COMPOSITION-SW-COMPONENT-TYPE">/Pkg/{type_name}</TYPE-TREF></SW-COMPONENT-PROTOTYPE>"""
        for prototype, type_name in prototypes)
    return f"""<COMPOSITION-SW-COMPONENT-TYPE><SHORT-NAME>{name}</SHORT-NAME>
      <COMPONENTS>{children}</COMPONENTS></COMPOSITION-SW-COMPONENT-TYPE>"""


@pytest.fixture()
def cyclic_model(tmp_path) -> ArxmlModel:
    compositions = "".join([
        _composition("Top", []),
        _composition("Loop", [("Self1", "Loop"), ("Self2", "Loop"), ("Self3", "Loop"), ("Top1", "Top")]),
        _composition("Ping", [("ToPong", "Pong")]),
        _composition("Pong", [("ToPing", "Ping"), ("ToTop", "Top")]),
        _composition("Root", [("LoopProto", "Loop"), ("PingProto", "Ping")]),
    ])
    path = tmp_path / "cyclic.arxml"
    path.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<AUTOSAR xmlns="{NAMESPACE}"><AR-PACKAGES><AR-PACKAGE><SHORT-NAME>Pkg</SHORT-NAME>
  <ELEMENTS>{compositions}</ELEMENTS></AR-PACKAGE></AR-PACKAGES></AUTOSAR>""", encoding="utf-8")
    return ArxmlModel.load([path])


def test_cyclic_compositions_are_pruned_quickly(cyclic_model, caplog):
    caplog.set_level(logging.WARNING)
    started = time.perf_counter()
    chains = cyclic_model.composition_chains("/Pkg/Root", "/Pkg/Top")
    elapsed = time.perf_counter() - started
    assert sorted(chains) == [
        ("/Pkg/Root/LoopProto", "/Pkg/Loop/Top1"),
        ("/Pkg/Root/PingProto", "/Pkg/Ping/ToPong", "/Pkg/Pong/ToTop"),
    ]
    assert cyclic_model.composition_chains("/Pkg/Loop", "/Pkg/Missing") == []
    assert elapsed < 1, f"composition chain search took {elapsed:.1f}s"
    assert "Composition /Pkg/Loop instantiates itself" in caplog.text
    assert "Composition /Pkg/Ping instantiates itself" in caplog.text


def test_interface_mappings_are_cached_until_invalidated(cyclic_model):
    mappings = cyclic_model.interface_mappings()
    assert cyclic_model.interface_mappings() is mappings
    cyclic_model.invalidate()
    assert cyclic_model.interface_mappings() is not mappings
