"""cli 与库代码共用的取值函数和默认值；不依赖第三方库，可在依赖检查前导入。"""

from __future__ import annotations

import re

DEFAULT_PROTOTYPE_PREFIX = "its"
DEFAULT_E2E_WRAPPER = "E2EWrapper"
DEFAULT_SIGNAL_PATH_TEMPLATE = "/${channel}/Signal/${signal}"
DEFAULT_AR_RELEASE = "R19-11"
# AUTOSAR XML 名称把连续大写缩写视为一个词（PRPortPrototype -> PR-PORT-PROTOTYPE），数字单独成词（J1939Cluster -> J-1939-CLUSTER）。
_DEST_WORD_BOUNDARY = re.compile(
    r"(?<=[a-z])(?=[A-Z])|(?<=[A-Za-z])(?=[0-9])|(?<=[0-9])(?=[A-Za-z])|(?<=[A-Z])(?=[A-Z][a-z])")
# 类名无法按词边界还原 XML 名称的少数类。
_DEST_OVERRIDES = {
    "SomeipSdClientEventGroupTimingConfig": "SOME-IP-SD-CLIENT-EVENT-GROUP-TIMING-CONFIG",
    "SomeipSdClientServiceInstanceConfig": "SOME-IP-SD-CLIENT-SERVICE-INSTANCE-CONFIG",
    "SomeipSdServerEventGroupTimingConfig": "SOME-IP-SD-SERVER-EVENT-GROUP-TIMING-CONFIG",
}


def class_destination(element: object) -> str:
    """由 armodel 类名得到 AUTOSAR DEST 字面量。"""

    name = type(element).__name__
    return _DEST_OVERRIDES.get(name) or _DEST_WORD_BOUNDARY.sub("-", name).upper()


def norm(value: object) -> str:
    """把 Excel、配置文件读到的值转成去掉首尾空白的字符串；``None`` 为空串。"""

    return "" if value is None else str(value).strip()


def ref_value(reference: object) -> str:
    """读取 armodel 引用或字面量（``RefType``、``ARLiteral`` 等）的值，也接受普通值。"""

    return norm(reference.getValue() if hasattr(reference, "getValue") else reference)
