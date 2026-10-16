"""对 openpyxl 私有实现的依赖集中在这里，由 test/contract 中的契约测试覆盖。"""

from __future__ import annotations

from copy import copy, deepcopy


def copy_worksheet(target_sheet, source_sheet) -> None:
    """跨工作簿复制单个页签的内容、单元格样式和工作表格式。

    openpyxl 的 ``Workbook.copy_worksheet`` 只支持同一工作簿内复制，因此这里直接遍历
    ``Worksheet._cells`` 并写入 ``Cell._value``，以保留原始取值类型、超链接和批注。
    """

    for (row_index, column_index), source_cell in source_sheet._cells.items():
        target_cell = target_sheet.cell(row=row_index, column=column_index)
        target_cell._value = source_cell._value
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
