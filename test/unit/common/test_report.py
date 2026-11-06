"""逐行结果的基础设施：行上下文、文件级消息、稀疏工作表扫描和不受日志级别限制的结论行。"""

from __future__ import annotations

import logging

import pytest
from openpyxl import Workbook
from openpyxl.styles import Font

from table_to_arxml.common import report
from table_to_arxml.common.logs import PACKAGE_LOGGER, log_always, setup_logging


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


def test_explicit_empty_sources_are_not_attributed_to_the_current_row(restore_logging, tmp_path):
    setup_logging("tool", tmp_path / "log", "INFO")
    run_report = report.start()
    logger = logging.getLogger(f"{PACKAGE_LOGGER}.test")
    row = ("", "P-Ports", 2)
    with report.rows([row]):
        logger.warning("row message")
        logger.warning("file message", extra=report.located(()))
    assert run_report.rows[row].messages == [("WARNING", "row message")]
    assert run_report.general == [("WARNING", "file message")]


def test_log_level_error_still_counts_warnings_and_prints_the_result(restore_logging, tmp_path, capsys):
    log_file, errors = setup_logging("tool", tmp_path / "log", "ERROR")
    logger = logging.getLogger(f"{PACKAGE_LOGGER}.test")
    logger.info("hidden info")
    logger.warning("hidden warning")
    log_always(logger, "RESULT: SUCCESS - tool")
    captured = capsys.readouterr()
    assert errors.warnings == 1
    assert "hidden" not in captured.out + captured.err
    assert "RESULT: SUCCESS - tool" in captured.out and "RESULT" not in captured.err
    text = log_file.read_text(encoding="utf-8")
    assert "RESULT: SUCCESS - tool" in text and "hidden" not in text


def test_result_columns_ignore_distant_formatted_cells():
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["SW-C", "Port Name"])
    sheet.append(["A", "p"])
    sheet.cell(1_000_000, 8).font = Font(bold=True)
    assert report.data_rows(sheet) == [2]
    run_report = report.RunReport()
    run_report.record([("", sheet.title, 2)], report.CREATED, "/Pkg/A/p")
    report.append_result_columns(sheet, run_report, "", sheet.title, report.WRITTEN, report.data_rows(sheet))
    assert [sheet.cell(1, column).value for column in range(3, 7)] == list(report.RESULT_HEADERS)
    assert sheet.cell(2, 3).value == "新建"
    workbook.close()
