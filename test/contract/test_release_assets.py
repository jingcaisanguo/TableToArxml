"""Release user guides are version-matched, self-contained and reproducible."""

import runpy
from zipfile import ZipFile

import pytest

from helpers import ROOT
from table_to_arxml import __version__


def package(wheel_dir):
    tool = runpy.run_path(str(ROOT / "tools" / "package_user_docs.py"))
    return tool["package_user_docs"](wheel_dir)


def wheel(directory, version=__version__, extra=None):
    path = directory / f"table_to_arxml-{version}-py3-none-any.whl"
    with ZipFile(path, "w") as archive:
        archive.writestr(
            f"table_to_arxml-{version}.dist-info/METADATA",
            f"Name: table-to-arxml\nVersion: {version}\n",
        )
        archive.writestr("table_to_arxml/__init__.py", f'__version__ = "{version}"\n')
        if extra:
            archive.writestr(extra, "unexpected")
    return path


def test_guide_shape_and_reproducibility(tmp_path):
    wheel(tmp_path)
    path = package(tmp_path)
    before = path.read_bytes()
    assert path.name == f"table_to_arxml-{__version__}-user-guide.zip"
    with ZipFile(path) as archive:
        names = set(archive.namelist())
        assert {"README.md", "migration.md", "swc/SwcTemplate.xlsx", "system/README.md"} <= names
        assert not any(name.startswith(("dev/", "test/", "third_party/")) for name in names)
        assert archive.read("swc/SwcTemplate.xlsx") == archive.read("tableToSwcArxml/SwcTemplate.xlsx")
    assert package(tmp_path).read_bytes() == before


def test_guide_rejects_mismatched_wheel_version(tmp_path):
    wheel(tmp_path, version="0.0.0")
    with pytest.raises(ValueError, match="does not match"):
        package(tmp_path)


def test_guide_rejects_unexpected_wheel_contents(tmp_path):
    wheel(tmp_path, extra="third_party/script.py")
    with pytest.raises(ValueError, match="outside the package"):
        package(tmp_path)


def test_guide_requires_exactly_one_wheel(tmp_path):
    with pytest.raises(ValueError, match="exactly one wheel"):
        package(tmp_path)
