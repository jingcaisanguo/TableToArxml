"""生成 System 工具的绿表空白模板；列位置与读取规则（:mod:`table_to_arxml.system.green_table`）一致。"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.hyperlink import Hyperlink

from table_to_arxml.system.green_table import (
    GREEN_COLUMN_COUNT,
    GREEN_REQUIRED_HEADERS,
    P_PORT_SHEET,
    R_PORT_SHEET,
    RESERVED_REMARK_KEYWORDS,
)

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "docs" / "user" / "system" / "GreenTableTemplate.xlsx"
USED = "1F4E78"
UNUSED = "7F8C8D"
RESERVED = "、".join(RESERVED_REMARK_KEYWORDS)


@dataclass(frozen=True)
class Column:
    header: str
    hint: str
    width: int
    used: bool = True


def columns(direction: str) -> tuple[Column, ...]:
    receive = direction == R_PORT_SHEET
    own = "接收端口 R-port" if receive else "发送端口 P-port"
    peer = "上游" if receive else "下游"
    peer_port = "P-port" if receive else "R-port"
    return (
        Column("No.", "序号，不参与转换。", 8, False),
        Column(f"{direction} 本模块{own}", f"必填：当前模块（文件名）的{own} SHORT-NAME。", 34),
        Column(f"{peer}模块 / CAN / LIN", f"CAN、LIN（不区分大小写）表示总线信号映射；其他值按普通{peer}组件连接。", 22),
        Column("数据类型", "不参与转换，可记录业务信息。", 14, False),
        Column("说明", "不参与转换。", 28, False),
        Column("详细说明", "不参与转换。", 28, False),
        Column("备注", "不参与转换。", 20, False),
        Column("DataElement（仅 CAN/LIN）", "总线映射使用的数据元素名；留空时取 B 列端口名。普通组件连接不使用本列。", 26),
        Column(
            f"{'From' if receive else 'To'} Component / 通道",
            f"普通连接：{peer}组件名（组件类型短名、prototype 名或 componentAliases 别名）；"
            "总线映射：物理通道短名，多个通道用 # 分隔。I、J 都为空的行跳过。", 30),
        Column(
            f"{'From' if receive else 'To'} Port / 信号",
            f"普通连接：{peer}组件的 {peer_port} SHORT-NAME；总线映射：信号名或以 / 开头的完整 SystemSignal 路径。", 34),
        Column(
            "Remark（预留说明）",
            f"I、J 只填一列时，备注含“{RESERVED}”之一视为预留行并跳过，否则报错。", 28),
    )


def check_layout() -> None:
    for direction in (R_PORT_SHEET, P_PORT_SHEET):
        headers = [column.header for column in columns(direction)]
        if len(headers) != GREEN_COLUMN_COUNT:
            raise ValueError(f"{direction}: expected {GREEN_COLUMN_COUNT} columns")
        for index, keyword in GREEN_REQUIRED_HEADERS.items():
            if not headers[index] or keyword not in headers[index].lower():
                raise ValueError(f"{direction}: header {headers[index]!r} does not satisfy the reader check")


README = (
    ("绿表模板", "System 工具的绿表空白模板：另存为 <模块名>.xlsx（文件名即组件名，须是 AUTOSAR SHORT-NAME），"
                "再在配置 greenTables 中选中。"),
    ("页签", f"{R_PORT_SHEET} 页填写本模块的接收端口，{P_PORT_SHEET} 页填写发送端口；两页都必须存在，可以只有表头。"
            "其他页签（包括本页）不参与转换。"),
    ("列位置", "工具按固定列位置读取，只使用 B、C、H、I、J、K 列（深蓝表头）；灰色表头列不参与转换，"
              "可以改名或填写业务信息，但不能插入或删除列。表头第 1 行只校验 B、C 非空，I 含 Component、J 含 Port。"),
    ("普通连接", "C 列不是 CAN/LIN 时，按 I/J 生成组件连接：R-Port 页为 I.J -> 本模块.B，P-Port 页为 本模块.B -> I.J。"
                "两个模块都有绿表时，同一连接应在提供方 P-Port 页和请求方 R-Port 页各写一次；只写一侧会在结果表中提示。"),
    ("总线映射", "C 列为 CAN/LIN 时，按 I（通道）和 J（信号）生成 SenderReceiverToSignal 映射，H 为数据元素名。"),
    ("跳过与预留", f"B、C、I、J 全空或 I、J 都为空的行跳过；I、J 只填一列且 K 含“{RESERVED}”之一时视为预留行并跳过，否则报错。"),
    ("结果确认", "每次运行在 logDir 生成 *_result.xlsx：每张绿表一页，右侧追加结果（新建/复用/错误等）、生成的对象和说明。"),
)


def create_workbook() -> Workbook:
    check_layout()
    workbook = Workbook()
    readme = workbook.active
    readme.title = "README"
    readme.column_dimensions["A"].width = 16
    readme.column_dimensions["B"].width = 110
    readme.sheet_view.showGridLines = False
    for title, text in README:
        readme.append([title, text])
    for row in readme.iter_rows():
        row[0].font = Font(bold=True, color="243238")
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            cell.fill = PatternFill("solid", fgColor="EAF0F5")
        readme.row_dimensions[row[0].row].height = 36
    for index, direction in enumerate((R_PORT_SHEET, P_PORT_SHEET), start=1):
        link = readme.cell(len(README) + index + 1, 1, f"→ {direction}")
        link.hyperlink = Hyperlink(ref=link.coordinate, location=f"'{direction}'!A1")
        link.font = Font(color="0563C1", underline="single")

        sheet = workbook.create_sheet(direction)
        sheet.freeze_panes = "C2"
        sheet.sheet_properties.tabColor = "B36B18"
        sheet.row_dimensions[1].height = 48
        for column_index, column in enumerate(columns(direction), start=1):
            cell = sheet.cell(1, column_index, column.header)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor=USED if column.used else UNUSED)
            cell.alignment = Alignment(wrap_text=True, horizontal="center", vertical="center")
            cell.comment = Comment(column.hint, "TableToArxml")
            cell.comment.width = 320
            cell.comment.height = 120
            sheet.column_dimensions[cell.column_letter].width = column.width
            for row in range(2, 201):
                sheet.cell(row, column_index).number_format = "@"
    return workbook


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, help="验证用输出目录；不指定时更新仓库内模板")
    args = parser.parse_args()
    target = TARGET if args.output_dir is None else args.output_dir / TARGET.name
    target.parent.mkdir(parents=True, exist_ok=True)
    workbook = create_workbook()
    try:
        workbook.save(target)
    finally:
        workbook.close()
    print(target)


if __name__ == "__main__":
    main()
