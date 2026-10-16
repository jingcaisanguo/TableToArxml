"""Reusable SWC input model and workbook helpers."""

from __future__ import annotations

import subprocess
from openpyxl import load_workbook
from helpers import ROOT, SWC, run_tool

COMPONENT = """        <APPLICATION-SW-COMPONENT-TYPE>
          <SHORT-NAME>{name}</SHORT-NAME>
          <PORTS></PORTS>
          <INTERNAL-BEHAVIORS>
            <SWC-INTERNAL-BEHAVIOR>
              <SHORT-NAME>IB</SHORT-NAME>
              <RUNNABLES>
                <RUNNABLE-ENTITY>
                  <SHORT-NAME>Run</SHORT-NAME>
                  <MINIMUM-START-INTERVAL>0.0</MINIMUM-START-INTERVAL>
                  <CAN-BE-INVOKED-CONCURRENTLY>false</CAN-BE-INVOKED-CONCURRENTLY>
                  <DATA-SEND-POINTS></DATA-SEND-POINTS>
                  <DATA-READ-ACCESSS></DATA-READ-ACCESSS>
                  <DATA-WRITE-ACCESSS></DATA-WRITE-ACCESSS>
                  <SYMBOL>{name}_Run</SYMBOL>
                </RUNNABLE-ENTITY>
              </RUNNABLES>
            </SWC-INTERNAL-BEHAVIOR>
          </INTERNAL-BEHAVIORS>
        </APPLICATION-SW-COMPONENT-TYPE>
"""


TWINS = """<?xml version="1.0" encoding="UTF-8"?>
<AUTOSAR xmlns="http://autosar.org/schema/r4.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://autosar.org/schema/r4.0 AUTOSAR_4-3-0.xsd">
  <AR-PACKAGES>
    <AR-PACKAGE>
      <SHORT-NAME>Pkg</SHORT-NAME>
      <ELEMENTS>
        <SENDER-RECEIVER-INTERFACE>
          <SHORT-NAME>If</SHORT-NAME>
          <IS-SERVICE>false</IS-SERVICE>
          <DATA-ELEMENTS>
            <VARIABLE-DATA-PROTOTYPE>
              <SHORT-NAME>If</SHORT-NAME>
              <TYPE-TREF DEST="IMPLEMENTATION-DATA-TYPE">/Pkg/uint8</TYPE-TREF>
            </VARIABLE-DATA-PROTOTYPE>
          </DATA-ELEMENTS>
        </SENDER-RECEIVER-INTERFACE>
        <CLIENT-SERVER-INTERFACE>
          <SHORT-NAME>CsIf</SHORT-NAME>
          <IS-SERVICE>false</IS-SERVICE>
          <OPERATIONS>
            <CLIENT-SERVER-OPERATION>
              <SHORT-NAME>Op</SHORT-NAME>
            </CLIENT-SERVER-OPERATION>
          </OPERATIONS>
        </CLIENT-SERVER-INTERFACE>
""" + COMPONENT.format(name="A") + COMPONENT.format(name="B") + """      </ELEMENTS>
    </AR-PACKAGE>
  </AR-PACKAGES>
</AUTOSAR>
"""


def write_twins(
    tmp_path, rows: dict[str, list[dict[str, str]]], *, minimal: bool = False
) -> None:
    """在 TWINS 模型上按 ``rows`` 填写工作簿并运行 SWC 工具；键名中的空格和连字符写成下划线。"""

    (tmp_path / "twins.arxml").write_text(TWINS, encoding="utf-8")
    (tmp_path / "arxml.lst").write_text("twins.arxml\n", encoding="utf-8")
    workbook = load_workbook(ROOT / "test/samples/swc/SwcTestPorts.xlsx")
    for sheet in workbook.worksheets[1:]:
        sheet.delete_rows(2, sheet.max_row)
    if minimal:
        for sheet in list(workbook):
            if sheet.title not in rows:
                workbook.remove(sheet)
    for sheet_name, sheet_rows in rows.items():
        sheet = workbook[sheet_name]
        for values in sheet_rows:
            sheet.append([
                values.get(str(cell.value).replace(" ", "_").replace("-", "_")) for cell in sheet[1]
            ])
    workbook.save(tmp_path / "ports.xlsx")
    workbook.close()

def run_twins(tmp_path, rows, *, minimal=False) -> subprocess.CompletedProcess:
    write_twins(tmp_path, rows, minimal=minimal)
    return run_prepared_twins(tmp_path)


def run_prepared_twins(tmp_path) -> subprocess.CompletedProcess:
    return run_tool(
        SWC, "--project-root", tmp_path, "--arxml-file-list", tmp_path / "arxml.lst",
        "--workbook", tmp_path / "ports.xlsx", "--log-dir", tmp_path / "log",
        "--output-dir", tmp_path / "out",
    )
