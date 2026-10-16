"""日志与异常消息的文本规范化。"""

from __future__ import annotations

import re

# armodel 的 warning 模式会在消息中插入 colorama 的 ANSI 颜色码。
ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def clean_message(message: object) -> str:
    """把异常或日志消息规范化为无 ANSI 控制码的单行文本。"""

    single_line = " ".join(str(message).splitlines())
    return ANSI_ESCAPE_PATTERN.sub("", single_line)
