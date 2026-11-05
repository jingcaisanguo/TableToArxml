"""Green and white table readers: understated xlsx dimensions, unreadable files and linked green tables."""

from __future__ import annotations

import os
import re
import zipfile
from pathlib import Path

import pytest
from openpyxl import Workbook

from system_fixtures import write_white_table
from table_to_arxml.system.green_table import collect_green_workbooks, parse_green_workbook
from table_to_arxml.system.white_table import read_composition_input

OLE_SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")
GREEN_HEADER = ["No", "Port", "Module", "", "", "", "", "DataElement", "Component", "port", "Remark"]


def understate_dimensions(path: Path, reference: str) -> None:
    """Rewrite every worksheet ``<dimension>`` like xlsx writers that do not track the used range."""

    with zipfile.ZipFile(path) as source:
        entries = [(info, source.read(info)) for info in source.infolist()]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as target:
        for info, data in entries:
            if info.filename.startswith("xl/worksheets/sheet"):
                data, count = re.subn(rb'<dimension ref="[^"]*"\s*/>', f'<dimension ref="{reference}"/>'.encode(), data)
                assert count == 1
            target.writestr(info, data)


def write_green_table(path: Path, receive_rows: int) -> Path:
    workbook = Workbook()
    receive = workbook.active
    receive.title = "R-Port"
    send = workbook.create_sheet("P-Port")
    for sheet in (receive, send):
        sheet.append(GREEN_HEADER)
    for index in range(receive_rows):
        receive.append(["", f"rIn{index}", "Swc", "", "", "", "", "a", "Peer", f"pOut{index}", ""])
    workbook.save(path)
    workbook.close()
    return path


def test_white_table_with_single_cell_dimension_is_read_completely(tmp_path):
    white = write_white_table(tmp_path, {
        "Components": [["A", "A", "/Pkg/A", "Reference"], ["B", "B", "/Pkg/B", "Reference"]],
    })
    understate_dimensions(white, "A1")
    data = read_composition_input(white)
    assert [row.values["Prototype"] for row in data.tables["Components"]] == ["A", "B"]
    assert data.general["System"] == "/Pkg/Sys"


def test_green_table_rows_beyond_the_declared_dimension_are_read(tmp_path):
    green = write_green_table(tmp_path / "Lamp.xlsx", receive_rows=3)
    understate_dimensions(green, "A1:B2")
    info = parse_green_workbook(green, "Lamp")
    assert [row.app_port for row in info.r_port_rows] == ["rIn0", "rIn1", "rIn2"]
    assert [row.peer_port for row in info.r_port_rows] == ["pOut0", "pOut1", "pOut2"]


@pytest.mark.parametrize("name", ["Lamp.xlsx", "Lamp.xls"])
def test_unreadable_workbooks_are_value_errors_naming_the_file(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(OLE_SIGNATURE + bytes(504))
    for reader in (lambda: parse_green_workbook(path, "Lamp"), lambda: read_composition_input(path)):
        with pytest.raises(ValueError, match="Cannot open .* as an .xlsx workbook") as caught:
            reader()
        assert str(path) in str(caught.value)


def test_linked_green_table_is_named_after_the_link(tmp_path, monkeypatch):
    tables, store = tmp_path / "tables", tmp_path / "store"
    tables.mkdir()
    store.mkdir()
    link, target = tables / "Bcm.xlsx", store / "Bcm_v3.xlsx"
    target.touch()
    try:
        link.symlink_to(target)
    except OSError:
        # Without symlink privileges simulate the link for the path resolution functions.
        link.touch()
        realpath, resolve = os.path.realpath, Path.resolve
        monkeypatch.setattr(os.path, "realpath", lambda path, *args, **kwargs: (
            str(target) if Path(path) == link else realpath(path, *args, **kwargs)))
        monkeypatch.setattr(Path, "resolve", lambda self, *args, **kwargs: (
            target if self == link else resolve(self, *args, **kwargs)))
    assert collect_green_workbooks(["tables/*.xlsx", "store/*.xlsx"], "V", tmp_path) == [("Bcm", link)]
