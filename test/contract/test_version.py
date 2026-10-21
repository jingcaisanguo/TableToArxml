"""Git tags and development commits determine package versions."""

import subprocess
import os
import shutil
import sys
import tarfile
from zipfile import ZipFile

import pytest
from packaging.version import Version
from setuptools_scm import get_version

from helpers import ROOT


@pytest.fixture
def git(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith("SETUPTOOLS_SCM_PRETEND_"):
            monkeypatch.delenv(key)

    def git(*arguments):
        subprocess.run(
            ["git", "-C", str(tmp_path), *arguments],
            check=True, capture_output=True, text=True,
        )

    git("init")
    return git


def test_tag_and_development_versions(tmp_path, git):
    git("-c", "user.name=Version test", "-c", "user.email=test@example.invalid",
        "commit", "--allow-empty", "-m", "Release")
    git("tag", "0.6.0")
    assert get_version(root=tmp_path) == "0.6.0"
    git("-c", "user.name=Version test", "-c", "user.email=test@example.invalid",
        "commit", "--allow-empty", "-m", "Development")
    version = Version(get_version(root=tmp_path))
    assert version.is_devrelease
    assert version > Version("0.6.0")
    (tmp_path / "untracked.txt").write_text("original", encoding="utf-8")
    git("add", "untracked.txt")
    (tmp_path / "untracked.txt").write_text("dirty", encoding="utf-8")
    assert get_version(root=tmp_path) != str(version)


def test_release_version_override_with_multiple_tags(tmp_path, git, monkeypatch):
    git("-c", "user.name=Version test", "-c", "user.email=test@example.invalid",
        "commit", "--allow-empty", "-m", "Release")
    git("tag", "1.2.3")
    git("tag", "1.2.4")
    monkeypatch.setenv("SETUPTOOLS_SCM_PRETEND_VERSION_FOR_TABLE_TO_ARXML", "1.2.4")
    assert get_version(root=tmp_path, dist_name="table-to-arxml") == "1.2.4"


def test_sdist_wheel_and_installed_versions_without_git(tmp_path, git, monkeypatch):
    for name in ("pyproject.toml", "requirements.txt", "README.md"):
        shutil.copy2(ROOT / name, tmp_path / name)
    shutil.copytree(ROOT / "src" / "table_to_arxml", tmp_path / "src" / "table_to_arxml",
                    ignore=shutil.ignore_patterns("__pycache__", "_version.py"))
    git("add", ".")
    git("-c", "user.name=Version test", "-c", "user.email=test@example.invalid",
        "commit", "-m", "Release")
    git("tag", "1.2.3")
    git("tag", "1.2.4")
    monkeypatch.setenv("SETUPTOOLS_SCM_PRETEND_VERSION_FOR_TABLE_TO_ARXML", "1.2.4")

    def run(*arguments, cwd=tmp_path):
        result = subprocess.run(
            [sys.executable, *map(str, arguments)], cwd=cwd, capture_output=True, text=True,
        )
        assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

    run("-m", "build", "--no-isolation")
    dist = tmp_path / "dist"
    wheel = dist / "table_to_arxml-1.2.4-py3-none-any.whl"
    assert wheel.is_file()
    with ZipFile(wheel) as archive:
        assert "Version: 1.2.4" in archive.read("table_to_arxml-1.2.4.dist-info/METADATA").decode().splitlines()
    with tarfile.open(dist / "table_to_arxml-1.2.4.tar.gz") as archive:
        # Only extract an archive produced from this test's own source tree.
        archive.extractall(tmp_path / "unpacked")
    monkeypatch.delenv("SETUPTOOLS_SCM_PRETEND_VERSION_FOR_TABLE_TO_ARXML")
    unpacked = tmp_path / "unpacked" / "table_to_arxml-1.2.4"
    run("-m", "build", "--wheel", "--no-isolation", cwd=unpacked)
    rebuilt = unpacked / "dist" / wheel.name
    assert rebuilt.is_file()
    install = tmp_path / "installed"
    run("-m", "pip", "install", "--no-deps", "--no-compile", "--target", install, rebuilt)
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(install)
    result = subprocess.run(
        [sys.executable, "-S", "-c",
         "import sys; from table_to_arxml import __version__; "
         "assert 'setuptools_scm' not in sys.modules; print(__version__)"],
        cwd=install, env=environment, check=True, capture_output=True, text=True,
    )
    assert result.stdout.strip() == "1.2.4"
