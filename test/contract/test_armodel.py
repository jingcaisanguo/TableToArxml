"""armodel 契约测试：CI 对 1.8.7、1.9.2、1.9.8 运行，确认工具依赖的行为和绕行代码在各版本下都成立。

armodel 1.9.4 和 1.9.8 先后改名或移动了部分类、方法，并修复了若干 writer 缺陷；
:mod:`table_to_arxml.common.armodel_compat` 按实际版本选择，这里只断言工具得到的结果一致。
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET

import pytest
from armodel import ARXMLWriter, ComponentInSystemInstanceRef, RefType, VariableDataPrototypeInSystemInstanceRef
from armodel.models.M2.AUTOSARTemplates.GenericStructure.GeneralTemplateClasses.PrimitiveTypes import ARLiteral
from armodel.models.M2.AUTOSARTemplates.SWComponentTemplate.Communication import NonqueuedReceiverComSpec
from armodel.models.M2.AUTOSARTemplates.SWComponentTemplate.Composition import DelegationSwConnector
from armodel.models.M2.AUTOSARTemplates.SystemTemplate.DataMapping import SenderReceiverToSignalMapping

from table_to_arxml.common.armodel_compat import (
    ARBoolean,
    AutosarVariableRef,
    SystemArxmlWriter,
    get_accessed_variable,
    get_enable_update,
    new_parser,
    reset_document,
    serialize,
    set_accessed_variable,
    set_communication_direction,
    set_enable_update,
    set_inner_port_iref,
    strip_handle_timeout_type,
)
from table_to_arxml.common.logs import ARMODEL_DIAGNOSTIC

MINIMAL = """<?xml version="1.0" encoding="UTF-8"?>
<AUTOSAR xmlns="http://autosar.org/schema/r4.0">
  <AR-PACKAGES>
    <AR-PACKAGE>
      <SHORT-NAME>{name}</SHORT-NAME>
    </AR-PACKAGE>
  </AR-PACKAGES>
</AUTOSAR>
"""


def test_tool_modules_import_with_this_armodel_version():
    import table_to_arxml.swc.arxml_model  # noqa: F401
    import table_to_arxml.system.arxml_generator  # noqa: F401


def test_document_is_a_global_singleton_cleared_by_reset(tmp_path):
    for name in ("First", "Second"):
        (tmp_path / f"{name}.arxml").write_text(MINIMAL.format(name=name), encoding="utf-8")
    document = reset_document()
    new_parser([]).load(str(tmp_path / "First.arxml"), document)
    assert document.find("/First") is not None
    again = reset_document()
    assert again is document
    assert document.find("/First") is None
    new_parser([]).load(str(tmp_path / "Second.arxml"), document)
    assert document.find("/Second") is not None


def test_swc_documents_become_stale_after_another_load(tmp_path):
    from table_to_arxml.swc.arxml_model import load_document

    (tmp_path / "a.arxml").write_text(MINIMAL.format(name="A"), encoding="utf-8")
    first, _ = load_document(tmp_path / "a.arxml")
    assert first.package_exists("/A")
    load_document(tmp_path / "a.arxml")
    with pytest.raises(RuntimeError, match="stale"):
        first.package_exists("/A")


def test_parser_logger_can_be_replaced_and_forwarded():
    target = logging.Logger("contract.target", level=logging.DEBUG)
    records: list[logging.LogRecord] = []
    target.addHandler(type("Collect", (logging.Handler,), {"emit": lambda self, record: records.append(record)})())
    diagnostics: list[str] = []
    parser = new_parser(diagnostics, target)
    assert hasattr(parser, "logger")
    parser.logger.error("\x1b[31mbroken\x1b[0m element")
    assert diagnostics == ["ERROR: broken element"]
    assert records and getattr(records[0], ARMODEL_DIAGNOSTIC) and records[0].getMessage() == "broken element"


def component_iref() -> ComponentInSystemInstanceRef:
    reference = ComponentInSystemInstanceRef().setContextCompositionRef(
        RefType().setDest("ROOT-SW-COMPOSITION-PROTOTYPE").setValue("/S/Root"))
    reference.addContextComponentRef(RefType().setDest("SW-COMPONENT-PROTOTYPE").setValue("/T/Inner"))
    return reference.setTargetComponentRef(RefType().setDest("SW-COMPONENT-PROTOTYPE").setValue("/T/Inner/X"))


def test_system_writer_writes_context_component_ref_exactly_once():
    parent = ET.Element("X")
    SystemArxmlWriter().setComponentInSystemInstanceRef(parent, "COMPONENT-IREF", component_iref())
    assert [child.tag for child in parent[0]] == [
        "CONTEXT-COMPOSITION-REF", "CONTEXT-COMPONENT-REF", "TARGET-COMPONENT-REF"]
    assert parent[0][1].text == "/T/Inner"


def test_system_writer_without_context_components_is_unchanged():
    reference = ComponentInSystemInstanceRef().setContextCompositionRef(
        RefType().setDest("ROOT-SW-COMPOSITION-PROTOTYPE").setValue("/S/Root"))
    reference.setTargetComponentRef(RefType().setDest("SW-COMPONENT-PROTOTYPE").setValue("/T/X"))
    expected, actual = ET.Element("X"), ET.Element("X")
    ARXMLWriter().setComponentInSystemInstanceRef(expected, "COMPONENT-IREF", reference)
    SystemArxmlWriter().setComponentInSystemInstanceRef(actual, "COMPONENT-IREF", reference)
    assert ET.tostring(actual) == ET.tostring(expected)


def receiver_com_spec() -> NonqueuedReceiverComSpec:
    com_spec = NonqueuedReceiverComSpec()
    com_spec.setDataElementRef(RefType().setDest("VARIABLE-DATA-PROTOTYPE").setValue("/I/e"))
    return com_spec


def test_handle_timeout_type_placeholder_is_stripped():
    com_spec = receiver_com_spec()
    com_spec.setHandleTimeoutType(ARLiteral().setValue("NONE"))
    node = serialize(ARXMLWriter().writeNonqueuedReceiverComSpec, com_spec, "REQUIRED-COM-SPECS")
    assert node.find(".//HANDLE-TIMEOUT-TYPE") is not None
    strip_handle_timeout_type(node)
    assert node.find(".//HANDLE-TIMEOUT-TYPE") is None


def test_enable_update_is_written_with_this_armodel_version():
    com_spec = receiver_com_spec()
    com_spec.setHandleTimeoutType(ARLiteral().setValue("NONE"))
    set_enable_update(com_spec, ARBoolean().setValue(True))
    assert str(get_enable_update(com_spec).getValue()).lower() == "true"
    node = serialize(ARXMLWriter().writeNonqueuedReceiverComSpec, com_spec, "REQUIRED-COM-SPECS")
    assert node.findtext(".//ENABLE-UPDATE") == "true"


def test_inner_port_iref_setter_matches_this_armodel_version():
    connector = DelegationSwConnector(None, "D")
    marker = object()
    set_inner_port_iref(connector, marker)
    getter = getattr(connector, "getInnerPortIRref", None) or connector.getInnerPortIRef
    assert getter() is marker


def test_signal_mapping_direction_is_written_on_every_version():
    mapping = SenderReceiverToSignalMapping()
    mapping.setDataElementIRef(VariableDataPrototypeInSystemInstanceRef().setContextCompositionRef(
        RefType().setDest("ROOT-SW-COMPOSITION-PROTOTYPE").setValue("/S/Root")))
    mapping.setSystemSignalRef(RefType().setDest("SYSTEM-SIGNAL").setValue("/Signals/S"))
    set_communication_direction(mapping, ARLiteral().setValue("IN"))
    parent = ET.Element("DATA-MAPPINGS")
    SystemArxmlWriter().writeSenderReceiverToSignalMapping(parent, mapping)
    assert [child.tag for child in parent[0]] == ["COMMUNICATION-DIRECTION", "DATA-ELEMENT-IREF", "SYSTEM-SIGNAL-REF"]
    assert parent[0].findtext("COMMUNICATION-DIRECTION") == "IN"


def test_variable_access_target_matches_this_armodel_version():
    from armodel.models.M2.AUTOSARTemplates.SWComponentTemplate.SwcInternalBehavior import RunnableEntity

    access = RunnableEntity(None, "R").createDataReadAccess("A")
    reference = AutosarVariableRef()
    set_accessed_variable(access, reference)
    assert get_accessed_variable(access) is reference
