"""Output paths must never implicitly overwrite source files."""

import os

import pytest

from table_to_arxml.swc.resolve import find_definition
from table_to_arxml.swc.store import SourceStore, write_changes
from swc_fixtures import TWINS


def test_output_root_cannot_be_the_project_root(tmp_path):
    source = tmp_path / "source.arxml"
    source.write_bytes(b"original")
    with pytest.raises(ValueError, match="overlaps an input file"):
        write_changes({source: b"changed"}, False, tmp_path, tmp_path)
    assert source.read_bytes() == b"original"
    write_changes({source: b"changed"}, True, None, tmp_path)
    assert source.read_bytes() == b"changed"


def test_output_hard_link_to_any_input_is_rejected(tmp_path):
    source = tmp_path / "source.arxml"
    protected = tmp_path / "protected.arxml"
    source.write_bytes(b"original")
    protected.write_bytes(b"protected")
    out = tmp_path / "out"
    out.mkdir()
    os.link(protected, out / source.name)
    with pytest.raises(ValueError, match="overlaps an input file"):
        write_changes({source: b"changed"}, False, out, tmp_path, [source, protected])
    assert protected.read_bytes() == b"protected"
    assert (out / source.name).read_bytes() == b"protected"


def test_output_symlink_to_source_is_rejected(tmp_path):
    source = tmp_path / "source.arxml"
    source.write_bytes(b"original")
    out = tmp_path / "out"
    out.mkdir()
    try:
        (out / source.name).symlink_to(source)
    except OSError as error:
        pytest.skip(f"Symlink creation unavailable: {error}")
    with pytest.raises(ValueError, match="overlaps an input file"):
        write_changes({source: b"changed"}, False, out, tmp_path)
    assert source.read_bytes() == b"original"


@pytest.mark.parametrize("short_name", [
    "<SHORT-NAME >If</SHORT-NAME >",
    "<SHORT-NAME\n>If</SHORT-NAME\n>",
    "<SHORT-NAME><![CDATA[If]]></SHORT-NAME>",
    "<SHORT-NAME>&#73;f</SHORT-NAME>",
])
def test_xml_short_name_index_preserves_first_definition(tmp_path, short_name):
    first, second = tmp_path / "first.arxml", tmp_path / "second.arxml"
    first.write_text(TWINS.replace("<SHORT-NAME>If</SHORT-NAME>", short_name), encoding="utf-8")
    second.write_text(TWINS, encoding="utf-8")
    store = SourceStore([first, second])
    assert store.short_name_index([first, second])["If"] == [first, second]
    assert store.find_first_definition_file("/Pkg/If") == first
    assert find_definition([first, second], "/Pkg/If", "interface", store)[0] == first


def test_definition_cache_includes_file_order_and_scope(tmp_path):
    first, second = tmp_path / "first.arxml", tmp_path / "second.arxml"
    for path in (first, second):
        path.write_text(TWINS, encoding="utf-8")
    store = SourceStore([first, second, first])
    assert store.source_files == (first, second)
    assert find_definition([first, second], "/Pkg/B", "component", store)[0] == first
    assert find_definition([second, first], "/Pkg/B", "component", store)[0] == second
    assert find_definition([second], "/Pkg/B", "component", store)[0] == second
    store.write(first, TWINS.replace("<SHORT-NAME>B</SHORT-NAME>", "<SHORT-NAME>C</SHORT-NAME>").encode())
    assert find_definition([first, second], "/Pkg/B", "component", store)[0] == second


def test_index_does_not_treat_comments_as_definitions(tmp_path):
    source = tmp_path / "source.arxml"
    source.write_text(TWINS.replace("<AR-PACKAGES>", "<!-- <SHORT-NAME>Fake</SHORT-NAME> --><AR-PACKAGES>"),
                      encoding="utf-8")
    assert "Fake" not in SourceStore([source]).short_name_index([source])
