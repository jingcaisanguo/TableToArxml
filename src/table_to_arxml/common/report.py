"""本次运行的逐行结果：把处理结果和日志关联到输入表格的行，写成结果工作簿。

日志调用以 ``extra=located(sources)`` 声明消息对应的表格行；处理步骤用 :func:`record` 记录每行的结果。
结果只用于报告，不影响退出码和产物发布；结果工作簿写入失败只记录 WARNING。
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Iterator, Sequence, TypeVar

from .messages import clean_message

LOGGER = logging.getLogger(__name__)
T = TypeVar("T")

# (工作簿, 工作表, Excel 行号)；工作簿为空串表示 SWC 的唯一输入工作簿，行号 0 表示整张工作表。
Source = tuple[str, str, int]
SOURCES = "table_to_arxml_sources"
_HANDLER = "_table_to_arxml_report_handler"
# 正在处理的表格行；日志没有显式指定来源时使用它。
_ROWS: ContextVar[tuple[Source, ...]] = ContextVar("table_to_arxml_rows", default=())

CREATED = "created"
EXISTING = "existing"
REUSED = "reused"
MERGED = "merged"
REFERENCED = "referenced"
LOCATED = "located"
RESERVED = "reserved"
EMPTY = "empty"
SKIPPED = "skipped"
PRIOR = "prior"
HINT = "提示"

# 运行结果：written 已写出或发布；dry-run 只校验；failed 有错误，未写出。
WRITTEN = "written"
DRY_RUN = "dry-run"
FAILED = "failed"

_CREATED_LABELS = {
    WRITTEN: "新建",
    DRY_RUN: "将新建（预演，未写出）",
    FAILED: "可新建（本次有错误，未写出）",
}
_LABELS = {
    EXISTING: "已存在且一致，未修改",
    REUSED: "复用已有对象",
    MERGED: "重复声明，已合并",
    REFERENCED: "引用已有实例",
    LOCATED: "已定位",
    RESERVED: "预留行，跳过",
    EMPTY: "未填写连接，跳过",
    SKIPPED: "按配置跳过",
    PRIOR: "依赖的对象有错误，未处理",
}
RESULT_HEADERS = ("结果", "对象", "目标文件", "说明")
_MAX_CELL = 32000


def created_label(outcome: str) -> str:
    return _CREATED_LABELS[outcome]


def describe(source: Source) -> str:
    book, sheet, row = source
    name = Path(book).name if book else ""
    place = f"{sheet}!{row}" if row else sheet
    return f"{name} {place}" if name else place


def location(sources: Iterable[Source]) -> str:
    return ", ".join(dict.fromkeys(describe(source) for source in sources))


def located(sources: Iterable[Source]) -> dict[str, tuple[Source, ...]]:
    """日志 ``extra`` 参数：把消息关联到表格行。"""

    return {SOURCES: tuple(sources)}


def sources_of(record: logging.LogRecord) -> tuple[Source, ...]:
    """日志记录关联的表格行：显式 ``extra`` 优先；WARNING 及以上取正在处理的行。"""

    explicit = getattr(record, SOURCES, ())
    if explicit or record.levelno < logging.WARNING:
        return explicit
    return _ROWS.get()


def current_rows() -> tuple[Source, ...]:
    """正在处理的表格行。"""

    return _ROWS.get()


@contextmanager
def rows(sources: Iterable[Source]) -> Iterator[None]:
    """在此范围内记录的日志默认关联到 ``sources``。"""

    token = _ROWS.set(tuple(sources))
    try:
        yield
    finally:
        _ROWS.reset(token)


def each(items: Iterable[T], sources: Callable[[T], Iterable[Source]]) -> Iterator[T]:
    """逐个产出 ``items``，循环体内的日志默认关联到该项的表格行。"""

    for item in items:
        with rows(sources(item)):
            yield item


@dataclass
class RowResult:
    statuses: list[tuple[str, str]] = field(default_factory=list)
    objects: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    messages: list[tuple[str, str]] = field(default_factory=list)

    @property
    def has_error(self) -> bool:
        return any(level == "ERROR" for level, _ in self.messages)

    def status(self, outcome: str) -> str:
        if self.has_error:
            return "错误"
        labels = [
            _CREATED_LABELS[outcome] if status == CREATED else _LABELS.get(status, status)
            for status, _detail in self.statuses
        ]
        text = "、".join(dict.fromkeys(labels)) or "未生成"
        if any(level == "WARNING" for level, _ in self.messages):
            text += "（有警告）"
        return text

    def notes(self) -> str:
        lines = [detail for _status, detail in self.statuses if detail]
        lines += [f"{level}: {text}" for level, text in self.messages]
        return "\n".join(dict.fromkeys(lines))


@dataclass(frozen=True)
class ObjectResult:
    status: str
    path: str
    file: str
    sources: tuple[Source, ...]


class RunReport:
    def __init__(self) -> None:
        self.rows: dict[Source, RowResult] = {}
        self.objects: list[ObjectResult] = []
        self.general: list[tuple[str, str]] = []

    def row(self, source: Source) -> RowResult:
        return self.rows.setdefault(source, RowResult())

    def record(
        self, sources: Iterable[Source], status: str, path: str = "", file: object = "", detail: str = "",
    ) -> None:
        sources = tuple(sources)
        file_text = str(file) if file else ""
        for source in sources:
            row = self.row(source)
            if (status, detail) not in row.statuses:
                row.statuses.append((status, detail))
            if path and path not in row.objects:
                row.objects.append(path)
            if file_text and file_text not in row.files:
                row.files.append(file_text)
        if path:
            self.objects.append(ObjectResult(status, path, file_text, sources))

    def note(self, sources: Iterable[Source], level: str, message: str) -> None:
        sources = tuple(sources)
        if not sources:
            self.general.append((level, message))
        for source in sources:
            entry = (level, message)
            row = self.row(source)
            if entry not in row.messages:
                row.messages.append(entry)

    def rows_of(self, book: str, sheet: str) -> dict[int, RowResult]:
        return {row: result for (row_book, row_sheet, row), result in self.rows.items()
                if row_book == book and row_sheet == sheet and row}

    def has_errors(self, sources: Iterable[Source]) -> bool:
        return any(source in self.rows and self.rows[source].has_error for source in sources)


class _RowMessages(logging.Handler):
    def __init__(self, run_report: RunReport) -> None:
        super().__init__(level=logging.WARNING)
        self.run_report = run_report
        setattr(self, _HANDLER, True)

    def emit(self, record: logging.LogRecord) -> None:
        from .logs import ARMODEL_DIAGNOSTIC

        level = f"armodel {record.levelname}" if getattr(record, ARMODEL_DIAGNOSTIC, False) else record.levelname
        self.run_report.note(sources_of(record), level, clean_message(record.getMessage()))


_current: RunReport | None = None


def start() -> RunReport:
    """开始新的运行报告，并接收工具 logger 的 WARNING/ERROR。"""

    global _current
    from .logs import PACKAGE_LOGGER

    logger = logging.getLogger(PACKAGE_LOGGER)
    for handler in list(logger.handlers):
        if getattr(handler, _HANDLER, False):
            logger.removeHandler(handler)
    _current = RunReport()
    logger.addHandler(_RowMessages(_current))
    return _current


def current() -> RunReport | None:
    return _current


def record(
    sources: Iterable[Source], status: str, path: str = "", file: object = "", detail: str = "",
) -> None:
    if _current is not None:
        _current.record(sources, status, path, file, detail)


def note(sources: Iterable[Source], level: str, message: str) -> None:
    if _current is not None:
        _current.note(sources, level, message)


def has_errors(sources: Iterable[Source]) -> bool:
    return _current is not None and _current.has_errors(sources)


TITLES = {WRITTEN: "SUCCESS", DRY_RUN: "DRY-RUN", FAILED: "FAILED"}


def result_line(tool: str, outcome: str, errors: int, warnings: int, detail: str) -> str:
    return f"RESULT: {TITLES[outcome]} - {tool}: {errors} error(s), {warnings} warning(s); {detail}"


# -- 结果工作簿 ---------------------------------------------------------------


def _cell_text(text: str) -> str:
    return text if len(text) <= _MAX_CELL else text[:_MAX_CELL] + "\n…"


def _style_header(cell) -> None:
    from openpyxl.styles import Alignment, Font, PatternFill

    cell.font = Font(bold=True, color="FFFFFF")
    cell.fill = PatternFill("solid", fgColor="7B3F00")
    cell.alignment = Alignment(wrap_text=True, vertical="center")


def append_result_columns(
    sheet, run_report: RunReport, book: str, source_sheet: str, outcome: str, rows: Iterable[int] = (),
) -> None:
    """在工作表数据右侧追加结果列；``rows`` 为有内容但可能没有结果的数据行。"""

    from openpyxl.styles import Alignment

    header_width = max((cell.column for cell in sheet[1] if cell.value not in (None, "")), default=0)
    start = max(header_width, 1) + 1 if header_width else sheet.max_column + 1
    for offset, header in enumerate(RESULT_HEADERS):
        cell = sheet.cell(1, start + offset, header)
        _style_header(cell)
        sheet.column_dimensions[cell.column_letter].width = 28 if offset < 3 else 60
    results = run_report.rows_of(book, source_sheet)
    for row_number in sorted({*results, *rows}):
        if row_number < 2:
            continue
        result = results.get(row_number)
        values = (
            ("未处理", "", "", "") if result is None else
            (result.status(outcome), "\n".join(result.objects), "\n".join(result.files), result.notes())
        )
        for offset, value in enumerate(values):
            cell = sheet.cell(row_number, start + offset, _cell_text(value))
            cell.alignment = Alignment(wrap_text=True, vertical="top")


def add_summary_sheet(workbook, lines: Sequence[tuple[str, str]], run_report: RunReport) -> None:
    from openpyxl.styles import Alignment, Font

    sheet = workbook.create_sheet("运行结果", 0)
    sheet.column_dimensions["A"].width = 22
    sheet.column_dimensions["B"].width = 120
    for key, value in lines:
        sheet.append([key, _cell_text(value)])
    sheet_messages = [
        (describe(source), level, text)
        for source, result in run_report.rows.items() if not source[2]
        for level, text in result.messages
    ]
    if run_report.general or sheet_messages:
        sheet.append([])
        sheet.append(["未关联到数据行的消息", ""])
        for level, text in run_report.general:
            sheet.append([level, _cell_text(text)])
        for place, level, text in sheet_messages:
            sheet.append([level, _cell_text(f"{place}: {text}")])
    for row in sheet.iter_rows():
        row[0].font = Font(bold=True)
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    workbook.active = 0


def add_objects_sheet(workbook, run_report: RunReport, outcome: str) -> None:
    sheet = workbook.create_sheet("ARXML 对象")
    sheet.append(["结果", "AUTOSAR 路径", "目标文件", "来源"])
    for cell in sheet[1]:
        _style_header(cell)
    for item in run_report.objects:
        label = _CREATED_LABELS[outcome] if item.status == CREATED else _LABELS.get(item.status, item.status)
        sheet.append([label, item.path, item.file, location(item.sources)])
    for column, width in zip("ABCD", (26, 80, 60, 40)):
        sheet.column_dimensions[column].width = width
    sheet.freeze_panes = "A2"


def save_workbook(workbook, path: Path) -> Path | None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        workbook.save(path)
    except Exception as error:  # 结果表只是报告，写入失败不影响本次运行结果
        LOGGER.warning("Could not write result workbook %s: %s", path, clean_message(error))
        return None
    finally:
        workbook.close()
    return path


def data_rows(sheet) -> list[int]:
    """工作表中表头以下有内容的行号。"""

    return sorted({
        cell.row for row in sheet.iter_rows(min_row=2) for cell in row
        if cell.value is not None and str(cell.value).strip()
    })


# -- 变更差异 -----------------------------------------------------------------


def _text(content: bytes) -> str:
    text = content.decode("utf-8", errors="replace")
    return text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")


def write_diff(path: Path, changes: Sequence[tuple[str, str, bytes | None, bytes]], header: str = "") -> Path | None:
    """以统一 diff 格式记录 ``(旧标签, 新标签, 旧内容, 新内容)``；旧内容为 ``None`` 时只记录新文件行数。

    差异文件只是报告，写入失败只记录 WARNING。
    """

    import difflib

    parts = [header] if header else []
    for old_label, new_label, old, new in changes:
        new_lines = _text(new).splitlines(keepends=True)
        if old is None:
            parts.append(f"=== New file: {new_label} ({len(new_lines)} lines)\n")
            continue
        diff = list(difflib.unified_diff(_text(old).splitlines(keepends=True), new_lines, old_label, new_label))
        if not diff:
            parts.append(f"=== Unchanged: {new_label}\n")
            continue
        parts.extend(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in diff)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as stream:
            stream.write("".join(parts))
    except OSError as error:
        LOGGER.warning("Could not write change diff %s: %s", path, clean_message(error))
        return None
    return path
