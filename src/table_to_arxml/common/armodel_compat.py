"""对 armodel 的用法约定和缺陷绕行集中在这里，由 test/contract 按 CI 指定的版本矩阵验证。

两个工具都直接依赖 armodel，不再额外抽象解析库；这里只放与具体业务无关、且依赖 armodel 内部
行为的代码：全局文档复位、解析器日志接管、片段序列化以及 writer 的缺陷修补。
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Any, Callable

from armodel import ARXMLWriter
from armodel.models.M2.AUTOSARTemplates.AutosarTopLevelStructure import AUTOSAR
from armodel.parser.arxml_parser import ARXMLParser

from .logs import ARMODEL_DIAGNOSTIC
from .messages import clean_message


def reset_document() -> AUTOSAR:
    """返回清空后的全局 ``AUTOSAR`` 文档。

    armodel 的 ``AUTOSAR`` 是进程级单例，每次加载前必须清空，否则上一次的包和元素会混入。
    """

    document = AUTOSAR.getInstance()
    if document is None:
        raise RuntimeError("armodel failed to create the AUTOSAR singleton")
    document.clear()
    return document


class _ParserDiagnostics(logging.Handler):
    """收集解析器的 WARNING/ERROR；可选地把记录转发给工具 logger，并标记为不计入失败。"""

    def __init__(self, diagnostics: list[str], forward_to: logging.Logger | None) -> None:
        super().__init__(level=logging.WARNING)
        self.diagnostics = diagnostics
        self.forward_to = forward_to

    def emit(self, record: logging.LogRecord) -> None:
        message = clean_message(record.getMessage())
        self.diagnostics.append(f"{record.levelname}: {message}")
        if self.forward_to is not None and self.forward_to.isEnabledFor(record.levelno):
            forwarded = logging.makeLogRecord({**record.__dict__, "msg": message, "args": ()})
            setattr(forwarded, ARMODEL_DIAGNOSTIC, True)
            self.forward_to.handle(forwarded)


def new_parser(diagnostics: list[str], forward_to: logging.Logger | None = None) -> ARXMLParser:
    """创建开启 warning 模式的解析器，并用独立 logger 接管其诊断输出。

    armodel 默认把解析器日志写到 root logger；替换 ``parser.logger`` 后，诊断只进入 ``diagnostics``，
    需要时再按原级别转发给 ``forward_to``。
    """

    parser_logger = logging.Logger(f"{__name__}.parser", level=logging.WARNING)
    parser_logger.addHandler(_ParserDiagnostics(diagnostics, forward_to))
    parser = ARXMLParser(options={"warning": True})
    parser.logger = parser_logger
    return parser


def serialize(write: Callable[[ET.Element, Any], None], element: Any, container_tag: str = "ELEMENTS") -> ET.Element:
    """用 writer 的方法把单个模型对象写到临时容器下，返回生成的 XML 元素。"""

    parent = ET.Element(container_tag)
    write(parent, element)
    return next(iter(parent))


def to_fragment(node: ET.Element) -> str:
    """把 XML 元素格式化为两空格缩进的片段文本。"""

    ET.indent(node, space="  ")
    return ET.tostring(node, encoding="unicode")


def strip_handle_timeout_type(node: ET.Element) -> None:
    """删除 writer 为接收端 ComSpec 生成的 ``HANDLE-TIMEOUT-TYPE``。

    armodel 把该属性缺省为空串并照样序列化，构造对象时只能先填占位值；工作簿不指定此属性，
    因此输出前删除，保持与手写 ARXML 一致。
    """

    for parent in node.iter():
        for child in list(parent):
            if child.tag == "HANDLE-TIMEOUT-TYPE":
                parent.remove(child)


class SystemArxmlWriter(ARXMLWriter):
    """补写 ``ComponentInSystemInstanceRef`` 的 ``CONTEXT-COMPONENT-REF``。

    armodel 的 writer 漏写这一引用，而嵌套 Composition 中的组件实例需要它来定位。
    """

    def setComponentInSystemInstanceRef(self, element, tag_name, ref):
        if ref is not None:
            child_element = ET.SubElement(element, tag_name)
            self.writeARObjectAttributes(child_element, ref)
            self.setChildElementOptionalRefType(child_element, "BASE-REF", ref.getBaseRef())
            self.setChildElementOptionalRefType(child_element, "CONTEXT-COMPOSITION-REF", ref.getContextCompositionRef())
            for context in ref.getContextComponentRefs():
                self.setChildElementOptionalRefType(child_element, "CONTEXT-COMPONENT-REF", context)
            self.setChildElementOptionalRefType(child_element, "TARGET-COMPONENT-REF", ref.getTargetComponentRef())
