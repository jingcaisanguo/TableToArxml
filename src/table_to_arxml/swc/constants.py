"""SWC 工具与解析库无关的取值常量；工作簿读取和参数解析只依赖本模块，不导入 armodel。"""

from __future__ import annotations

# 端口方向；``UNKNOWN`` 表示同名元素不是端口原型。
DIRECTION_PROVIDED = "P"
DIRECTION_REQUIRED = "R"
DIRECTION_UNKNOWN = "?"
DIRECTION_LABELS = {DIRECTION_PROVIDED: "P-Port", DIRECTION_REQUIRED: "R-Port"}
PORT_ELEMENTS = {DIRECTION_PROVIDED: "P-PORT-PROTOTYPE", DIRECTION_REQUIRED: "R-PORT-PROTOTYPE"}

# 端口接口的 AUTOSAR 元类名称，即引用中的 ``DEST``。
SENDER_RECEIVER_INTERFACE = "SENDER-RECEIVER-INTERFACE"
CLIENT_SERVER_INTERFACE = "CLIENT-SERVER-INTERFACE"
MODE_SWITCH_INTERFACE = "MODE-SWITCH-INTERFACE"
NV_DATA_INTERFACE = "NV-DATA-INTERFACE"
PARAMETER_INTERFACE = "PARAMETER-INTERFACE"
TRIGGER_INTERFACE = "TRIGGER-INTERFACE"

# 数据访问点类别；取值只在工具内部使用，工作簿中的写法见 ``workbook.ACCESS_KIND_LABELS``。
ACCESS_SEND = "send"
ACCESS_RECEIVE_BY_ARGUMENT = "receiveByArgument"
ACCESS_RECEIVE_BY_VALUE = "receiveByValue"
ACCESS_READ = "read"
ACCESS_WRITE = "write"

# 每种访问点所在的 runnable 子容器（AUTOSAR 标签名中的 ``ACCESSS`` 为原文拼写）。
ACCESS_CONTAINERS = {
    ACCESS_SEND: "DATA-SEND-POINTS",
    ACCESS_RECEIVE_BY_ARGUMENT: "DATA-RECEIVE-POINT-BY-ARGUMENTS",
    ACCESS_RECEIVE_BY_VALUE: "DATA-RECEIVE-POINT-BY-VALUES",
    ACCESS_READ: "DATA-READ-ACCESSS",
    ACCESS_WRITE: "DATA-WRITE-ACCESSS",
}

# 每种访问点所要求的端口方向。
ACCESS_DIRECTIONS = {
    ACCESS_SEND: DIRECTION_PROVIDED,
    ACCESS_RECEIVE_BY_ARGUMENT: DIRECTION_REQUIRED,
    ACCESS_RECEIVE_BY_VALUE: DIRECTION_REQUIRED,
    ACCESS_READ: DIRECTION_REQUIRED,
    ACCESS_WRITE: DIRECTION_PROVIDED,
}

# ``Access Kind`` 留空时按端口方向取的默认类别。
DEFAULT_ACCESS_KINDS = {DIRECTION_PROVIDED: ACCESS_SEND, DIRECTION_REQUIRED: ACCESS_RECEIVE_BY_ARGUMENT}

# C99/C11 关键字：runnable 的 ``SYMBOL`` 是 RTE 生成的 C 函数名，使用关键字只记录 WARNING（浅校验，由 EB 校验）。
C_KEYWORDS = frozenset((
    "auto", "break", "case", "char", "const", "continue", "default", "do", "double", "else", "enum",
    "extern", "float", "for", "goto", "if", "inline", "int", "long", "register", "restrict", "return",
    "short", "signed", "sizeof", "static", "struct", "switch", "typedef", "union", "unsigned", "void",
    "volatile", "while", "_Alignas", "_Alignof", "_Atomic", "_Bool", "_Complex", "_Generic",
    "_Imaginary", "_Noreturn", "_Static_assert", "_Thread_local",
))
