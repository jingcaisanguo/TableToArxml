"""Release user guides are version-matched, self-contained and reproducible."""

import runpy
import json
import subprocess
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
        assert {name.split("/")[0] for name in names} == {"README.md", "migration.md", "swc", "system"}
        assert [name for name in names if name.endswith(".xlsx")] == ["swc/SwcTemplate.xlsx"]
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


def test_guide_uses_release_override_and_explicit_source_root(tmp_path, monkeypatch):
    monkeypatch.setenv("SETUPTOOLS_SCM_PRETEND_VERSION_FOR_TABLE_TO_ARXML", "1.2.4")
    wheel(tmp_path, version="1.2.4")
    tool = runpy.run_path(str(ROOT / "tools" / "package_user_docs.py"))
    path = tool["package_user_docs"](tmp_path, ROOT)
    assert path.name == "table_to_arxml-1.2.4-user-guide.zip"
    with ZipFile(path) as archive:
        assert archive.read("README.md") == (ROOT / "docs" / "user" / "README.md").read_bytes()


@pytest.fixture
def publisher(monkeypatch):
    tool = runpy.run_path(str(ROOT / "tools" / "release_assets.py"))
    monkeypatch.setenv("GH_REPO", "example/project")
    return tool["publish"], tool["release_state"]


def release_gh(monkeypatch, function, releases):
    calls = []

    def gh(*arguments):
        calls.append(arguments)
        return json.dumps([releases]) if arguments[0] == "api" else ""

    monkeypatch.setitem(function.__globals__, "gh", gh)
    return calls


def assets(directory):
    names = ["table_to_arxml-1.2.3-py3-none-any.whl", "table_to_arxml-1.2.3-user-guide.zip"]
    for name in names:
        (directory / name).touch()
    return names


def test_publish_creates_draft_then_publishes(tmp_path, monkeypatch, publisher):
    publish, _ = publisher
    names = assets(tmp_path)
    calls = release_gh(monkeypatch, publish, [])
    publish("1.2.3", False, tmp_path)
    assert calls[1] == (
        "release", "create", "1.2.3", *(str(tmp_path / name) for name in names),
        "--verify-tag", "--draft", "--title", "1.2.3", "--generate-notes",
    )
    assert calls[2] == ("release", "edit", "1.2.3", "--draft=false")


@pytest.mark.parametrize("draft", [False, True])
@pytest.mark.parametrize("recover", [False, True])
@pytest.mark.parametrize("existing_count", [0, 1])
def test_existing_release_only_uploads_missing_assets(
    tmp_path, monkeypatch, publisher, draft, recover, existing_count,
):
    publish, _ = publisher
    names = assets(tmp_path)
    calls = release_gh(monkeypatch, publish, [{
        "tag_name": "1.2.3",
        "assets": [{"name": name} for name in names[:existing_count]],
        "draft": draft, "name": "User title", "body": "User notes", "prerelease": True,
    }])
    publish("1.2.3", recover, tmp_path)
    assert calls[1:] == [
        ("release", "upload", "1.2.3", str(tmp_path / name))
        for name in names[existing_count:]
    ]
    assert not any("--clobber" in call for call in calls)


def test_recovery_requires_existing_release(monkeypatch, publisher):
    _, preflight = publisher
    release_gh(monkeypatch, preflight, [])
    with pytest.raises(ValueError, match="requires an existing release"):
        preflight("1.2.3", True)


@pytest.mark.parametrize("recover", [False, True])
@pytest.mark.parametrize("draft", [False, True])
def test_completed_release_is_successful_noop(tmp_path, monkeypatch, publisher, recover, draft):
    publish, _ = publisher
    names = assets(tmp_path)
    calls = release_gh(monkeypatch, publish, [{
        "tag_name": "1.2.3", "assets": [{"name": name} for name in names], "draft": draft,
    }])
    publish("1.2.3", recover, tmp_path)
    assert len(calls) == 1


def test_release_created_after_preflight_is_reused(tmp_path, monkeypatch, publisher):
    publish, preflight = publisher
    names = assets(tmp_path)
    release_gh(monkeypatch, preflight, [])
    assert preflight("1.2.3", False) is None
    calls = release_gh(monkeypatch, publish, [{
        "tag_name": "1.2.3", "assets": [], "draft": False,
    }])
    publish("1.2.3", False, tmp_path)
    assert calls[1:] == [
        ("release", "upload", "1.2.3", str(tmp_path / name)) for name in names
    ]


def test_upload_failure_is_reported(tmp_path, monkeypatch, publisher):
    publish, _ = publisher
    assets(tmp_path)

    def failing_upload(*arguments):
        if arguments[0] == "api":
            return json.dumps([[{"tag_name": "1.2.3", "assets": [], "draft": False}]])
        raise subprocess.CalledProcessError(1, arguments, stderr="Upload failed")

    monkeypatch.setitem(publish.__globals__, "gh", failing_upload)
    with pytest.raises(subprocess.CalledProcessError):
        publish("1.2.3", False, tmp_path)


def test_api_failure_is_not_treated_as_missing_release(tmp_path, monkeypatch, publisher):
    publish, _ = publisher
    assets(tmp_path)

    def failing_gh(*arguments):
        raise subprocess.CalledProcessError(1, arguments, stderr="API unavailable")

    monkeypatch.setitem(publish.__globals__, "gh", failing_gh)
    with pytest.raises(subprocess.CalledProcessError):
        publish("1.2.3", False, tmp_path)


@pytest.mark.parametrize("tag", ["main", "01.2.3", "1.2.3-rc1", "--help"])
def test_preflight_rejects_noncanonical_tags(publisher, tag):
    _, preflight = publisher
    with pytest.raises(ValueError, match="canonical"):
        preflight(tag, False)


def test_publish_rejects_unexpected_local_assets(tmp_path, monkeypatch, publisher):
    publish, _ = publisher
    assets(tmp_path)
    (tmp_path / "extra.whl").touch()
    calls = release_gh(monkeypatch, publish, [])
    with pytest.raises(ValueError, match="exactly"):
        publish("1.2.3", False, tmp_path)
    assert len(calls) == 1
