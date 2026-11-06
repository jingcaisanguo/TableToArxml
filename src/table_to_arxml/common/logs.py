"""两个工具共用的日志配置。

日志同时写入文件和控制台：INFO/DEBUG 走 stdout，WARNING 及以上走 stderr。handler 同时挂在本包
logger 和 root logger 上，因为 armodel 的 writer 直接向 root logger 写日志；本包 logger 不向上传播，
避免同一条记录输出两次。
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path

PACKAGE_LOGGER = "table_to_arxml"
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
LOG_FORMAT = "%(asctime)s %(levelname)s: %(message)s"
# 从 armodel 解析器转发的诊断记录带此属性，照常输出，但不计入失败。
ARMODEL_DIAGNOSTIC = "armodel_parser_diagnostic"
_OWNED = "_table_to_arxml_handler"
_ALWAYS = "_table_to_arxml_always"


class ErrorCollector(logging.Handler):
    """收集本次运行中本工具记录的 ERROR，并统计 WARNING 和 armodel 诊断，供退出码和结论行使用。"""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []
        self.warnings = 0
        self.diagnostics = 0

    @property
    def count(self) -> int:
        return len(self.messages)

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(record, ARMODEL_DIAGNOSTIC, False):
            self.diagnostics += 1
        elif record.levelno >= logging.ERROR:
            self.messages.append(record.getMessage())
        else:
            self.warnings += 1


def _owned(handler: logging.Handler) -> logging.Handler:
    setattr(handler, _OWNED, True)
    return handler


class _LocationFormatter(logging.Formatter):
    """消息关联了表格行时，在末尾追加位置，如 ``[P-Ports!3]``。"""

    def formatMessage(self, record: logging.LogRecord) -> str:
        from .report import expand, location, sources_of

        message = super().formatMessage(record)
        sources = sources_of(record)
        return f"{message} [{location(expand(sources))}]" if sources else message


def _remove_owned_handlers(logger: logging.Logger) -> None:
    # 同一进程多次运行时关闭上次的文件句柄；不动 pytest 等外部添加的 handler。
    for handler in list(logger.handlers):
        if getattr(handler, _OWNED, False):
            logger.removeHandler(handler)
            handler.close()


class _ConsoleHandler(logging.StreamHandler):
    """控制台编码（如管道下的 cp1252/cp936）无法表示的字符转义输出，不丢失整条记录。"""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record) + self.terminator
            try:
                self.stream.write(message)
            except UnicodeEncodeError:
                encoding = getattr(self.stream, "encoding", None) or "ascii"
                self.stream.write(message.encode(encoding, "backslashreplace").decode(encoding))
            self.flush()
        except RecursionError:
            raise
        except Exception:
            self.handleError(record)


def setup_logging(tool_name: str, log_dir: Path, level_name: str) -> tuple[Path, ErrorCollector]:
    """创建带时间戳的日志文件并配置输出，返回日志文件路径和 ERROR 收集器。

    logger 至少放行 WARNING，输出 handler 再按 ``level_name`` 过滤：日志级别设为 ERROR 时 WARNING 不输出，
    但仍计入结论行和结果表。:func:`log_always` 的记录不受级别限制。
    """

    level = getattr(logging, level_name)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{tool_name}_{datetime.now():%Y%m%d_%H%M%S_%f}_{os.getpid()}.log"
    formatter = _LocationFormatter(LOG_FORMAT)

    def visible(record: logging.LogRecord) -> bool:
        return record.levelno >= level or getattr(record, _ALWAYS, False)

    file_handler = logging.FileHandler(log_file, mode="w", encoding="utf-8")
    stdout_handler = _ConsoleHandler(sys.stdout)
    stdout_handler.addFilter(lambda record: record.levelno < logging.WARNING)
    stderr_handler = _ConsoleHandler(sys.stderr)
    stderr_handler.setLevel(logging.WARNING)
    output_handlers = [file_handler, stdout_handler, stderr_handler]
    for handler in output_handlers:
        handler.setFormatter(formatter)
        handler.addFilter(visible)

    package_logger = logging.getLogger(PACKAGE_LOGGER)
    root_logger = logging.getLogger()
    for logger in (package_logger, root_logger):
        _remove_owned_handlers(logger)
        logger.setLevel(min(level, logging.WARNING))
        for handler in output_handlers:
            logger.addHandler(_owned(handler))
    package_logger.propagate = False

    errors = ErrorCollector()
    package_logger.addHandler(_owned(errors))
    package_logger.info("Log file: %s", log_file)
    return log_file, errors


def log_always(logger: logging.Logger, message: str) -> None:
    """以 INFO 输出到日志文件和 stdout，不受 ``--log-level`` 限制；用于脚本依赖的结论行。"""

    record = logger.makeRecord(logger.name, logging.INFO, "(result)", 0, "%s", (message,), None)
    setattr(record, _ALWAYS, True)
    logger.handle(record)
