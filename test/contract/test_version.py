"""Git tags and development commits determine package versions."""

import subprocess

from packaging.version import Version
from setuptools_scm import get_version


def test_tag_and_development_versions(tmp_path):
    def git(*arguments):
        subprocess.run(
            ["git", "-C", str(tmp_path), *arguments],
            check=True, capture_output=True, text=True,
        )

    git("init")
    git("-c", "user.name=Version test", "-c", "user.email=test@example.invalid",
        "commit", "--allow-empty", "-m", "Release")
    git("tag", "0.6.0")
    assert get_version(root=tmp_path) == "0.6.0"
    git("-c", "user.name=Version test", "-c", "user.email=test@example.invalid",
        "commit", "--allow-empty", "-m", "Development")
    version = Version(get_version(root=tmp_path))
    assert version.is_devrelease
    assert version > Version("0.6.0")
