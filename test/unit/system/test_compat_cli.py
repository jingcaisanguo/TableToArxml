"""System run: temporary-directory cleanup, unreadable workbooks and merge without System generation."""

from __future__ import annotations

import json
import logging
import os
import shutil
from tempfile import TemporaryDirectory

import pytest
from openpyxl import Workbook, load_workbook

from helpers import SYSTEM, run_tool
from system_fixtures import write_config, write_model, write_white_table
from table_to_arxml.common.logs import PACKAGE_LOGGER
from table_to_arxml.system import cli

OLE_SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")  # encrypted/IRM-protected Office files are CFB containers


def test_system_output_cannot_overwrite_selected_green_workbook(tmp_path):
    write_model(tmp_path)
    book = Workbook()
    book.remove(book.active)
    for name in ("R-Port", "P-Port"):
        book.create_sheet(name).append(
            ["ID", "Port", "Module", "", "", "", "", "", "Component", "Port", "Remark"])
    book.create_sheet("Notes").append(["Keep original business notes"])
    source = tmp_path / "TGreenTables.xlsx"
    book.save(source)
    book.close()
    original = source.read_bytes()
    config = write_config(tmp_path, greenTables=[source.name], outputDir=".",
                          componentAliases={"TGreenTables": "/Pkg/Top/A"},
                          general={"Composition": "/Pkg/Top", "System": "/Pkg/Sys",
                                   "EcuInstance": "/Pkg/Ecu", "RootSwCompositionPrototype": "/Pkg/Sys/RootProto"})
    settings = json.loads(config.read_text(encoding="utf-8"))
    del settings["whiteTable"]
    config.write_text(json.dumps(settings), encoding="utf-8")
    result = run_tool(SYSTEM, "--config", config)
    assert result.returncode == 1, result.stderr
    assert "Output path overlaps an input file" in result.stderr
    assert source.read_bytes() == original
    assert not (tmp_path / "TWhiteTable.xlsx").exists()
    assert not (tmp_path / "TSystem.arxml").exists()


@pytest.mark.parametrize("source_name", ["model.arxml", "model.lst", "white.xlsx", "config.json"])
def test_system_outputs_protect_all_input_file_aliases(tmp_path, source_name):
    write_model(tmp_path)
    write_white_table(tmp_path, {"Components": [["A", "A", "/Pkg/A", "Reference"]]})
    config = write_config(tmp_path)
    source = tmp_path / source_name
    original = source.read_bytes()
    output = tmp_path / "out"
    output.mkdir()
    destination = output / "TSystem.arxml"
    try:
        os.link(source, destination)
    except OSError as error:
        pytest.skip(f"Hard links unavailable: {error}")
    result = run_tool(SYSTEM, "--config", config)
    assert result.returncode == 1, result.stderr
    assert "Output path overlaps an input file" in result.stderr
    assert source.read_bytes() == destination.read_bytes() == original


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


def test_temporary_directory_cleanup_failure_keeps_published_artifacts(tmp_path, monkeypatch, capsys,
                                                                       restore_logging):
    write_model(tmp_path)
    write_white_table(tmp_path, {"Components": [["A", "A", "/Pkg/A", "Reference"]]})
    config = write_config(tmp_path)
    leftovers = []

    def locked_cleanup(self):
        # Like the real cleanup: the finalizer is detached before removing the tree fails.
        self._finalizer.detach()
        leftovers.append(self.name)
        raise PermissionError(32, "The process cannot access the file because it is being used", self.name)

    monkeypatch.setattr(TemporaryDirectory, "cleanup", locked_cleanup)
    try:
        assert cli.main(["--config", str(config)]) == 0
    finally:
        monkeypatch.undo()
        for directory in leftovers:
            shutil.rmtree(directory, ignore_errors=True)
    assert (tmp_path / "out" / "TSystem.arxml").is_file()
    error = capsys.readouterr().err
    assert "WARNING: Could not remove temporary directory" in error
    assert "failed" not in error


def test_encrypted_and_legacy_workbooks_are_reported_without_traceback(tmp_path):
    write_model(tmp_path)
    tables = tmp_path / "tables"
    tables.mkdir()
    for name in ("Door.xlsx", "Lamp.xlsx"):
        (tables / name).write_bytes(OLE_SIGNATURE + bytes(504))
    config = write_config(tmp_path, greenTables=["tables/*.xlsx"], general={"Composition": "/Pkg/Top"})
    settings = json.loads(config.read_text(encoding="utf-8"))
    del settings["whiteTable"]
    config.write_text(json.dumps(settings), encoding="utf-8")
    result = run_tool(SYSTEM, "--config", config)
    assert result.returncode == 1, result.stderr
    assert "Traceback" not in result.stderr and "unexpectedly" not in result.stderr
    assert str(tables / "Door.xlsx") in result.stderr and str(tables / "Lamp.xlsx") in result.stderr
    assert "2 input table(s) are invalid" in result.stderr

    (tmp_path / "white.xls").write_bytes(OLE_SIGNATURE + bytes(504))
    result = run_tool(SYSTEM, "--config", write_config(tmp_path, whiteTable="white.xls"))
    assert result.returncode == 1, result.stderr
    assert "Traceback" not in result.stderr and "unexpectedly" not in result.stderr
    assert f"Cannot open {tmp_path / 'white.xls'}" in result.stderr


def test_merge_without_system_generation_does_not_require_the_system(tmp_path):
    list_file = write_model(tmp_path)
    white = write_white_table(tmp_path, {"Components": [["A", "A", "/Pkg/A", "Reference"]]})
    workbook = load_workbook(white)
    for row in workbook["General"].iter_rows(min_row=2):
        if row[0].value == "System":
            row[1].value = "/Pkg/NoSuchSystem"
    workbook.save(white)
    workbook.close()
    replace = run_tool(SYSTEM, "--config", write_config(tmp_path))
    assert replace.returncode == 0, replace.stderr
    shutil.copyfile(tmp_path / "out" / "TSystem.arxml", tmp_path / "TSystem.arxml")
    list_file.write_text("model.arxml\nTSystem.arxml\n", encoding="utf-8")

    merge = run_tool(SYSTEM, "--config", write_config(tmp_path, outputMode="merge"))
    assert merge.returncode == 0, merge.stderr
    assert "NoSuchSystem" not in merge.stderr

    overlap = run_tool(SYSTEM, "--config", write_config(tmp_path, outputMode="merge"), "--output-dir", tmp_path)
    assert overlap.returncode == 1
    assert "overlaps an input file" in overlap.stderr and "--in-place" not in overlap.stderr
