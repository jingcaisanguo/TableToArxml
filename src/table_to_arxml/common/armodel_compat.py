"""对 armodel 的用法约定和缺陷绕行集中在这里，由 test/contract 按 CI 指定的版本矩阵验证。

两个工具都直接依赖 armodel，不再额外抽象解析库；这里只放与具体业务无关、且依赖 armodel 内部
行为的代码：跨版本的类导入、全局文档复位、解析器日志接管、片段序列化以及 writer 的缺陷修补。
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

# armodel 1.9.4 起删除 ARBoolean/ARFloat，改用 Boolean/Float；1.9.8 起又删除 ARNumerical，改用 Numerical
# （1.9.4–1.9.7 两者并存）。1.9.4 起还把下面几个类移到了其他模块。
# 先按旧名称和旧位置导入：1.9.2 及以前虽然也有 Boolean，但 reader/writer 使用的是 ARBoolean 等旧类。
try:
    from armodel.models.M2.AUTOSARTemplates.GenericStructure.GeneralTemplateClasses.PrimitiveTypes import (
        ARBoolean,
        ARFloat,
        ARNumerical,
    )
except ImportError:
    from armodel.models.M2.AUTOSARTemplates.GenericStructure.GeneralTemplateClasses.PrimitiveTypes import (
        Boolean as ARBoolean,
        Float as ARFloat,
        Numerical as ARNumerical,
    )
try:
    from armodel.models.M2.AUTOSARTemplates.SystemTemplate.Fibex.FibexCore.CoreTopology import ISignalPort
except ImportError:
    from armodel.models.M2.AUTOSARTemplates.SystemTemplate.Fibex.FibexCore.CoreCommunication import ISignalPort
try:
    from armodel.models.M2.AUTOSARTemplates.SWComponentTemplate.Composition.InstanceRefs import (
        POperationInAtomicSwcInstanceRef,
        ROperationInAtomicSwcInstanceRef,
    )
except ImportError:
    from armodel.models.M2.AUTOSARTemplates.SWComponentTemplate.Components.InstanceRefs import (
        POperationInAtomicSwcInstanceRef,
        ROperationInAtomicSwcInstanceRef,
    )
try:
    from armodel.models.M2.AUTOSARTemplates.SWComponentTemplate.SwcInternalBehavior.AutosarVariableRef import (
        AutosarVariableRef,
    )
    from armodel.models.M2.AUTOSARTemplates.SWComponentTemplate.SwcInternalBehavior.InstanceRefsUsage import (
        VariableInAtomicSWCTypeInstanceRef,
    )
except ImportError:
    from armodel.models.M2.AUTOSARTemplates.SWComponentTemplate.SwcInternalBehavior.DataElements import (
        AutosarVariableRef,
    )
    from armodel.models.M2.AUTOSARTemplates.SWComponentTemplate.SwcInternalBehavior.DataElements.InstanceRefsUsage import (  # noqa: E501
        VariableInAtomicSWCTypeInstanceRef,
    )

__all__ = [
    "ARBoolean", "ARFloat", "ARNumerical", "AutosarVariableRef", "ISignalPort",
    "POperationInAtomicSwcInstanceRef", "ROperationInAtomicSwcInstanceRef", "VariableInAtomicSWCTypeInstanceRef",
]


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
    """补齐不同 armodel 版本 writer 的差异，使 System ARXML 输出与版本无关。

    - ``ComponentInSystemInstanceRef`` 的 ``CONTEXT-COMPONENT-REF``：1.9.7 及以前漏写，嵌套 Composition
      中的组件实例需要它来定位；1.9.8 起已自行写出。
    - ``SenderReceiverToSignalMapping`` 的 ``COMMUNICATION-DIRECTION``：1.9.8 起按 R23-11 删除了该属性，
      为保持工具输出不变，仍按旧版本的位置写出。

    两处都只在原生 writer 缺失时补写，不重复。
    """

    def setComponentInSystemInstanceRef(self, element, tag_name, ref):
        super().setComponentInSystemInstanceRef(element, tag_name, ref)
        if ref is None or not ref.getContextComponentRefs():
            return
        child_element = element[-1]
        if child_element.find("CONTEXT-COMPONENT-REF") is not None:
            return
        staging = ET.Element(tag_name)
        for context in ref.getContextComponentRefs():
            self.setChildElementOptionalRefType(staging, "CONTEXT-COMPONENT-REF", context)
        _insert_before(child_element, "TARGET-COMPONENT-REF", list(staging))

    def writeSenderReceiverToSignalMapping(self, element, mapping):
        super().writeSenderReceiverToSignalMapping(element, mapping)
        direction = getattr(mapping, "communicationDirection", None)
        child_element = element[-1]
        if direction is None or child_element.find("COMMUNICATION-DIRECTION") is not None:
            return
        staging = ET.Element("staging")
        self.setChildElementOptionalLiteral(staging, "COMMUNICATION-DIRECTION", direction)
        _insert_before(child_element, "DATA-ELEMENT-IREF", list(staging))


def _insert_before(parent: ET.Element, anchor_tag: str, children: list[ET.Element]) -> None:
    anchor = parent.find(anchor_tag)
    index = list(parent).index(anchor) if anchor is not None else len(parent)
    parent[index:index] = children


def set_communication_direction(mapping: Any, value: Any) -> None:
    """设置信号映射方向；1.9.8 起模型没有该属性，保存在同名字段中由 :class:`SystemArxmlWriter` 写出。"""

    setter = getattr(mapping, "setCommunicationDirection", None)
    if setter is not None:
        setter(value)
    else:
        mapping.communicationDirection = value


def _renamed(obj: Any, old: str, new: str) -> Callable[..., Any]:
    """armodel 1.9.4 和 1.9.8 先后改名了部分方法；按对象实际提供的名称返回方法。"""

    method = getattr(obj, old, None) or getattr(obj, new, None)
    if method is None:
        raise AttributeError(f"{type(obj).__name__} has neither {old} nor {new}")
    return method


def get_enable_update(com_spec: Any) -> Any:
    return _renamed(com_spec, "getEnableUpdated", "getEnableUpdate")()


def set_enable_update(com_spec: Any, value: Any) -> None:
    _renamed(com_spec, "setEnableUpdated", "setEnableUpdate")(value)


def get_accessed_variable(access: Any) -> Any:
    return _renamed(access, "getAccessedVariableRef", "getAccessedVariable")()


def set_accessed_variable(access: Any, value: Any) -> None:
    _renamed(access, "setAccessedVariableRef", "setAccessedVariable")(value)


def set_inner_port_iref(connector: Any, iref: Any) -> None:
    _renamed(connector, "setInnerPortIRref", "setInnerPortIRef")(iref)
