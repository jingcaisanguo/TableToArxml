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


class ErrorCollector(logging.Handler):
    """收集本次运行中本工具记录的 ERROR，供退出码和写入保护使用。"""

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.messages: list[str] = []

    @property
    def count(self) -> int:
        return len(self.messages)

    def emit(self, record: logging.LogRecord) -> None:
        if not getattr(record, ARMODEL_DIAGNOSTIC, False):
            self.messages.append(record.getMessage())


def _owned(handler: logging.Handler) -> logging.Handler:
    setattr(handler, _OWNED, True)
    return handler


def _remove_owned_handlers(logger: logging.Logger) -> None:
    # 同一进程多次运行时关闭上次的文件句柄；不动 pytest 等外部添加的 handler。
    for handler in list(logger.handlers):
        if getattr(handler, _OWNED, False):
            logger.removeHandler(handler)
            handler.close()


def setup_logging(tool_name: str, log_dir: Path, level_name: str) -> tuple[Path, ErrorCollector]:
    """创建带时间戳的日志文件并配置输出，返回日志文件路径和 ERROR 收集器。"""

    level = getattr(logging, level_name)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{tool_name}_{datetime.now():%Y%m%d_%H%M%S_%f}_{os.getpid()}.log"
    formatter = logging.Formatter(LOG_FORMAT)

    file_handler = logging.FileHandler(log_file, mode="w", encoding="utf-8")
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.addFilter(lambda record: record.levelno < logging.WARNING)
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.WARNING)
    output_handlers = [file_handler, stdout_handler, stderr_handler]
    for handler in output_handlers:
        handler.setFormatter(formatter)

    package_logger = logging.getLogger(PACKAGE_LOGGER)
    root_logger = logging.getLogger()
    for logger in (package_logger, root_logger):
        _remove_owned_handlers(logger)
        logger.setLevel(level)
        for handler in output_handlers:
            logger.addHandler(_owned(handler))
    package_logger.propagate = False

    errors = ErrorCollector()
    package_logger.addHandler(_owned(errors))
    package_logger.info("Log file: %s", log_file)
    return log_file, errors
