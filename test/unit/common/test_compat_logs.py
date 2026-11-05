"""Console logging survives streams whose encoding cannot represent the message."""

from __future__ import annotations

import io
import logging
import sys

import pytest

from table_to_arxml.common.logs import PACKAGE_LOGGER, setup_logging


@pytest.fixture
def restore_logging():
    loggers = [logging.getLogger(PACKAGE_LOGGER), logging.getLogger()]
    saved = [(logger, list(logger.handlers), logger.level, logger.propagate) for logger in loggers]
    yield
    for logger, handlers, level, propagate in saved:
        for handler in list(logger.handlers):
            if handler not in handlers:
                logger.removeHandler(handler)
                handler.close()
        logger.setLevel(level)
        logger.propagate = propagate


@pytest.mark.parametrize("encoding", ["cp1252", "ascii"])
def test_non_utf8_console_escapes_characters_instead_of_dropping_the_record(
        tmp_path, monkeypatch, restore_logging, encoding):
    stdout = io.TextIOWrapper(io.BytesIO(), encoding=encoding, newline="\n")
    stderr = io.TextIOWrapper(io.BytesIO(), encoding=encoding, newline="\n")
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    log_file, errors = setup_logging("tool", tmp_path / "log", "INFO")
    logger = logging.getLogger(f"{PACKAGE_LOGGER}.test")
    logger.info("Parsing ARXML: D:/工程/模型.arxml")
    logger.warning("警告 message")
    logger.error("错误 message")
    stdout.flush()
    stderr.flush()

    out = stdout.buffer.getvalue().decode(encoding)
    err = stderr.buffer.getvalue().decode(encoding)
    assert "Parsing ARXML: D:/\\u5de5\\u7a0b/\\u6a21\\u578b.arxml" in out
    assert "\\u8b66\\u544a message" in err and "\\u9519\\u8bef message" in err
    assert "Logging error" not in err
    assert errors.messages == ["错误 message"]
    assert "D:/工程/模型.arxml" in log_file.read_text(encoding="utf-8")
