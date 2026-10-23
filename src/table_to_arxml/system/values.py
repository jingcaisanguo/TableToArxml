"""cli 与库代码共用的取值函数和默认值；不依赖第三方库，可在依赖检查前导入。"""

from __future__ import annotations

import re

DEFAULT_PROTOTYPE_PREFIX = "its"
DEFAULT_E2E_WRAPPER = "E2EWrapper"
DEFAULT_SIGNAL_PATH_TEMPLATE = "/${channel}/Signal/${signal}"
DEFAULT_AR_RELEASE = "R19-11"


def class_destination(element: object) -> str:
    """由 armodel 类名得到 AUTOSAR DEST 字面量。"""

    return re.sub(r"(?<!^)(?=[A-Z])", "-", type(element).__name__).upper()


def norm(value: object) -> str:
    """把 Excel、配置文件读到的值转成去掉首尾空白的字符串；``None`` 为空串。"""

    return "" if value is None else str(value).strip()


def ref_value(reference: object) -> str:
    """读取 armodel 引用或字面量（``RefType``、``ARLiteral`` 等）的值，也接受普通值。"""

    return norm(reference.getValue() if hasattr(reference, "getValue") else reference)
