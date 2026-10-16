"""armodel 契约测试：CI 对 1.8.7、1.9.2 运行，确认工具依赖的行为和绕行代码仍然成立。

若某条“缺陷仍存在”的断言失败，说明新版 armodel 已修复该问题，可以评估删除
:mod:`table_to_arxml.common.armodel_compat` 中对应的绕行代码。
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET

import pytest
from armodel import ARXMLWriter, ComponentInSystemInstanceRef, RefType
from armodel.models.M2.AUTOSARTemplates.GenericStructure.GeneralTemplateClasses.PrimitiveTypes import ARLiteral
from armodel.models.M2.AUTOSARTemplates.SWComponentTemplate.Communication import NonqueuedReceiverComSpec

from table_to_arxml.common.armodel_compat import (
    SystemArxmlWriter,
    new_parser,
    reset_document,
    serialize,
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
    import table_to_arxml.system.type_signature  # noqa: F401


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


def test_writer_still_omits_context_component_ref():
    parent = ET.Element("X")
    ARXMLWriter().setComponentInSystemInstanceRef(parent, "COMPONENT-IREF", component_iref())
    assert "CONTEXT-COMPONENT-REF" not in [child.tag for child in parent[0]]


def test_system_writer_adds_context_component_ref():
    parent = ET.Element("X")
    SystemArxmlWriter().setComponentInSystemInstanceRef(parent, "COMPONENT-IREF", component_iref())
    assert [child.tag for child in parent[0]] == [
        "CONTEXT-COMPOSITION-REF", "CONTEXT-COMPONENT-REF", "TARGET-COMPONENT-REF"]


def receiver_com_spec() -> NonqueuedReceiverComSpec:
    com_spec = NonqueuedReceiverComSpec()
    com_spec.setDataElementRef(RefType().setDest("VARIABLE-DATA-PROTOTYPE").setValue("/I/e"))
    return com_spec


def test_receiver_com_spec_without_handle_timeout_type_still_breaks_writer():
    with pytest.raises(AttributeError):
        serialize(ARXMLWriter().writeNonqueuedReceiverComSpec, receiver_com_spec(), "REQUIRED-COM-SPECS")


def test_handle_timeout_type_placeholder_is_stripped():
    com_spec = receiver_com_spec()
    com_spec.setHandleTimeoutType(ARLiteral().setValue("NONE"))
    node = serialize(ARXMLWriter().writeNonqueuedReceiverComSpec, com_spec, "REQUIRED-COM-SPECS")
    assert node.find(".//HANDLE-TIMEOUT-TYPE") is not None
    strip_handle_timeout_type(node)
    assert node.find(".//HANDLE-TIMEOUT-TYPE") is None
