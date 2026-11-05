"""原始字节修改的兼容性回归：Unicode 行分隔符、命名空间前缀、批量插入与逐个插入一致。"""

from __future__ import annotations

import re

import pytest

from table_to_arxml.swc import xml_patch
from swc_fixtures import TWINS


def prefixed(text: str) -> str:
    text = text.replace('xmlns="http://autosar.org/schema/r4.0"', 'xmlns:ar="http://autosar.org/schema/r4.0"')
    return re.sub(r"<(/?)([A-Z])", r"<\1ar:\2", text)


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\x85", "\x0b", "\x1e"])
def test_fragment_text_is_not_split_at_unicode_line_separators(separator):
    source = TWINS.encode()
    fragment = f"<COMPU-CONST>\n  <VT>A{separator}B</VT>\n</COMPU-CONST>"
    updated = xml_patch.insert_or_create_child_container(source, "/Pkg", "ELEMENTS", [fragment]).decode()
    assert f"          <VT>A{separator}B</VT>\n" in updated
    assert updated.replace(
        "        <COMPU-CONST>\n" f"          <VT>A{separator}B</VT>\n" "        </COMPU-CONST>\n", "", 1,
    ) == TWINS


def test_crlf_fragments_keep_the_file_newline():
    source = TWINS.replace("\n", "\r\n").encode()
    updated = xml_patch.insert_or_create_child_container(source, "/Pkg", "ELEMENTS", ["<X>\r\n  <Y/>\r\n</X>\n"])
    assert b"        <X>\r\n          <Y/>\r\n        </X>\r\n      </ELEMENTS>" in updated


def test_namespace_prefixed_tags_are_located_by_local_name():
    source = prefixed(TWINS).encode()
    assert xml_patch.element_tag(source, "/Pkg/If") == "SENDER-RECEIVER-INTERFACE"
    assert xml_patch.element_tag(source, "/Pkg/B/IB/Run") == "RUNNABLE-ENTITY"
    assert "Run" in xml_patch.child_names(source, "/Pkg/B/IB")
    assert xml_patch.container_problem(source, "/Pkg/B", "PORTS") is None


def test_namespace_prefixed_root_cannot_be_modified():
    problem = xml_patch.modification_problem(prefixed(TWINS).encode())
    assert problem is not None and "namespace-prefixed ARXML is not supported" in problem
    assert xml_patch.modification_problem(TWINS.encode()) is None


BEHAVIOR = """<AUTOSAR>
  <AR-PACKAGES>
    <AR-PACKAGE>
      <SHORT-NAME>P</SHORT-NAME>
      <ELEMENTS>
        <APPLICATION-SW-COMPONENT-TYPE>
          <SHORT-NAME>C</SHORT-NAME>
          <INTERNAL-BEHAVIORS>
            <SWC-INTERNAL-BEHAVIOR>
              <SHORT-NAME>IB</SHORT-NAME>
{behavior}
            </SWC-INTERNAL-BEHAVIOR>
          </INTERNAL-BEHAVIORS>
        </APPLICATION-SW-COMPONENT-TYPE>
      </ELEMENTS>
    </AR-PACKAGE>
  </AR-PACKAGES>
</AUTOSAR>
"""


@pytest.mark.parametrize("behavior", [
    "",
    "              <SUPPORTS-MULTIPLE-INSTANTIATION>false</SUPPORTS-MULTIPLE-INSTANTIATION>",
    "              <HANDLE-TERMINATION-AND-RESTART>NO-SUPPORT</HANDLE-TERMINATION-AND-RESTART>",
    "              <RUNNABLES>\n              </RUNNABLES>",
])
@pytest.mark.parametrize("order", [("RUNNABLES", "EVENTS"), ("EVENTS", "RUNNABLES")])
def test_batched_insertions_match_sequential_insertions(behavior, order):
    source = BEHAVIOR.format(behavior=behavior).encode()
    sequential = source
    for container in order:
        sequential = xml_patch.insert_or_create_child_container(
            sequential, "/P/C/IB", container, [f"<{container}-ITEM/>"]
        )
    batched = xml_patch.apply_insertions(source, [
        xml_patch.container_insertion(source, "/P/C/IB", container, [f"<{container}-ITEM/>"])
        for container in order
    ])
    assert batched == sequential
