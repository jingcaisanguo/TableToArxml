"""SWC 工具对 armodel 的全部用法：加载单个 ARXML、查询已有定义、生成待插入的 XML 片段。

armodel 是两个工具的直接依赖，这里不再抽象解析库；与具体库无关的取值常量放在 :mod:`.constants`，
供工作簿读取和参数解析使用而不必导入 armodel。

armodel 的 ``AUTOSAR`` 是全局单例，每次加载都会清空并使之前的文档失效；文档对象带加载代次，
过期使用会报错。``add_*`` 方法修改过的文档 ``dirty`` 为真，不能再作为未修改的缓存复用。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence, cast

from armodel.models.M2.AUTOSARTemplates.AutosarTopLevelStructure import AUTOSAR
from armodel.models.M2.AUTOSARTemplates.CommonStructure.ImplementationDataTypes import (
    ImplementationDataType,
)
from armodel.models.M2.AUTOSARTemplates.GenericStructure.GeneralTemplateClasses.ARPackage import (
    ARPackage,
)
from armodel.models.M2.AUTOSARTemplates.GenericStructure.GeneralTemplateClasses.PrimitiveTypes import (
    ARBoolean,
    ARFloat,
    ARLiteral,
    ARNumerical,
    Limit,
    RefType,
    TRefType,
)
from armodel.models.M2.AUTOSARTemplates.SWComponentTemplate.Communication import (
    NonqueuedReceiverComSpec,
    NonqueuedSenderComSpec,
    QueuedReceiverComSpec,
    SenderComSpec,
    TransmissionAcknowledgementRequest,
)
from armodel.models.M2.AUTOSARTemplates.SWComponentTemplate.Components import (
    AtomicSwComponentType,
    PPortPrototype,
    RPortPrototype,
    SwComponentType,
)
from armodel.models.M2.AUTOSARTemplates.SWComponentTemplate.PortInterface import (
    ArgumentDataPrototype,
    ClientServerInterface,
    ModeSwitchInterface,
    NvDataInterface,
    ParameterInterface,
    PortInterface,
    SenderReceiverInterface,
    TriggerInterface,
)
from armodel.models.M2.AUTOSARTemplates.SWComponentTemplate.SwcInternalBehavior import (
    RunnableEntity,
    SwcInternalBehavior,
    SynchronousServerCallPoint,
)
from armodel.models.M2.AUTOSARTemplates.SWComponentTemplate.Composition.InstanceRefs import (
    POperationInAtomicSwcInstanceRef,
    ROperationInAtomicSwcInstanceRef,
)
from armodel.models.M2.AUTOSARTemplates.SWComponentTemplate.SwcInternalBehavior.AutosarVariableRef import (
    AutosarVariableRef,
)
from armodel.models.M2.AUTOSARTemplates.SWComponentTemplate.SwcInternalBehavior.InstanceRefsUsage import (
    VariableInAtomicSWCTypeInstanceRef,
)
from armodel.models.M2.MSR.AsamHdo.ComputationMethod import (
    Compu,
    CompuConst,
    CompuConstTextContent,
    CompuMethod,
    CompuScale,
    CompuScaleConstantContents,
    CompuScales,
)
from armodel.models.M2.MSR.AsamHdo.Constraints.GlobalConstraints import (
    DataConstr,
    DataConstrRule,
    PhysConstrs,
)
from armodel.models.M2.MSR.DataDictionary.CalibrationParameter import SwCalprmAxisSet
from armodel.models.M2.MSR.DataDictionary.DataDefProperties import SwDataDefProps
from armodel.writer.arxml_writer import ARXMLWriter

from ..common.armodel_compat import new_parser, reset_document, serialize, strip_handle_timeout_type, to_fragment
from .constants import (
    ACCESS_CONTAINERS,
    ACCESS_DIRECTIONS,
    ACCESS_READ,
    ACCESS_RECEIVE_BY_ARGUMENT,
    ACCESS_RECEIVE_BY_VALUE,
    ACCESS_SEND,
    ACCESS_WRITE,
    CLIENT_SERVER_INTERFACE,
    DIRECTION_PROVIDED,
    DIRECTION_REQUIRED,
    DIRECTION_UNKNOWN,
    MODE_SWITCH_INTERFACE,
    NV_DATA_INTERFACE,
    PARAMETER_INTERFACE,
    PORT_ELEMENTS,
    SENDER_RECEIVER_INTERFACE,
    TRIGGER_INTERFACE,
)

# 把 armodel 的接口 Python 类映射回端口引用 DEST 需要的 AUTOSAR 元类名称。
INTERFACE_DESTS: tuple[tuple[type[PortInterface], str], ...] = (
    (SenderReceiverInterface, SENDER_RECEIVER_INTERFACE),
    (ClientServerInterface, CLIENT_SERVER_INTERFACE),
    (ModeSwitchInterface, MODE_SWITCH_INTERFACE),
    (NvDataInterface, NV_DATA_INTERFACE),
    (ParameterInterface, PARAMETER_INTERFACE),
    (TriggerInterface, TRIGGER_INTERFACE),
)
_load_generation = 0


def load_generation() -> int:
    """当前加载代次；每加载一个文件加一，之前的文档随之失效。"""

    return _load_generation


def load_document(arxml_file: Path) -> tuple["ArxmlDocument", list[str]]:
    """加载单个 ARXML，返回文档对象和解析诊断；之前加载的文档随之失效。"""

    global _load_generation
    _load_generation += 1
    document = reset_document()
    diagnostics: list[str] = []
    new_parser(diagnostics).load(str(arxml_file), document)
    return ArxmlDocument(document, _load_generation), diagnostics


@dataclass(frozen=True)
class PortInfo:
    """一个已存在端口的库无关描述。

    ComSpec 相关字段只反映脚本会生成的那部分内容，其余字段（如 ``INIT-VALUE``、
    ``HANDLE-OUT-OF-RANGE``）不参与比对。``update_elements`` 和 ``queued_elements``
    描述 R-Port 的接收端 ComSpec，``sender_elements`` 描述 P-Port 的发送端 ComSpec，
    其元组依次是数据元素路径、``TRANSMISSION-ACKNOWLEDGE`` 超时（``None`` 表示没有
    该子元素）和 ``USES-END-TO-END-PROTECTION``。
    """

    name: str
    direction: str
    type_name: str
    interface_path: str
    interface_dest: str
    update_elements: tuple[str, ...] = ()
    queued_elements: tuple[tuple[str, int], ...] = ()
    sender_elements: tuple[tuple[str, float | None, bool], ...] = ()


@dataclass(frozen=True)
class AccessInfo:
    """一个已存在 VARIABLE-ACCESS 的库无关描述。"""

    name: str
    port_path: str
    data_path: str


@dataclass(frozen=True)
class OperationPointInfo:
    """一个 server call point 或 operation-invoked event 的引用信息。"""

    name: str
    port_path: str
    operation_path: str
    runnable_path: str = ""
    timeout: float | None = None


@dataclass(frozen=True)
class ImplementationTypeInfo:
    """实现类型自身的属性和成员；引用仅保存路径。"""

    category: str
    base_type_ref: str = ""
    compu_method_ref: str = ""
    data_constr_ref: str = ""
    members: tuple[tuple[str, str, str, str, str], ...] = ()


@dataclass(frozen=True)
class InterfaceSummary:
    """端口接口的元类及数据元素、operation 简称；不支持的接口类型 ``dest`` 为空。"""

    dest: str
    type_name: str
    data_elements: tuple[str, ...] = ()
    operations: tuple[str, ...] = ()


@dataclass(frozen=True)
class DocumentSummary:
    """一个 ARXML 中全部软件组件与端口接口的完整路径。"""

    components: frozenset[str]
    interfaces: dict[str, InterfaceSummary]


class ArxmlDocument:
    """单个 ARXML 文档的查询与修改能力。"""

    # 访问点类别到 armodel ``RunnableEntity`` 读写方法的映射。
    ACCESS_GETTERS = {
        ACCESS_SEND: "getDataSendPoints",
        ACCESS_RECEIVE_BY_ARGUMENT: "getDataReceivePointByArguments",
        ACCESS_RECEIVE_BY_VALUE: "getDataReceivePointByValues",
        ACCESS_READ: "getDataReadAccesses",
        ACCESS_WRITE: "getDataWriteAccesses",
    }
    ACCESS_CREATORS = {
        ACCESS_SEND: "createDataSendPoint",
        ACCESS_RECEIVE_BY_ARGUMENT: "createDataReceivePointByArgument",
        ACCESS_RECEIVE_BY_VALUE: "createDataReceivePointByValue",
        ACCESS_READ: "createDataReadAccess",
        ACCESS_WRITE: "createDataWriteAccess",
    }

    def __init__(self, document: AUTOSAR, generation: int):
        self._model = document
        self._generation = generation
        self.dirty = False

    @property
    def _document(self) -> AUTOSAR:
        if self._generation != _load_generation:
            raise RuntimeError(
                "ARXML document is stale because another file was loaded; reload it before use"
            )
        return self._model

    def summarize(self) -> DocumentSummary:
        components: set[str] = set()
        interfaces: dict[str, InterfaceSummary] = {}
        packages: list[ARPackage] = list(self._document.getARPackages())
        while packages:
            package = packages.pop()
            packages.extend(package.getARPackages())
            for element in package.getElements():
                if isinstance(element, SwComponentType):
                    components.add(element.getFullName())
                elif isinstance(element, PortInterface):
                    interfaces[element.getFullName()] = InterfaceSummary(
                        next((dest for kind, dest in INTERFACE_DESTS if isinstance(element, kind)), ""),
                        type(element).__name__,
                        tuple(item.getShortName() for item in element.getDataElements())
                        if isinstance(element, SenderReceiverInterface) else (),
                        tuple(item.getShortName() for item in element.getOperations())
                        if isinstance(element, ClientServerInterface) else (),
                    )
        return DocumentSummary(frozenset(components), interfaces)

    def interface_dest(self, interface_path: str) -> str | None:
        interface = self._document.find(interface_path)
        if not isinstance(interface, PortInterface):
            return None
        for interface_type, dest in INTERFACE_DESTS:
            if isinstance(interface, interface_type):
                return dest
        raise ValueError(
            f"Unsupported port interface type at {interface_path}: "
            f"{type(interface).__name__}"
        )

    def read_port(self, component_path: str, port_name: str) -> PortInfo | None:
        component = self._require_component(component_path)
        element = component.getElement(port_name)
        if element is None:
            return None

        update_elements: tuple[str, ...] = ()
        queued_elements: tuple[tuple[str, int], ...] = ()
        sender_elements: tuple[tuple[str, float | None, bool], ...] = ()
        if isinstance(element, PPortPrototype):
            direction = DIRECTION_PROVIDED
            reference = element.getProvidedInterfaceTRef()
            sender_elements = self._read_sender_com_specs(element)
        elif isinstance(element, RPortPrototype):
            direction = DIRECTION_REQUIRED
            reference = element.getRequiredInterfaceTRef()
            update_elements, queued_elements = self._read_com_specs(element)
        else:
            return PortInfo(
                name=port_name,
                direction=DIRECTION_UNKNOWN,
                type_name=type(element).__name__,
                interface_path="",
                interface_dest="",
            )

        return PortInfo(
            name=port_name,
            direction=direction,
            type_name=type(element).__name__,
            interface_path="" if reference is None else reference.getValue() or "",
            interface_dest="" if reference is None else reference.getDest() or "",
            update_elements=update_elements,
            queued_elements=queued_elements,
            sender_elements=sender_elements,
        )

    @staticmethod
    def _read_com_specs(
        port: RPortPrototype,
    ) -> tuple[tuple[str, ...], tuple[tuple[str, int], ...]]:
        """读出 R-Port 上开启了数据更新的元素和带队列的元素。"""

        updates: list[str] = []
        queued: list[tuple[str, int]] = []
        for com_spec in port.getRequiredComSpecs():
            reference = getattr(com_spec, "getDataElementRef", lambda: None)()
            data_path = "" if reference is None else reference.getValue() or ""
            if isinstance(com_spec, NonqueuedReceiverComSpec):
                enabled = com_spec.getEnableUpdated()
                if enabled is not None and str(enabled.getValue()).lower() == "true":
                    updates.append(data_path)
            elif isinstance(com_spec, QueuedReceiverComSpec):
                length = com_spec.getQueueLength()
                queued.append((data_path, 0 if length is None else int(length.getValue())))
        return tuple(updates), tuple(queued)

    @staticmethod
    def _read_sender_com_specs(
        port: PPortPrototype,
    ) -> tuple[tuple[str, float | None, bool], ...]:
        """读出 P-Port 上每个发送端 ComSpec 的发送确认超时和 E2E 标志。"""

        senders: list[tuple[str, float | None, bool]] = []
        for com_spec in port.getProvidedComSpecs():
            if not isinstance(com_spec, SenderComSpec):
                continue
            reference = com_spec.getDataElementRef()
            data_path = "" if reference is None else reference.getValue() or ""
            acknowledge = com_spec.getTransmissionAcknowledge()
            timeout = (
                None
                if acknowledge is None or acknowledge.timeout is None
                else float(acknowledge.timeout.getValue())
            )
            protection = com_spec.getUsesEndToEndProtection()
            senders.append(
                (
                    data_path,
                    timeout,
                    protection is not None
                    and str(protection.getValue()).lower() == "true",
                )
            )
        return tuple(senders)

    def add_port(
        self,
        component_path: str,
        port_name: str,
        direction: str,
        interface_path: str,
        interface_dest: str,
        update_elements: Sequence[str] = (),
        queued_elements: Sequence[tuple[str, int]] = (),
        sender_elements: Sequence[tuple[str, float | None, bool]] = (),
    ) -> str:
        component = self._require_component(component_path)
        reference = TRefType().setDest(interface_dest).setValue(interface_path)
        self.dirty = True
        writer = ARXMLWriter()
        if direction == DIRECTION_PROVIDED:
            provided = component.createPPortPrototype(port_name)
            provided.setProvidedInterfaceTRef(reference)
            for data_path, ack_timeout, uses_protection in sender_elements:
                provided.addProvidedComSpec(
                    self._sender_com_spec(data_path, ack_timeout, uses_protection)
                )
            return to_fragment(serialize(writer.writePPortPrototype, provided, "PORTS"))
        if direction == DIRECTION_REQUIRED:
            required = component.createRPortPrototype(port_name)
            required.setRequiredInterfaceTRef(reference)
            for data_path in update_elements:
                required.addRequiredComSpec(self._update_com_spec(data_path))
            for data_path, queue_length in queued_elements:
                required.addRequiredComSpec(
                    self._queued_com_spec(data_path, queue_length)
                )
            node = serialize(writer.writeRPortPrototype, required, "PORTS")
            strip_handle_timeout_type(node)
            return to_fragment(node)
        raise ValueError(f"Unsupported port direction: {direction}")

    @staticmethod
    def _update_com_spec(data_path: str) -> NonqueuedReceiverComSpec:
        """构造仅包含数据元素引用和 ``ENABLE-UPDATE`` 的接收端 ComSpec。"""

        com_spec = NonqueuedReceiverComSpec()
        com_spec.setDataElementRef(
            RefType().setDest("VARIABLE-DATA-PROTOTYPE").setValue(data_path)
        )
        com_spec.setEnableUpdated(ARBoolean().setValue(True))
        # armodel 将缺省值初始化为空字符串并错误地尝试序列化；先提供占位，
        # add_port 输出片段前用 strip_handle_timeout_type 删除这个未由 Excel 指定的标签。
        com_spec.setHandleTimeoutType(ARLiteral().setValue("NONE"))
        return com_spec

    @staticmethod
    def _queued_com_spec(data_path: str, queue_length: int) -> QueuedReceiverComSpec:
        """构造带 ``QUEUE-LENGTH`` 的队列接收端 ComSpec。"""

        com_spec = QueuedReceiverComSpec()
        com_spec.setDataElementRef(
            RefType().setDest("VARIABLE-DATA-PROTOTYPE").setValue(data_path)
        )
        # writer 直接读取内部文本，数值必须以字符串形式写入。
        com_spec.setQueueLength(ARNumerical().setValue(str(queue_length)))
        return com_spec

    @staticmethod
    def _sender_com_spec(
        data_path: str, ack_timeout: float | None, uses_protection: bool
    ) -> NonqueuedSenderComSpec:
        """构造发送端 ComSpec；``ack_timeout`` 为 ``None`` 时不写发送确认子元素。"""

        com_spec = NonqueuedSenderComSpec()
        com_spec.setDataElementRef(
            RefType().setDest("VARIABLE-DATA-PROTOTYPE").setValue(data_path)
        )
        com_spec.setHandleOutOfRange(ARLiteral().setValue("NONE"))
        if ack_timeout is not None:
            acknowledge = TransmissionAcknowledgementRequest()
            # armodel 把 timeout 标注为 float，但读写两侧实际使用的都是 ARFloat。
            acknowledge.timeout = cast(Any, ARFloat().setValue(ack_timeout))
            com_spec.setTransmissionAcknowledge(acknowledge)
        com_spec.setUsesEndToEndProtection(ARBoolean().setValue(uses_protection))
        return com_spec

    def sender_receiver_definition(
        self, interface_path: str
    ) -> list[tuple[str, str, bool]]:
        interface = self._document.find(interface_path)
        if not isinstance(interface, SenderReceiverInterface):
            return []
        definition: list[tuple[str, str, bool]] = []
        for element in interface.getDataElements():
            type_ref = element.getTypeTRef()
            props = element.getSwDataDefProps()
            policy = None if props is None else props.getSwImplPolicy()
            definition.append(
                (
                    element.getShortName(),
                    "" if type_ref is None else type_ref.getValue() or "",
                    policy is not None and policy.getValue() == "QUEUED",
                )
            )
        return definition

    def client_server_definition(
        self, interface_path: str
    ) -> list[tuple[str, list[tuple[str, str, str]]]]:
        interface = self._document.find(interface_path)
        if not isinstance(interface, ClientServerInterface):
            return []
        definition: list[tuple[str, list[tuple[str, str, str]]]] = []
        for operation in interface.getOperations():
            arguments: list[tuple[str, str, str]] = []
            for argument in operation.getArguments():
                type_ref = argument.getTypeTRef()
                direction = argument.getDirection()
                arguments.append(
                    (
                        argument.getShortName(),
                        "" if type_ref is None else type_ref.getValue() or "",
                        "" if direction is None else str(direction.getValue() or ""),
                    )
                )
            definition.append((operation.getShortName(), arguments))
        return definition

    def interface_is_service(self, interface_path: str) -> bool | None:
        interface = self._document.find(interface_path)
        if not isinstance(interface, PortInterface):
            return None
        value = interface.getIsService()
        return value is not None and str(value.getValue()).lower() == "true"

    def internal_behavior_path(self, component_path: str) -> str | None:
        component = self._require_component(component_path)
        if not isinstance(component, AtomicSwComponentType):
            return None
        behavior = component.getInternalBehavior()
        return None if behavior is None else behavior.getFullName()

    def find_runnable(self, component_path: str, runnable_name: str) -> str | None:
        component = self._require_component(component_path)
        if not isinstance(component, AtomicSwComponentType):
            return None
        behavior = component.getInternalBehavior()
        if behavior is None:
            return None
        for runnable in behavior.getRunnableEntities():
            if runnable.getShortName() == runnable_name:
                return runnable.getFullName()
        return None

    def runnable_definition(
        self, runnable_path: str
    ) -> tuple[str, float | None, bool | None] | None:
        runnable = self._document.find(runnable_path)
        if not isinstance(runnable, RunnableEntity):
            return None
        interval = runnable.getMinimumStartInterval()
        concurrent = runnable.getCanBeInvokedConcurrently()
        return (
            self._value_text(runnable.getSymbol()),
            None if interval is None else float(interval.getValue()),
            None if concurrent is None else self._value_text(concurrent).lower() == "true",
        )

    def read_access(
        self, runnable_path: str, access_name: str, kind: str
    ) -> AccessInfo | None:
        runnable = self._require_runnable(runnable_path)
        getter = self.ACCESS_GETTERS.get(kind)
        if getter is None:
            raise ValueError(f"Unsupported access kind: {kind}")
        accesses = getattr(runnable, getter)()
        access = next(
            (item for item in accesses if item.getShortName() == access_name), None
        )
        if access is None:
            return None
        return self._access_info(access)

    def list_accesses(self, runnable_path: str, kind: str) -> list[AccessInfo]:
        runnable = self._require_runnable(runnable_path)
        getter = self.ACCESS_GETTERS.get(kind)
        if getter is None:
            raise ValueError(f"Unsupported access kind: {kind}")
        return [self._access_info(item) for item in getattr(runnable, getter)()]

    @staticmethod
    def _access_info(access: Any) -> AccessInfo:
        accessed = access.getAccessedVariableRef()
        iref = (
            None
            if accessed is None
            else cast(AutosarVariableRef, accessed).getAutosarVariableIRef()
        )
        port_ref = None if iref is None else iref.getPortPrototypeRef()
        data_ref = None if iref is None else iref.getTargetDataPrototypeRef()
        return AccessInfo(
            name=access.getShortName(),
            port_path="" if port_ref is None else port_ref.getValue() or "",
            data_path="" if data_ref is None else data_ref.getValue() or "",
        )

    def add_access(
        self,
        runnable_path: str,
        access_name: str,
        kind: str,
        port_path: str,
        data_path: str,
    ) -> str:
        runnable = self._require_runnable(runnable_path)
        creator = self.ACCESS_CREATORS.get(kind)
        if creator is None:
            raise ValueError(f"Unsupported access kind: {kind}")
        self.dirty = True
        access = getattr(runnable, creator)(access_name)
        port_dest = PORT_ELEMENTS[ACCESS_DIRECTIONS[kind]]

        iref = (
            VariableInAtomicSWCTypeInstanceRef()
            .setPortPrototypeRef(RefType().setDest(port_dest).setValue(port_path))
            .setTargetDataPrototypeRef(
                RefType().setDest("VARIABLE-DATA-PROTOTYPE").setValue(data_path)
            )
        )
        cast(Any, access).setAccessedVariableRef(
            AutosarVariableRef().setAutosarVariableIRef(iref)
        )
        return self._serialize(ARXMLWriter().writeVariableAccess, access, ACCESS_CONTAINERS[kind])

    def read_server_call(
        self, runnable_path: str, call_name: str
    ) -> OperationPointInfo | None:
        runnable = self._require_runnable(runnable_path)
        call = next(
            (item for item in runnable.getServerCallPoints() if item.getShortName() == call_name),
            None,
        )
        if not isinstance(call, SynchronousServerCallPoint):
            return None
        iref = call.getOperationIRef()
        port_ref = None if iref is None else iref.getContextRPortRef()
        operation_ref = None if iref is None else iref.getTargetRequiredOperationRef()
        timeout = cast("ARFloat | float | None", call.getTimeout())
        if isinstance(timeout, ARFloat):
            timeout = timeout.getValue()
        return OperationPointInfo(
            call_name,
            "" if port_ref is None else port_ref.getValue() or "",
            "" if operation_ref is None else operation_ref.getValue() or "",
            runnable_path,
            None if timeout is None else float(timeout),
        )

    def add_server_call(
        self,
        runnable_path: str,
        call_name: str,
        port_path: str,
        operation_path: str,
    ) -> str:
        runnable = self._require_runnable(runnable_path)
        self.dirty = True
        call = runnable.createSynchronousServerCallPoint(call_name)
        call.setOperationIRef(
            ROperationInAtomicSwcInstanceRef()
            .setContextRPortRef(RefType().setDest("R-PORT-PROTOTYPE").setValue(port_path))
            .setTargetRequiredOperationRef(
                RefType().setDest("CLIENT-SERVER-OPERATION").setValue(operation_path)
            )
        )
        call.setTimeout(ARFloat().setValue(0.0))
        return self._serialize(ARXMLWriter().setSynchronousServerCallPoint, call, "SERVER-CALL-POINTS")

    def read_operation_event(
        self, behavior_path: str, event_name: str
    ) -> OperationPointInfo | None:
        behavior = self._require_behavior(behavior_path)
        event = next(
            (item for item in behavior.getOperationInvokedEvents() if item.getShortName() == event_name),
            None,
        )
        if event is None:
            return None
        iref = event.getOperationIRef()
        port_ref = None if iref is None else iref.getContextPPortRef()
        operation_ref = None if iref is None else iref.getTargetProvidedOperationRef()
        runnable_ref = event.getStartOnEventRef()
        return OperationPointInfo(
            event_name,
            "" if port_ref is None else port_ref.getValue() or "",
            "" if operation_ref is None else operation_ref.getValue() or "",
            "" if runnable_ref is None else runnable_ref.getValue() or "",
        )

    def add_runnable(
        self,
        behavior_path: str,
        runnable_name: str,
        symbol: str,
        minimum_start_interval: float,
        can_be_invoked_concurrently: bool,
    ) -> str:
        behavior = self._require_behavior(behavior_path)
        self.dirty = True
        runnable = behavior.createRunnableEntity(runnable_name)
        runnable.setMinimumStartInterval(ARFloat().setValue(minimum_start_interval))
        runnable.setCanBeInvokedConcurrently(
            ARBoolean().setValue(can_be_invoked_concurrently)
        )
        runnable.setSymbol(ARLiteral().setValue(symbol))
        return self._serialize(ARXMLWriter().writeRunnableEntity, runnable)

    def add_operation_event(
        self,
        behavior_path: str,
        event_name: str,
        runnable_path: str,
        port_path: str,
        operation_path: str,
    ) -> str:
        behavior = self._require_behavior(behavior_path)
        self.dirty = True
        event = behavior.createOperationInvokedEvent(event_name)
        event.setStartOnEventRef(
            RefType().setDest("RUNNABLE-ENTITY").setValue(runnable_path)
        )
        event.setOperationIRef(
            POperationInAtomicSwcInstanceRef()
            .setContextPPortRef(RefType().setDest("P-PORT-PROTOTYPE").setValue(port_path))
            .setTargetProvidedOperationRef(
                RefType().setDest("CLIENT-SERVER-OPERATION").setValue(operation_path)
            )
        )
        return self._serialize(ARXMLWriter().writeOperationInvokedEvent, event, "EVENTS")

    def _require_component(self, component_path: str) -> SwComponentType:
        component = self._document.find(component_path)
        if not isinstance(component, SwComponentType):
            raise ValueError(f"Component not found: {component_path}")
        return component

    def package_exists(self, package_path: str) -> bool:
        return isinstance(self._document.find(package_path), ARPackage)

    def element_exists(self, element_path: str) -> bool:
        return self._document.find(element_path) is not None

    @staticmethod
    def _value_text(value: Any) -> str:
        if value is None or value.getValue() is None:
            return ""
        return str(value.getValue())

    def implementation_type_definition(self, type_path: str) -> ImplementationTypeInfo | None:
        data_type = self._document.find(type_path)
        if not isinstance(data_type, ImplementationDataType):
            return None
        props = data_type.getSwDataDefProps()
        members: list[tuple[str, str, str, str, str]] = []
        for element in data_type.getSubElements():
            element_props = element.getSwDataDefProps()
            size = element.getArraySize()
            size_value = None if size is None else float(size.getValue())
            size_text = "" if size_value is None else str(
                int(size_value) if size_value.is_integer() else size_value
            )
            members.append((
                element.getShortName(),
                self._value_text(element.getCategory()),
                "" if element_props is None else self._value_text(
                    element_props.getImplementationDataTypeRef()
                ),
                size_text,
                self._value_text(element.getArraySizeSemantics()),
            ))
        return ImplementationTypeInfo(
            self._value_text(data_type.getCategory()),
            "" if props is None else self._value_text(props.getImplementationDataTypeRef()),
            "" if props is None else self._value_text(props.getCompuMethodRef()),
            "" if props is None else self._value_text(props.getDataConstrRef()),
            tuple(members),
        )

    def compu_method_definition(
        self, compu_method_path: str
    ) -> tuple[str, tuple[tuple[str, str, str], ...]] | None:
        compu_method = self._document.find(compu_method_path)
        if not isinstance(compu_method, CompuMethod):
            return None
        compu = compu_method.getCompuInternalToPhys()
        content = None if compu is None else compu.getCompuContent()
        if not isinstance(content, CompuScales):
            return None
        scales: list[tuple[str, str, str]] = []
        for scale in content.getCompuScales():
            contents = scale.getCompuScaleContents()
            if not isinstance(contents, CompuScaleConstantContents):
                return None
            constant = contents.compuConst.compuConstContentType
            if not isinstance(constant, CompuConstTextContent):
                return None
            scales.append((
                self._value_text(scale.getLowerLimit()),
                self._value_text(scale.getUpperLimit()),
                str(constant.getVt() or ""),
            ))
        return self._value_text(compu_method.getCategory()), tuple(scales)

    def data_constr_definition(self, constr_path: str) -> tuple[tuple[str, str], ...] | None:
        constr = self._document.find(constr_path)
        if not isinstance(constr, DataConstr):
            return None
        limits: list[tuple[str, str]] = []
        for rule in constr.getDataConstrRules():
            phys = rule.physConstrs
            if phys is None:
                return None
            limits.append((
                self._value_text(phys.lower_limit),
                self._value_text(phys.upper_limit),
            ))
        return tuple(limits)

    def add_sr_interface(
        self,
        package_path: str,
        interface_name: str,
        data_elements: Sequence[tuple[str, str, bool]],
        is_service: bool = False,
    ) -> str:
        package = self._require_package(package_path)
        self.dirty = True
        interface = package.createSenderReceiverInterface(interface_name)
        interface.setIsService(ARBoolean().setValue(is_service))
        for element_name, data_type, is_queued in data_elements:
            element = interface.createDataElement(element_name)
            if is_queued:
                props = SwDataDefProps()
                props.setSwCalprmAxisSet(SwCalprmAxisSet())
                props.setSwImplPolicy(ARLiteral().setValue("QUEUED"))
                element.setSwDataDefProps(props)
            element.setTypeTRef(
                TRefType().setDest("IMPLEMENTATION-DATA-TYPE").setValue(data_type)
            )
        return self._serialize(ARXMLWriter().writeSenderReceiverInterface, interface)

    def add_cs_interface(
        self,
        package_path: str,
        interface_name: str,
        operations: Sequence[tuple[str, Sequence[tuple[str, str, str]]]],
        is_service: bool = False,
    ) -> str:
        package = self._require_package(package_path)
        self.dirty = True
        interface = package.createClientServerInterface(interface_name)
        interface.setIsService(ARBoolean().setValue(is_service))
        for operation_name, arguments in operations:
            operation = interface.createOperation(operation_name)
            for argument_name, data_type_ref, direction in arguments:
                argument = cast(
                    ArgumentDataPrototype,
                    operation.createArgumentDataPrototype(argument_name),
                )
                argument.setTypeTRef(
                    TRefType()
                    .setDest("IMPLEMENTATION-DATA-TYPE")
                    .setValue(data_type_ref)
                )
                argument.setDirection(ARLiteral().setValue(direction))
        return self._serialize(ARXMLWriter().writeClientServerInterface, interface)

    def add_compu_method(
        self,
        package_path: str,
        compu_method_name: str,
        values: Sequence[tuple[str, str]],
    ) -> str:
        package = self._require_package(package_path)
        self.dirty = True
        compu_method = package.createCompuMethod(compu_method_name)
        compu_method.setCategory("TEXTTABLE")

        scales = CompuScales()
        for value, label in values:
            # 枚举的每个取值都是一个上下限相同的闭区间。
            scale = CompuScale()
            scale.setLowerLimit(Limit().setValue(value))
            scale.setUpperLimit(Limit().setValue(value))
            constant = CompuConst()
            constant.compuConstContentType = CompuConstTextContent().setVt(
                ARLiteral().setValue(label)
            )
            contents = CompuScaleConstantContents()
            contents.compuConst = constant
            scale.setCompuScaleContents(contents)
            scales.getCompuScales().append(scale)

        compu_method.setCompuInternalToPhys(Compu().setCompuContent(scales))
        return self._serialize(ARXMLWriter().writeCompuMethod, compu_method)

    def add_enum_data_type(
        self,
        package_path: str,
        type_name: str,
        base_type_path: str,
        compu_method_path: str,
        data_constr_path: str,
    ) -> str:
        package = self._require_package(package_path)
        self.dirty = True
        data_type = package.createImplementationDataType(type_name)
        data_type.setCategory("TYPE_REFERENCE")

        props = SwDataDefProps()
        # writer 无条件读取该集合，缺省的 None 会让序列化报错。
        props.setSwCalprmAxisSet(SwCalprmAxisSet())
        props.setCompuMethodRef(
            RefType().setDest("COMPU-METHOD").setValue(compu_method_path)
        )
        props.setDataConstrRef(
            RefType().setDest("DATA-CONSTR").setValue(data_constr_path)
        )
        props.setImplementationDataTypeRef(
            RefType().setDest("IMPLEMENTATION-DATA-TYPE").setValue(base_type_path)
        )
        data_type.setSwDataDefProps(props)
        return self._serialize(ARXMLWriter().writeImplementationDataType, data_type)

    @staticmethod
    def _type_reference_props(type_ref: str) -> SwDataDefProps:
        props = SwDataDefProps()
        props.setSwCalprmAxisSet(SwCalprmAxisSet())
        props.setImplementationDataTypeRef(
            RefType().setDest("IMPLEMENTATION-DATA-TYPE").setValue(type_ref)
        )
        return props

    def add_array_data_type(
        self,
        package_path: str,
        type_name: str,
        element_name: str,
        element_type_ref: str,
        array_size: int,
        array_size_semantics: str = "FIXED-SIZE",
    ) -> str:
        package = self._require_package(package_path)
        self.dirty = True
        data_type = package.createImplementationDataType(type_name)
        data_type.setCategory("ARRAY")
        element = data_type.createImplementationDataTypeElement(element_name)
        element.setCategory("TYPE_REFERENCE")
        element.setArraySize(ARNumerical().setValue(str(array_size)))
        element.setArraySizeSemantics(ARLiteral().setValue(array_size_semantics))
        element.setSwDataDefProps(self._type_reference_props(element_type_ref))
        return self._serialize(ARXMLWriter().writeImplementationDataType, data_type)

    def add_structure_data_type(
        self,
        package_path: str,
        type_name: str,
        members: Sequence[tuple[str, str]],
    ) -> str:
        package = self._require_package(package_path)
        self.dirty = True
        data_type = package.createImplementationDataType(type_name)
        data_type.setCategory("STRUCTURE")
        for member_name, member_type_ref in members:
            element = data_type.createImplementationDataTypeElement(member_name)
            element.setCategory("TYPE_REFERENCE")
            element.setSwDataDefProps(self._type_reference_props(member_type_ref))
        return self._serialize(ARXMLWriter().writeImplementationDataType, data_type)

    def add_data_constr(
        self,
        package_path: str,
        constr_name: str,
        lower_limit: str,
        upper_limit: str,
    ) -> str:
        package = self._require_package(package_path)
        self.dirty = True
        constr = package.createDataConstr(constr_name)
        rule = DataConstrRule()
        phys = PhysConstrs()
        phys.lower_limit = Limit().setValue(lower_limit)
        phys.upper_limit = Limit().setValue(upper_limit)
        rule.physConstrs = phys
        constr.getDataConstrRules().append(rule)
        return self._serialize(ARXMLWriter().writeDataConstr, constr)

    def _require_package(self, package_path: str) -> ARPackage:
        package = self._document.find(package_path)
        if not isinstance(package, ARPackage):
            raise ValueError(f"Package not found: {package_path}")
        return package

    @staticmethod
    def _serialize(write, element: Any, container_tag: str = "ELEMENTS") -> str:
        """用 writer 生成单个元素的 XML 片段；``container_tag`` 为该元素在 ARXML 中的父容器。"""

        return to_fragment(serialize(write, element, container_tag))

    def _require_runnable(self, runnable_path: str) -> RunnableEntity:
        runnable = self._document.find(runnable_path)
        if not isinstance(runnable, RunnableEntity):
            raise ValueError(f"Runnable not found: {runnable_path}")
        return runnable

    def _require_behavior(self, behavior_path: str) -> SwcInternalBehavior:
        behavior = self._document.find(behavior_path)
        if not isinstance(behavior, SwcInternalBehavior):
            raise ValueError(f"Internal behavior not found: {behavior_path}")
        return behavior
