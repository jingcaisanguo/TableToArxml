"""日志配置、ERROR 收集与依赖版本范围。"""

from __future__ import annotations

import logging

import pytest

from helpers import ROOT
from table_to_arxml.common import dependencies
from table_to_arxml.common.logs import ARMODEL_DIAGNOSTIC, PACKAGE_LOGGER, setup_logging


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


def test_streams_file_and_error_collection(tmp_path, capsys, restore_logging):
    log_file, errors = setup_logging("tool", tmp_path / "log", "INFO")
    logger = logging.getLogger(f"{PACKAGE_LOGGER}.test")
    logger.info("info message")
    logger.warning("warning message")
    logger.error("error message")
    diagnostic = logging.makeLogRecord({"name": logger.name, "levelno": logging.ERROR, "levelname": "ERROR",
                                        "msg": "parser message", ARMODEL_DIAGNOSTIC: True})
    logger.handle(diagnostic)
    logging.getLogger().warning("third-party message")

    captured = capsys.readouterr()
    assert "info message" in captured.out and "warning message" not in captured.out
    assert "warning message" in captured.err and "error message" in captured.err
    assert "third-party message" in captured.err
    assert errors.messages == ["error message"]
    text = log_file.read_text(encoding="utf-8")
    assert all(message in text for message in ("info message", "parser message", "third-party message"))
    assert log_file.name.startswith("tool_")


def test_repeated_setup_replaces_own_handlers(tmp_path, restore_logging):
    setup_logging("tool", tmp_path / "log", "INFO")
    count = len(logging.getLogger(PACKAGE_LOGGER).handlers)
    setup_logging("tool", tmp_path / "log", "DEBUG")
    assert len(logging.getLogger(PACKAGE_LOGGER).handlers) == count
    assert logging.getLogger(PACKAGE_LOGGER).level == logging.DEBUG


def test_armodel_requirement_matches_requirements_txt():
    lines = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    assert f"armodel{dependencies.ARMODEL_REQUIREMENT}" in lines


def test_dependency_check(monkeypatch):
    dependencies.check_dependencies()
    monkeypatch.setattr(dependencies, "version", lambda name: "1.9.8")
    with pytest.raises(RuntimeError, match=r"armodel>=1\.8\.7,<1\.9\.4 is required; installed: 1\.9\.8"):
        dependencies.check_dependencies()
