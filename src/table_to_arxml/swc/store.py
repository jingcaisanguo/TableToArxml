"""源文件缓存与写出：保存已修改但尚未写盘的 ARXML 字节，并按文件版本缓存解析结果。"""

from __future__ import annotations

import codecs
import hashlib
import logging
import tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Literal, Mapping, Sequence

from ..common.output import write_files_atomically
from ..common.messages import clean_message
from ..common.paths import path_key, validate_output_paths
from . import xml_patch
from .arxml_model import ArxmlDocument, DocumentSummary, load_document, load_generation

LOGGER = logging.getLogger(__name__)
DefinitionKind = Literal["component", "interface"]
OVERLAP_HINT = "use --in-place explicitly to overwrite source ARXML"


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


def _digest(content: bytes) -> bytes:
    return hashlib.sha256(content).digest()


class PendingChanges(dict):
    """待写出的 ``源文件 -> 新字节``，只含内容确实变化的文件。

    ``loaded`` 为各源文件首次读取时内容的摘要；写出前据此发现运行期间被外部修改的源文件，避免覆盖外部修改。
    """

    def __init__(self, changes: Mapping[Path, bytes], loaded: Mapping[Path, bytes]) -> None:
        super().__init__(changes)
        self.loaded = dict(loaded)


class SourceStore:
    """保存已修改但尚未写盘的 ARXML 字节，并缓存解析结果。

    新建接口后端口阶段需要能查到它，因此后续读取和解析都走这里的缓存，
    使 dry-run 也能验证完整链路。armodel 同一时间只持有一个文档，因此只复用最近一次加载、
    之后既未加载其他文件也未被修改的文档；组件和接口摘要按文件版本缓存。
    同一文件经清单和 ``Target ARXML`` 以不同写法（链接、junction、``..``、大小写）出现时按 :func:`path_key`
    视为同一文件，统一使用首次登记的路径。
    """

    def __init__(self, source_files: Sequence[Path] = ()) -> None:
        self._source_files: dict[Path, None] = {}
        self._canonical_paths: dict[str, Path] = {}
        self._aliases: dict[Path, Path] = {}
        self._definition_file_cache: dict[str, Path | None] = {}
        self._buffers: dict[Path, bytes] = {}
        self._loaded_digests: dict[Path, bytes] = {}
        self._file_revisions: dict[Path, int] = defaultdict(int)
        self._short_name_index: dict[str, list[Path]] | None = None
        self._short_name_index_key: tuple[Path, ...] | None = None
        self._file_short_names: dict[tuple[Path, int], frozenset[str]] = {}
        self._loaded_document_cache: tuple[Path, int, int, ArxmlDocument] | None = None
        self._summaries: dict[tuple[Path, int], DocumentSummary] = {}
        self._parsed: set[tuple[Path, int]] = set()
        self.definitions: dict[tuple[tuple[Path, ...], DefinitionKind, str], tuple[Path, str] | None] = {}
        for path in source_files:
            self._register_file(path)

    @property
    def source_files(self) -> tuple[Path, ...]:
        return tuple(self._source_files)

    def canonical(self, path: Path) -> Path:
        """返回同一文件首次登记时的路径。"""

        canonical = self._aliases.get(path)
        if canonical is None:
            canonical = self._canonical_paths.setdefault(path_key(path), path)
            self._aliases[path] = canonical
        return canonical

    def read(self, path: Path) -> bytes:
        path = self._register_file(path)
        if path in self._buffers:
            return self._buffers[path]
        content = path.read_bytes()
        self._loaded_digests.setdefault(path, _digest(content))
        return content

    def write(self, path: Path, document: bytes, parsed: ArxmlDocument | None = None) -> None:
        """更新文件的待写出字节；``parsed`` 为刚由这些字节解析出的文档时一并缓存，下一步无需重新解析。"""

        path = self._register_file(path)
        if path not in self._buffers and path not in self._loaded_digests and path.is_file():
            self.read(path)
        self._buffers[path] = document
        self._file_revisions[path] += 1
        self._short_name_index = None
        self._short_name_index_key = None
        self.definitions.clear()
        self._definition_file_cache.clear()
        if parsed is not None and not parsed.dirty:
            self._parsed.add((path, self._file_revisions[path]))
            self._loaded_document_cache = (path, self._file_revisions[path], parsed.generation, parsed)

    def modified(self) -> PendingChanges:
        """返回内容与加载时不同的文件；插入后又恢复原样或从未插入内容的文件不计入。"""

        changed = {
            path: document for path, document in self._buffers.items()
            if _digest(document) != self._loaded_digests.get(path)
        }
        return PendingChanges(
            changed, {path: self._loaded_digests[path] for path in changed if path in self._loaded_digests}
        )

    def short_name_index(self, arxml_files: Sequence[Path]) -> dict[str, list[Path]]:
        """返回 ``SHORT-NAME`` -> 所在文件列表的反向索引，按需构建并在任何写入后失效。

        每个文件按版本缓存其 SHORT-NAME 集合，写入后只重新索引被修改的文件。
        """

        key = tuple(arxml_files)
        if self._short_name_index is not None and self._short_name_index_key == key:
            return self._short_name_index
        index: dict[str, list[Path]] = defaultdict(list)
        for arxml_file in arxml_files:
            for element_short_name in self._short_names(arxml_file):
                index[element_short_name].append(arxml_file)
        self._short_name_index = dict(index)
        self._short_name_index_key = key
        return self._short_name_index

    def _short_names(self, arxml_file: Path) -> frozenset[str]:
        """文件当前版本中的全部 SHORT-NAME；无法读取或解析时记录 ERROR 并返回空集合。"""

        key = (self.canonical(arxml_file), self._file_revisions[self.canonical(arxml_file)])
        if key in self._file_short_names:
            return self._file_short_names[key]
        names: frozenset[str] = frozenset()
        source = self.read(arxml_file)
        encoding = xml_patch.source_encoding(source)
        try:
            text = source.decode("utf-8-sig" if codecs.lookup(encoding).name == "utf-8" else encoding)
        except (LookupError, UnicodeDecodeError) as error:
            LOGGER.error("Cannot read %s ARXML %s: %s", encoding.upper(), arxml_file, clean_message(error))
        else:
            try:
                root = ET.fromstring(text)
            except ET.ParseError as error:
                LOGGER.error("Cannot index ARXML %s; parser failures: %s", arxml_file, clean_message(error))
            else:
                names = frozenset(
                    (element.text or "").strip() for element in root.iter()
                    if element.tag.rsplit("}", 1)[-1] == "SHORT-NAME"
                )
        self._file_short_names[key] = names
        return names

    def _candidate_files(self, element_short_name: str, ignore_case: bool = False) -> list[Path]:
        """按 SHORT-NAME 预筛候选文件；定义查找区分大小写，``ignore_case`` 只供大小写变体提示使用。"""

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
        """返回全部文件中与 ``element_path`` 仅大小写不同的已有路径，仅用于 WARNING 提示。"""

        variants: list[tuple[Path, str]] = []
        for candidate in self._candidate_files(element_path.rsplit("/", 1)[-1], ignore_case=True):
            source = self._indexable_source(candidate)
            if source is not None:
                variants.extend((candidate, other) for other in xml_patch.case_variants(source, element_path))
        return variants

    def modification_problem(self, path: Path) -> str | None:
        """要插入片段的文件不支持按字节修改时返回问题描述（见 :func:`xml_patch.modification_problem`）。"""

        path = self.canonical(path)
        problem = xml_patch.modification_problem(self.read(path))
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

    def _register_file(self, path: Path) -> Path:
        path = self.canonical(path)
        if path not in self._source_files:
            self._source_files[path] = None
            self._short_name_index = None
            self._short_name_index_key = None
            self._definition_file_cache.clear()
            self.definitions.clear()
        return path

    def load(self, path: Path, *, reuse_modified: bool = False) -> ArxmlDocument:
        """按缓存内容加载文档；原始文件的解析诊断只在首次解析时记录一次。

        ``reuse_modified`` 为真时，最近加载的文档即使已通过 ``add_*`` 加入尚未写回字节的对象也直接复用；
        只用于在同一步骤中连续生成片段，查询已有定义时应重新解析。
        """

        path = self.canonical(path)
        version = self._file_revisions[path]
        if self._loaded_document_cache is not None:
            loaded_path, loaded_version, generation, document = self._loaded_document_cache
            if (loaded_path, loaded_version, generation) == (path, version, load_generation()) and (
                reuse_modified or not document.dirty
            ):
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

        key = (self.canonical(path), self._file_revisions[self.canonical(path)])
        if key not in self._summaries:
            self._summaries[key] = self.load(path).summarize()
        return self._summaries[key]


def output_path(source_file: Path, output_dir: Path, project_root: Path) -> Path:
    """把工程内的源文件映射到输出目录下的相同相对路径。

    先按未解析链接的路径计算，链接或 junction 指向工程外的目录时仍保持清单中的布局；
    不在工程内时再按解析链接后的实际路径判断。
    """

    from ..common.paths import absolute_path

    for resolve_links in (False, True):
        try:
            relative_path = absolute_path(source_file, resolve_links=resolve_links).relative_to(
                absolute_path(project_root, resolve_links=resolve_links))
        except ValueError:
            continue
        return output_dir / relative_path
    raise ValueError(f"Target ARXML is outside projectRoot: {source_file}")


def write_changes(
    modified: dict[Path, bytes],
    in_place: bool,
    output_dir: Path | None,
    project_root: Path,
    source_files: Sequence[Path] = (),
) -> list[Path]:
    """原子写出字节发生变化的文件并返回写出的路径；无实际修改时不触碰任何文件。

    ``modified`` 为 :class:`PendingChanges` 时，先确认源文件仍与加载时相同；运行期间被外部修改的文件
    会让整次写出失败（不写任何文件），以免覆盖外部修改或基于旧内容生成输出。
    """

    if not in_place and output_dir is None:
        raise ValueError("Output directory is required when not writing in place")
    loaded = getattr(modified, "loaded", {})
    current = {source_file: source_file.read_bytes() for source_file in modified}
    changed_on_disk = [
        str(source_file) for source_file, content in current.items()
        if source_file in loaded and _digest(content) != loaded[source_file]
    ]
    if changed_on_disk:
        raise ValueError(
            f"ARXML changed on disk after it was loaded: {', '.join(changed_on_disk)}; "
            "rerun the tool on the current files"
        )
    contents = [
        (source_file if in_place else output_path(source_file, output_dir, project_root), document)
        for source_file, document in modified.items()
        if document != current[source_file]
    ]
    if not in_place:
        validate_output_paths(
            (destination for destination, _document in contents),
            {*modified, *source_files},
            overlap_hint=OVERLAP_HINT,
        )
    write_files_atomically(contents)
    for destination, _document in contents:
        LOGGER.info("ARXML written to: %s", destination)
    return [destination for destination, _document in contents]
