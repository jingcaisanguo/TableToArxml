"""在原始 ARXML 字节上按 AUTOSAR 路径定位元素并插入片段，其余字节保持不变。

元素路径与 armodel 的 ``getFullName`` 一致：沿祖先中带 ``SHORT-NAME`` 的元素逐级拼接，
因此不同组件下同名的 runnable、内部行为或包都能被唯一区分。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Sequence

# 注释、CDATA、处理指令和声明中的 ``<`` 不是元素标签，扫描时整体跳过；属性值可能含 ``>``。
_TOKEN = re.compile(
    rb"<!--.*?-->|<!\[CDATA\[.*?\]\]>|<\?.*?\?>|<![^>]*>"
    rb"|<(/?)([A-Za-z_][\w.:-]*)((?:\s+[^\s=/>]+\s*=\s*(?:\"[^\"]*\"|'[^']*'))*)\s*(/?)>",
    re.DOTALL,
)


@dataclass(frozen=True)
class ElementSpan:
    """元素在字节中的位置；自闭合元素的 ``content_start`` 与 ``end_tag_start`` 都等于 ``end``。"""

    tag: str
    start: int
    content_start: int
    end_tag_start: int
    end: int
    self_closing: bool = False


@dataclass
class _OpenElement:
    tag: str
    start: int
    content_start: int
    parent_path: str
    path: str | None = None


@lru_cache(maxsize=8)
def _path_index(source: bytes) -> dict[str, list[ElementSpan]]:
    """返回 ``AUTOSAR 路径 -> 元素位置列表``；只扫描一次并按内容缓存。"""

    index: dict[str, list[ElementSpan]] = {}
    stack: list[_OpenElement] = []
    for match in _TOKEN.finditer(source):
        tag_bytes = match.group(2)
        if tag_bytes is None:
            continue
        tag = tag_bytes.decode("ascii")
        if match.group(1):
            if not stack or stack[-1].tag != tag:
                expected = stack[-1].tag if stack else "nothing"
                raise ValueError(f"Malformed ARXML: found </{tag}> while expecting </{expected}>")
            element = stack.pop()
            if element.path is not None:
                index.setdefault(element.path, []).append(
                    ElementSpan(tag, element.start, element.content_start, match.start(), match.end())
                )
            continue
        if match.group(4):
            continue
        parent_path = ""
        if stack:
            parent = stack[-1]
            parent_path = parent.path if parent.path is not None else parent.parent_path
        stack.append(_OpenElement(tag, match.start(), match.end(), parent_path))
        if tag == "SHORT-NAME" and len(stack) > 1:
            closing = source.find(b"</SHORT-NAME>", match.end())
            owner = stack[-2]
            if closing >= 0 and owner.path is None:
                name = source[match.end():closing].strip().decode("utf-8")
                owner.path = f"{owner.parent_path}/{name}"
    if stack:
        raise ValueError(f"Malformed ARXML: <{stack[-1].tag}> is not closed")
    return index


def element_span(source: bytes, path: str) -> ElementSpan:
    """返回路径对应的唯一元素；不存在或重复时报错。"""

    spans = _path_index(source).get(path, [])
    if len(spans) != 1:
        raise ValueError(f"Expected one element at {path}, found {len(spans)}")
    return spans[0]


def child_spans(source: bytes, parent: ElementSpan) -> list[ElementSpan]:
    """按出现顺序返回元素的直接子元素。"""

    children: list[ElementSpan] = []
    depth = 0
    opened: tuple[str, int, int] | None = None
    for match in _TOKEN.finditer(source, parent.content_start, parent.end_tag_start):
        tag_bytes = match.group(2)
        if tag_bytes is None:
            continue
        tag = tag_bytes.decode("ascii")
        if match.group(1):
            depth -= 1
            if depth == 0 and opened is not None:
                children.append(ElementSpan(tag, opened[1], opened[2], match.start(), match.end()))
        elif match.group(4):
            if depth == 0:
                children.append(
                    ElementSpan(tag, match.start(), match.end(), match.end(), match.end(), True)
                )
        else:
            if depth == 0:
                opened = (tag, match.start(), match.end())
            depth += 1
    return children


def child_container(source: bytes, parent: ElementSpan, tag: str) -> ElementSpan | None:
    """返回指定标签的直接子容器；自闭合的空容器无法追加内容，视为错误。"""

    container = next((child for child in child_spans(source, parent) if child.tag == tag), None)
    if container is not None and container.self_closing:
        raise ValueError(f"Self-closing <{tag}/> is not supported; write it as <{tag}></{tag}>")
    return container


def insert_at_offset(
    source: bytes,
    offset: int,
    fragments: Sequence[str],
    child_indent: bool = True,
) -> bytes:
    """在偏移处按原换行和缩进插入片段。

    ``offset`` 通常是父容器的结束标签，片段比该行多缩进两格；``child_indent`` 为假时
    ``offset`` 是兄弟元素的开始标签，片段与它同级缩进。``<TAG></TAG>`` 写在同一行的空容器
    会把结束标签挪到新的一行。片段按 UTF-8 编码写入，因此标签、端口名等允许使用中文。
    """

    line_start = source.rfind(b"\n", 0, offset) + 1
    prefix = source[line_start:offset]
    newline = b"\r\n" if b"\r\n" in source else b"\n"

    if not prefix.strip(b" \t"):
        # 偏移处的标签独占一行：插到该行行首，沿用其缩进。
        base_indentation = prefix
        insert_offset = line_start
        before = after = b""
    else:
        # 同一行前面还有内容（如 ``<TAG></TAG>``）：先换行，插入后补回该标签所在行的缩进。
        stripped = prefix.lstrip(b" \t")
        base_indentation = prefix[: len(prefix) - len(stripped)]
        insert_offset = offset
        before, after = newline, base_indentation

    indentation = base_indentation + b"  " if child_indent else base_indentation
    encoded_fragments = [
        newline.join(indentation + line.encode("utf-8") for line in fragment.splitlines())
        for fragment in fragments
    ]
    insertion = newline.join(encoded_fragments) + newline
    return source[:insert_offset] + before + insertion + after + source[insert_offset:]


def container_problem(source: bytes, element_path: str, container_tag: str) -> str | None:
    """检查元素及其直接子容器均可定位；返回问题描述，没有问题时返回 ``None``。"""

    try:
        if child_container(source, element_span(source, element_path), container_tag) is None:
            return f"{container_tag} container not found in {element_path}"
    except ValueError as error:
        return str(error)
    return None


def container_end(source: bytes, element_path: str, container_tag: str) -> int:
    """返回元素直接子容器结束标签的偏移；容器不存在时报错。"""

    container = child_container(source, element_span(source, element_path), container_tag)
    if container is None:
        raise ValueError(f"{container_tag} container not found in {element_path}")
    return container.end_tag_start


def insert_many(source: bytes, insertions: Sequence[tuple[int, Sequence[str]]]) -> bytes:
    """一次应用多处插入；偏移取自同一份原始字节，从后往前插入可保持前面的偏移有效。"""

    for offset, fragments in sorted(insertions, key=lambda item: item[0], reverse=True):
        source = insert_at_offset(source, offset, fragments)
    return source


def insert_or_create_child_container(
    source: bytes,
    element_path: str,
    container_tag: str,
    fragments: Sequence[str],
    following_tags: Sequence[str] = (),
) -> bytes:
    """向元素的直接子容器追加片段；容器缺失时在第一个后续兄弟元素前（或元素末尾）创建。"""

    if not fragments:
        return source
    element = element_span(source, element_path)
    container = child_container(source, element, container_tag)
    if container is not None:
        return insert_at_offset(source, container.end_tag_start, fragments)

    wrapped = "\n".join(
        [f"<{container_tag}>"]
        + [f"  {line}" for fragment in fragments for line in fragment.splitlines()]
        + [f"</{container_tag}>"]
    )
    following = next(
        (child for child in child_spans(source, element) if child.tag in following_tags), None
    )
    if following is not None:
        return insert_at_offset(source, following.start, [wrapped], child_indent=False)
    return insert_at_offset(source, element.end_tag_start, [wrapped])
