"""The package imports (and ``--help`` works) without Git, setuptools-scm or a generated version file."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

from helpers import SRC

PATCH_METADATA = (
    "import importlib.metadata as metadata\n"
    "def version(name):\n"
    "    if {installed!r} is None:\n"
    "        raise metadata.PackageNotFoundError(name)\n"
    "    return {installed!r}\n"
    "metadata.version = version\n"
)
FAILING_SCM = (
    "import sys, types\n"
    "scm = types.ModuleType('setuptools_scm')\n"
    "def get_version(**kwargs):\n"
    "    raise LookupError('setuptools-scm was unable to detect version: git not found')\n"
    "scm.get_version = get_version\n"
    "sys.modules['setuptools_scm'] = scm\n"
)
UNRESOLVABLE_PATHS = (
    "import os, pathlib\n"
    "def fail(*args, **kwargs):\n"
    "    raise OSError(1, 'Incorrect function')\n"
    "pathlib.Path.resolve = fail\n"
    "os.path.realpath = fail\n"
)


def source_tree(tmp_path, *, git: bool, version_file: str | None):
    package = tmp_path / "src" / "table_to_arxml"
    shutil.copytree(SRC / "table_to_arxml", package, ignore=shutil.ignore_patterns("__pycache__", "_version.py"))
    if git:
        (tmp_path / ".git").mkdir()
    if version_file is not None:
        (package / "_version.py").write_text(f"__version__ = {version_file!r}\n", encoding="utf-8")
    return tmp_path / "src"


def imported_version(source, prelude: str) -> str:
    code = (
        f"import sys\nsys.path.insert(0, {str(source)!r})\n{prelude}"
        "import table_to_arxml\n"
        f"assert table_to_arxml.__file__.startswith({str(source)!r}), table_to_arxml.__file__\n"
        "print(table_to_arxml.__version__)\n"
    )
    environment = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    result = subprocess.run(
        [sys.executable, "-c", code], env=environment, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.mark.parametrize("installed, expected", [("1.2.3", "1.2.3"), (None, "0+unknown")])
def test_source_archive_without_git_or_version_file(tmp_path, installed, expected):
    source = source_tree(tmp_path, git=False, version_file=None)
    assert imported_version(source, PATCH_METADATA.format(installed=installed)) == expected


@pytest.mark.parametrize("version_file, expected", [("4.5.6", "4.5.6"), (None, "1.2.3")])
def test_git_checkout_without_git_executable(tmp_path, version_file, expected):
    source = source_tree(tmp_path, git=True, version_file=version_file)
    prelude = FAILING_SCM + PATCH_METADATA.format(installed="1.2.3")
    assert imported_version(source, prelude) == expected


def test_import_does_not_require_resolvable_package_path(tmp_path):
    source = source_tree(tmp_path, git=False, version_file="7.8.9")
    assert imported_version(source, UNRESOLVABLE_PATHS) == "7.8.9"
