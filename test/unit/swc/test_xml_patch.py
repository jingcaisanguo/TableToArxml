"""按 AUTOSAR schema 顺序新建子容器。"""

from __future__ import annotations

import re

import pytest

from table_to_arxml.swc.xml_patch import CHILD_ORDER, insert_or_create_child_container

DOCUMENT = """<AUTOSAR>
  <AR-PACKAGES>
    <AR-PACKAGE>
      <SHORT-NAME>P</SHORT-NAME>
{package}
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


def child_tags(source: bytes, parent: str) -> list[str]:
    text = source.decode()
    body = text.split(f"<{parent}>", 1)[1].split(f"</{parent}>", 1)[0]
    indent = re.search(r"\n( +)<SHORT-NAME>", body).group(1)
    return re.findall(rf"^{indent}<([A-Z-]+)[ >]", body, flags=re.M)


def create(behavior: str, container: str, path: str = "/P/C/IB", package: str = "") -> list[str]:
    source = DOCUMENT.format(behavior=behavior, package=package).encode()
    updated = insert_or_create_child_container(source, path, container, ["<X/>"])
    parent = "SWC-INTERNAL-BEHAVIOR" if path == "/P/C/IB" else "RUNNABLE-ENTITY"
    return child_tags(updated, parent)


def test_runnables_go_before_trailing_supports_multiple_instantiation():
    behavior = """              <EVENTS></EVENTS>
              <HANDLE-TERMINATION-AND-RESTART>NO-SUPPORT</HANDLE-TERMINATION-AND-RESTART>
              <SUPPORTS-MULTIPLE-INSTANTIATION>false</SUPPORTS-MULTIPLE-INSTANTIATION>"""
    assert create(behavior, "RUNNABLES") == [
        "SHORT-NAME", "EVENTS", "HANDLE-TERMINATION-AND-RESTART", "RUNNABLES", "SUPPORTS-MULTIPLE-INSTANTIATION"]


def test_runnables_go_before_service_dependencies():
    behavior = "              <SERVICE-DEPENDENCYS></SERVICE-DEPENDENCYS>"
    assert create(behavior, "RUNNABLES") == ["SHORT-NAME", "RUNNABLES", "SERVICE-DEPENDENCYS"]


@pytest.mark.parametrize("behavior, expected", [
    ("              <EXCLUSIVE-AREAS></EXCLUSIVE-AREAS>\n              <RUNNABLES></RUNNABLES>",
     ["SHORT-NAME", "EXCLUSIVE-AREAS", "EVENTS", "RUNNABLES"]),
    ("              <HANDLE-TERMINATION-AND-RESTART>NO-SUPPORT</HANDLE-TERMINATION-AND-RESTART>\n"
     "              <RUNNABLES></RUNNABLES>",
     ["SHORT-NAME", "EVENTS", "HANDLE-TERMINATION-AND-RESTART", "RUNNABLES"]),
])
def test_events_follow_schema_order(behavior, expected):
    assert create(behavior, "EVENTS") == expected


def test_server_call_points_go_before_symbol():
    behavior = """              <RUNNABLES>
                <RUNNABLE-ENTITY>
                  <SHORT-NAME>Run</SHORT-NAME>
                  <CAN-BE-INVOKED-CONCURRENTLY>false</CAN-BE-INVOKED-CONCURRENTLY>
                  <DATA-SEND-POINTS></DATA-SEND-POINTS>
                  <SYMBOL>Run</SYMBOL>
                </RUNNABLE-ENTITY>
              </RUNNABLES>"""
    assert create(behavior, "SERVER-CALL-POINTS", "/P/C/IB/Run") == [
        "SHORT-NAME", "CAN-BE-INVOKED-CONCURRENTLY", "DATA-SEND-POINTS", "SERVER-CALL-POINTS", "SYMBOL"]


def test_existing_container_is_reused():
    behavior = "              <RUNNABLES>\n              </RUNNABLES>"
    source = DOCUMENT.format(behavior=behavior, package="").encode()
    updated = insert_or_create_child_container(source, "/P/C/IB", "RUNNABLES", ["<X/>"]).decode()
    assert updated.count("<RUNNABLES>") == 1 and "<X/>" in updated


def test_unknown_parent_or_container_is_rejected():
    source = DOCUMENT.format(behavior="", package="").encode()
    with pytest.raises(ValueError, match="unknown schema order"):
        insert_or_create_child_container(source, "/P/C", "PORTS", ["<X/>"])
    with pytest.raises(ValueError, match="unknown schema order"):
        insert_or_create_child_container(source, "/P/C/IB", "NOT-A-CONTAINER", ["<X/>"])


def test_schema_order_tables_have_no_duplicates():
    for parent, order in CHILD_ORDER.items():
        assert len(order) == len(set(order)), parent
