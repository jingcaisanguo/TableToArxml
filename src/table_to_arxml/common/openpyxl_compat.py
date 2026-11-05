"""对 openpyxl 私有实现的依赖集中在这里，由 test/contract 中的契约测试覆盖。"""

from __future__ import annotations

from copy import copy, deepcopy
import logging
from typing import Mapping

from openpyxl.formula.tokenizer import Tokenizer, TokenizerError
from openpyxl.utils.cell import quote_sheetname

LOGGER = logging.getLogger(__name__)


def _rewrite_sheet_references(formula: str, sheet_names: Mapping[str, str], context: str) -> str:
    names = {name.casefold(): target for name, target in sheet_names.items()}
    try:
        tokens = Tokenizer(formula)
    except TokenizerError as error:
        raise ValueError(f"Cannot rewrite formula in {context}: {error}") from error
    for token in tokens.items:
        if token.type != "OPERAND" or token.subtype != "RANGE" or "!" not in token.value:
            continue
        sheet, _, reference = token.value.rpartition("!")
        if sheet.startswith("'") and sheet.endswith("'"):
            sheet = sheet[1:-1].replace("''", "'")
        if "[" in sheet:
            continue  # External workbook references are not renamed.
        parts = sheet.split(":")
        if all(part.casefold() in names for part in parts):
            target = ":".join(names[part.casefold()] for part in parts)
            token.value = f"{quote_sheetname(target)}!{reference}"
        else:
            LOGGER.warning(
                "Formula in %s references a worksheet not copied into the merged workbook: %s; "
                "reference kept unchanged, verify it in Excel", context, sheet,
            )
    return tokens.render()


def loaded_cells(sheet) -> dict:
    """返回已加载单元格的 ``{(行, 列): Cell}``；按尺寸遍历会为远处带格式的空行创建大量单元格。"""

    return sheet._cells


def copy_worksheet(target_sheet, source_sheet, *, sheet_names: Mapping[str, str] | None = None) -> None:
    """跨工作簿复制单个页签的内容、单元格样式和工作表格式。

    openpyxl 的 ``Workbook.copy_worksheet`` 只支持同一工作簿内复制，因此这里直接遍历
    ``Worksheet._cells`` 并写入 ``Cell._value``，以保留原始取值类型、超链接和批注。
    """

    for (row_index, column_index), source_cell in source_sheet._cells.items():
        target_cell = target_sheet.cell(row=row_index, column=column_index)
        value = source_cell._value
        if sheet_names is not None and source_cell.data_type == "f":
            context = f"{source_sheet.title}!{source_cell.coordinate}"
            if isinstance(value, str):
                value = _rewrite_sheet_references(value, sheet_names, context)
            elif hasattr(value, "text") and isinstance(value.text, str):
                value = copy(value)
                value.text = _rewrite_sheet_references(value.text, sheet_names, context)
            else:
                raise ValueError(f"Cannot rewrite unsupported formula in {context}")
        target_cell._value = value
        target_cell.data_type = source_cell.data_type
        target_cell.font = copy(source_cell.font)
        target_cell.fill = copy(source_cell.fill)
        target_cell.border = copy(source_cell.border)
        target_cell.alignment = copy(source_cell.alignment)
        target_cell.number_format = source_cell.number_format
        target_cell.protection = copy(source_cell.protection)
        target_cell.quotePrefix = source_cell.quotePrefix
        target_cell.pivotButton = source_cell.pivotButton
        if source_cell.hyperlink:
            target_cell._hyperlink = copy(source_cell.hyperlink)
        if source_cell.comment:
            target_cell.comment = copy(source_cell.comment)

    for key, dimension in source_sheet.row_dimensions.items():
        target_sheet.row_dimensions[key] = copy(dimension)
        target_sheet.row_dimensions[key].worksheet = target_sheet
    for key, dimension in source_sheet.column_dimensions.items():
        target_sheet.column_dimensions[key] = copy(dimension)
        target_sheet.column_dimensions[key].worksheet = target_sheet

    for merged_range in source_sheet.merged_cells.ranges:
        target_sheet.merge_cells(str(merged_range))

    target_sheet.sheet_format = copy(source_sheet.sheet_format)
    target_sheet.sheet_properties = copy(source_sheet.sheet_properties)
    target_sheet.page_margins = copy(source_sheet.page_margins)
    target_sheet.page_setup = copy(source_sheet.page_setup)
    target_sheet.print_options = copy(source_sheet.print_options)
    target_sheet.protection = copy(source_sheet.protection)
    target_sheet.views = deepcopy(source_sheet.views)
    target_sheet.freeze_panes = source_sheet.freeze_panes
    target_sheet.auto_filter = deepcopy(source_sheet.auto_filter)
    target_sheet.data_validations = deepcopy(source_sheet.data_validations)
    target_sheet.conditional_formatting = deepcopy(source_sheet.conditional_formatting)
    target_sheet.oddHeader = copy(source_sheet.oddHeader)
    target_sheet.oddFooter = copy(source_sheet.oddFooter)
    target_sheet.evenHeader = copy(source_sheet.evenHeader)
    target_sheet.evenFooter = copy(source_sheet.evenFooter)
    target_sheet.firstHeader = copy(source_sheet.firstHeader)
    target_sheet.firstFooter = copy(source_sheet.firstFooter)
