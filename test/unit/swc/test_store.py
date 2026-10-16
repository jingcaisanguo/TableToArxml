"""Output paths must never implicitly overwrite source files."""

import os

import pytest

from table_to_arxml.swc.store import write_changes


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
