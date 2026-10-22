"""源文件缓存与写出：保存已修改但尚未写盘的 ARXML 字节，并按文件版本缓存解析结果。"""

from __future__ import annotations

import logging
import tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Literal, Sequence

from ..common.output import write_files_atomically
from ..common.messages import clean_message
from ..common.paths import validate_output_paths
from . import xml_patch
from .arxml_model import ArxmlDocument, DocumentSummary, load_document, load_generation

LOGGER = logging.getLogger(__name__)
DefinitionKind = Literal["component", "interface"]


def load_bytes(source: bytes) -> tuple[ArxmlDocument, list[str]]:
    """把内存中的 ARXML 字节写入临时文件后解析。"""

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".arxml", delete=False) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(source)
        return load_document(temporary_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


class SourceStore:
    """保存已修改但尚未写盘的 ARXML 字节，并缓存解析结果。

    新建接口后端口阶段需要能查到它，因此后续读取和解析都走这里的缓存，
    使 dry-run 也能验证完整链路。armodel 同一时间只持有一个文档，因此只复用最近一次加载、
    之后既未加载其他文件也未被修改的文档；组件和接口摘要按文件版本缓存。
    """

    def __init__(self, source_files: Sequence[Path] = ()) -> None:
        self._source_files: dict[Path, None] = {}
        self._definition_file_cache: dict[str, Path | None] = {}
        self._buffers: dict[Path, bytes] = {}
        self._file_revisions: dict[Path, int] = defaultdict(int)
        self._short_name_index: dict[str, list[Path]] | None = None
        self._short_name_index_key: tuple[Path, ...] | None = None
        self._loaded_document_cache: tuple[Path, int, int, ArxmlDocument] | None = None
        self._summaries: dict[tuple[Path, int], DocumentSummary] = {}
        self._parsed: set[tuple[Path, int]] = set()
        self.definitions: dict[tuple[tuple[Path, ...], DefinitionKind, str], tuple[Path, str] | None] = {}
        for path in source_files:
            self._register_file(path)

    @property
    def source_files(self) -> tuple[Path, ...]:
        return tuple(self._source_files)

    def read(self, path: Path) -> bytes:
        self._register_file(path)
        return self._buffers[path] if path in self._buffers else path.read_bytes()

    def write(self, path: Path, document: bytes) -> None:
        self._register_file(path)
        self._buffers[path] = document
        self._file_revisions[path] += 1
        self._short_name_index = None
        self._short_name_index_key = None
        self.definitions.clear()
        self._definition_file_cache.clear()

    def modified(self) -> dict[Path, bytes]:
        return dict(self._buffers)

    def short_name_index(self, arxml_files: Sequence[Path]) -> dict[str, list[Path]]:
        """返回 ``SHORT-NAME`` -> 所在文件列表的反向索引，按需构建并在任何写入后失效。"""

        key = tuple(arxml_files)
        if self._short_name_index is not None and self._short_name_index_key == key:
            return self._short_name_index
        index: dict[str, list[Path]] = defaultdict(list)
        for arxml_file in arxml_files:
            try:
                text = self.read(arxml_file).decode("utf-8-sig")
            except UnicodeDecodeError as error:
                LOGGER.error("Cannot read UTF-8 ARXML %s: %s", arxml_file, clean_message(error))
                continue
            try:
                root = ET.fromstring(text)
            except ET.ParseError as error:
                LOGGER.error("Cannot index ARXML %s; parser failures: %s", arxml_file, clean_message(error))
                continue
            names = {
                (element.text or "").strip() for element in root.iter()
                if element.tag.rsplit("}", 1)[-1] == "SHORT-NAME"
            }
            for element_short_name in names:
                index[element_short_name].append(arxml_file)
        self._short_name_index = dict(index)
        self._short_name_index_key = key
        return self._short_name_index

    def _candidate_files(self, element_short_name: str, ignore_case: bool = False) -> list[Path]:
        index = self.short_name_index(self.source_files)
        if not ignore_case:
            return list(index.get(element_short_name, ()))
        folded = element_short_name.casefold()
        files = [path for name, paths in index.items() if name.casefold() == folded for path in paths]
        return [path for path in self.source_files if path in set(files)]

    def _indexable_source(self, path: Path) -> bytes | None:
        source = self.read(path)
        try:
            xml_patch.element_tag(source, "")
        except ValueError:
            return None
        return source

    def element_tag(self, element_path: str) -> tuple[Path, str] | None:
        """按文件顺序返回首个定义完整路径的文件及其元素标签（元类）；含本次运行已插入的对象。"""

        for candidate in self._candidate_files(element_path.rsplit("/", 1)[-1]):
            source = self._indexable_source(candidate)
            tag = None if source is None else xml_patch.element_tag(source, element_path)
            if tag is not None:
                return candidate, tag
        return None

    def case_variants(self, element_path: str) -> list[tuple[Path, str]]:
        """返回全部文件中与 ``element_path`` 仅大小写不同的已有路径。"""

        variants: list[tuple[Path, str]] = []
        for candidate in self._candidate_files(element_path.rsplit("/", 1)[-1], ignore_case=True):
            source = self._indexable_source(candidate)
            if source is not None:
                variants.extend((candidate, other) for other in xml_patch.case_variants(source, element_path))
        return variants

    def encoding_problem(self, path: Path) -> str | None:
        """要插入片段的文件声明了非 UTF-8 编码时返回问题描述。"""

        problem = xml_patch.encoding_problem(self.read(path))
        return None if problem is None else f"{path}: {problem}"

    def find_first_definition_file(self, element_path: str) -> Path | None:
        """返回清单及目标文件顺序中首次定义完整路径的文件。"""

        if element_path in self._definition_file_cache:
            return self._definition_file_cache[element_path]
        element_short_name = element_path.rsplit("/", 1)[-1]
        matches: list[Path] = []
        for candidate in self.short_name_index(self.source_files).get(element_short_name, ()):
            try:
                if self.load(candidate).element_exists(element_path):
                    matches.append(candidate)
            except (OSError, ValueError, RuntimeError, SyntaxError) as error:
                LOGGER.error(
                    "Cannot check definition %s in %s: %s",
                    element_path,
                    candidate,
                    clean_message(error),
                )
                raise ValueError(f"Cannot determine first definition of {element_path}") from error
        if len(matches) > 1:
            LOGGER.warning(
                "Multiple definitions found for %s: %s; using %s. "
                "The AUTOSAR importer should validate duplicate definitions",
                element_path,
                ", ".join(str(path) for path in matches),
                matches[0],
            )
        first_match = matches[0] if matches else None
        self._definition_file_cache[element_path] = first_match
        return first_match

    def _register_file(self, path: Path) -> None:
        if path not in self._source_files:
            self._source_files[path] = None
            self._short_name_index = None
            self._short_name_index_key = None
            self._definition_file_cache.clear()
            self.definitions.clear()

    def load(self, path: Path) -> ArxmlDocument:
        """按缓存内容加载文档；原始文件的解析诊断只在首次解析时记录一次。"""

        version = self._file_revisions[path]
        if self._loaded_document_cache is not None:
            loaded_path, loaded_version, generation, document = self._loaded_document_cache
            if (loaded_path, loaded_version, generation) == (path, version, load_generation()) and not document.dirty:
                return document

        key = (path, version)
        first = key not in self._parsed
        self._parsed.add(key)
        pending = " (with pending changes)" if path in self._buffers else ""
        LOGGER.log(logging.INFO if first else logging.DEBUG, "Parsing ARXML%s: %s", pending, path)
        if path in self._buffers:
            document, diagnostics = load_bytes(self._buffers[path])
        else:
            document, diagnostics = load_document(path)
            if first:
                for diagnostic in diagnostics:
                    LOGGER.warning("armodel: %s: %s", path, diagnostic)
        self._loaded_document_cache = (path, version, load_generation(), document)
        return document

    def summary(self, path: Path) -> DocumentSummary:
        """返回文件中全部组件和端口接口的摘要，每个文件版本只解析一次。"""

        key = (path, self._file_revisions[path])
        if key not in self._summaries:
            self._summaries[key] = self.load(path).summarize()
        return self._summaries[key]


def output_path(source_file: Path, output_dir: Path, project_root: Path) -> Path:
    """把工程内的源文件映射到输出目录下的相同相对路径。"""

    try:
        relative_path = source_file.resolve().relative_to(project_root.resolve())
    except ValueError as error:
        raise ValueError(f"Target ARXML is outside projectRoot: {source_file}") from error
    return output_dir / relative_path


def write_changes(
    modified: dict[Path, bytes],
    in_place: bool,
    output_dir: Path | None,
    project_root: Path,
    source_files: Sequence[Path] = (),
) -> list[Path]:
    """原子写出字节发生变化的文件并返回写出的路径；无实际修改时不触碰任何文件。"""

    if not in_place and output_dir is None:
        raise ValueError("Output directory is required when not writing in place")
    contents = [
        (source_file if in_place else output_path(source_file, output_dir, project_root), document)
        for source_file, document in modified.items()
        if document != source_file.read_bytes()
    ]
    if not in_place:
        validate_output_paths(
            (destination for destination, _document in contents),
            {*modified, *source_files},
        )
    write_files_atomically(contents)
    for destination, _document in contents:
        LOGGER.info("ARXML written to: %s", destination)
    return [destination for destination, _document in contents]
